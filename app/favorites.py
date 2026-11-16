"""お気に入り（SQLite）。Docker では /data ボリュームに保存され、コンテナを作り直しても残ります。"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data")) / "favorites.db"
FIELDS = ("provider", "title", "command", "memo", "service", "operation", "doc_url", "state")


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS favorites(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL, title TEXT NOT NULL, command TEXT NOT NULL,
            memo TEXT DEFAULT '', service TEXT DEFAULT '', operation TEXT DEFAULT '',
            doc_url TEXT DEFAULT '', state TEXT DEFAULT '{}',
            use_count INTEGER DEFAULT 0,
            created_at TEXT, updated_at TEXT)""")


def _row(r) -> dict:
    d = dict(r)
    try:
        d["state"] = json.loads(d.get("state") or "{}")
    except ValueError:
        d["state"] = {}
    return d


def _clean(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    # 文字列以外（数値・配列など）が来ても保存できるよう、文字列にそろえる。state はオブジェクトのときだけ保存
    d = {k: "" if data.get(k) is None else str(data.get(k)) for k in FIELDS}
    state = data.get("state")
    d["state"] = json.dumps(state if isinstance(state, dict) else {}, ensure_ascii=False)
    for k in ("provider", "title", "command"):
        if not str(d[k]).strip():
            raise ValueError(f"{k} is required")
    if d["provider"] != "aws":
        raise ValueError("provider must be aws")
    if not str(d.get("doc_url") or "").startswith("https://"):   # javascript: などのリンクを保存しない
        d["doc_url"] = ""
    return d


def list_all(provider: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM favorites", []
    if provider:
        q += " WHERE provider=?"
        args.append(provider)
    q += " ORDER BY use_count DESC, updated_at DESC"
    with _conn() as c:
        return [_row(r) for r in c.execute(q, args)]


def create(data: dict) -> dict:
    d = _clean(data)
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as c:
        cur = c.execute(f"INSERT INTO favorites({','.join(FIELDS)},created_at,updated_at) VALUES({','.join('?' * len(FIELDS))},?,?)",
                        [d[k] for k in FIELDS] + [now, now])
        return _row(c.execute("SELECT * FROM favorites WHERE id=?", (cur.lastrowid,)).fetchone())


def update(fid: int, data: dict) -> dict | None:
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    with _conn() as c:
        cur = c.execute("SELECT * FROM favorites WHERE id=?", (fid,)).fetchone()
        if not cur:
            return None
        merged = _row(cur)
        merged.update({k: v for k, v in data.items() if k in FIELDS})
        d = _clean(merged)
        now = datetime.now().isoformat(timespec="seconds")
        c.execute(f"UPDATE favorites SET {','.join(k + '=?' for k in FIELDS)}, updated_at=? WHERE id=?",
                  [d[k] for k in FIELDS] + [now, fid])
        return _row(c.execute("SELECT * FROM favorites WHERE id=?", (fid,)).fetchone())


def touch(fid: int):
    with _conn() as c:
        c.execute("UPDATE favorites SET use_count=use_count+1 WHERE id=?", (fid,))


def delete(fid: int) -> bool:
    with _conn() as c:
        return c.execute("DELETE FROM favorites WHERE id=?", (fid,)).rowcount > 0


def import_many(items: list[dict]) -> int:
    n = 0
    for it in items:
        try:
            create(it)
            n += 1
        except (ValueError, TypeError):
            continue
    return n
