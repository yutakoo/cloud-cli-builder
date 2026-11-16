"""英語の説明文 → 日本語（辞書で訳せる部分は Claude を使わない）。

説明文を「文」に分けて、文ごとに辞書を引きます。
- app/ja_docs.json           … 手で書いた辞書（同梱。編集して増やせます）
- $DATA_DIR/ja_learned.json  … Claude が訳した文を自動で追記する辞書（次回から Claude 不要）
辞書にない文だけを Claude に渡すので、同じ文（例: --dry-run の説明）は二度と送りません。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path

BUILTIN = Path(__file__).resolve().parent / "ja_docs.json"
LEARNED = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data")) / "ja_learned.json"
LEARN = os.environ.get("CLAUDE_SAVE_TRANSLATIONS", "1") != "0"

_lock = threading.Lock()
_cache: dict | None = None
_mtime: tuple = ()

# 文の区切り（". " の後が大文字・数字・記号で始まるところ）。略語の後では区切らない
_ABBR = re.compile(r"(?:\b(?:e\.g|i\.e|etc|vs|Inc|Ltd|No|approx|min|max|Mr|Ms|Dr|U\.S)|\b[A-Z])\.$")
_SPLIT = re.compile(r"(?<=[.!?:])\s+(?=[A-Z0-9\"'(\[<`])")


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def sentences(text: str) -> list[str]:
    out: list[str] = []
    for part in _SPLIT.split(norm(text)):
        if out and _ABBR.search(out[-1]):
            out[-1] += " " + part
        elif part:
            out.append(part)
    return out


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {norm(k): v for k, v in data.items() if isinstance(v, str) and v and not k.startswith("//")}


def _stamp():
    return tuple(p.stat().st_mtime if p.exists() else 0 for p in (BUILTIN, LEARNED))


def table() -> dict:
    """辞書（同梱 + 学習済み）。ファイルを編集すると再起動なしで反映される。"""
    global _cache, _mtime
    st = _stamp()
    with _lock:
        if _cache is None or st != _mtime:
            _cache = {**_read(LEARNED), **_read(BUILTIN)}   # 手書きの辞書を優先
            _mtime = st
        return _cache


# 決まった形の文（wait コマンドの説明など）は、正規表現のパターンで訳す（Claude 不要）
PATTERNS = [(re.compile(p), j) for p, j in [
    (r"^Wait until JMESPath query (.+) returns (.+) for all elements when polling with (\S+)\.$",
     r"\3 でポーリングし、JMESPath クエリ \1 がすべての要素で \2 を返すまで待機します。"),
    (r"^Wait until JMESPath query (.+) returns (.+) for any element when polling with (\S+)\.$",
     r"\3 でポーリングし、JMESPath クエリ \1 がいずれかの要素で \2 を返すまで待機します。"),
    (r"^Wait until JMESPath query (.+) returns (.+) when polling with (\S+)\.$",
     r"\3 でポーリングし、JMESPath クエリ \1 が \2 を返すまで待機します。"),
    (r"^Wait until (\d+) response is received when polling with (\S+)\.$",
     r"\2 でポーリングし、HTTP \1 のレスポンスが返るまで待機します。"),
    (r"^Wait until (\S+) is thrown when polling with (\S+)\.$",
     r"\2 でポーリングし、エラー \1 が返るまで待機します。"),
    (r"^Wait until the expected state is reached when polling with (\S+)\.$",
     r"\1 でポーリングし、期待した状態になるまで待機します。"),
    (r"^It will poll every (\d+) seconds until a successful state has been reached\.$",
     r"成功の状態になるまで \1 秒ごとに確認します。"),
    (r"^This will exit with a return code of 255 after (\d+) failed checks\.$",
     r"\1 回確認しても成功しない場合は、終了コード 255 で終了します。"),
]]


def _pattern(sentence: str) -> str | None:
    for rx, ja in PATTERNS:
        if rx.match(sentence):
            return rx.sub(ja, sentence)
    return None


def _one(d: dict, sentence: str) -> str | None:
    return d.get(sentence) or _pattern(sentence)


def missing(texts) -> list[str]:
    """辞書にない文（重複なし・出現順）。"""
    d, out = table(), {}
    for text in texts:
        t = norm(text)
        if not t or t in d:
            continue
        for p in sentences(t):
            if not _one(d, p):
                out[p] = None
    return list(out)


def translate(text: str, extra: dict) -> str:
    """辞書 + extra（今回 Claude が訳した文）で訳す。訳せない文は英語のまま残す。"""
    t = norm(text)
    d = table()
    if t in d:
        return d[t]
    out = ""
    for p in sentences(t):
        ja = extra.get(p) or _one(d, p)
        # 訳せずに英語のまま残す文は、前が英語なら空白で区切る（日本語の文どうしは詰めてつなぐ）
        if ja is None and out and out[-1].isascii() and not out[-1].isspace():
            out += " "
        out += ja or p
    return out


def learn(pairs: dict) -> None:
    """Claude の訳を学習辞書に追記する（公開されている botocore の説明文の訳だけ。ユーザーの入力は含まない）。"""
    if not LEARN or not pairs:
        return
    with _lock:
        data = {}
        if LEARNED.exists():
            try:
                data = json.loads(LEARNED.read_text(encoding="utf-8"))
            except ValueError:
                data = {}
        data.update({norm(k): v for k, v in pairs.items() if norm(k) and v})
        LEARNED.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=LEARNED.parent, prefix=".ja_learned.")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=0, sort_keys=True)
        os.replace(tmp, LEARNED)
