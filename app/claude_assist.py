"""Claude Code 連携（日本語化ボタンを押したときだけ実行）。

Claude Code をヘッドレスモード（claude -p）で呼び出し、辞書にない文だけを翻訳させます。

安全策:
- --tools ""        … ツールを一切使わせない（Web の読み込み・コマンド実行・ファイル操作もできない）
- dontAsk + deny    … 万一ツールが有効になっても自動で拒否
- 従量課金の API キーは子プロセスに渡さない（プランのトークンだけを使う）

データを保持しないための工夫:
- 実行ごとに一時ディレクトリを作り、そこを作業ディレクトリ兼 Claude Code の設定ディレクトリにする
- --no-session-persistence でセッション（会話ログ）を保存しない
- 実行後に一時ディレクトリごと削除（訳した文だけは ja_docs の学習辞書に保存）
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

MODEL = os.environ.get("CLAUDE_MODEL", "sonnet")
TRANSLATE_MODEL = os.environ.get("CLAUDE_TRANSLATE_MODEL") or MODEL
TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "300"))
MAX_BUDGET = os.environ.get("CLAUDE_MAX_BUDGET_USD", "1.00")
# 翻訳は CHUNK 文ずつに分けて、最大 CONCURRENCY 個を同時に実行する（1回で全部訳すより大幅に速い）
CHUNK = max(5, int(os.environ.get("CLAUDE_TRANSLATE_CHUNK", "20")))
CONCURRENCY = max(1, int(os.environ.get("CLAUDE_MAX_CONCURRENCY", "4")))
_slots = threading.BoundedSemaphore(CONCURRENCY)


class ClaudeError(Exception):
    pass


# ---------- 接続の事前確認（つながらないときに 300 秒待たず、すぐ知らせる） ----------
API_HOST = "api.anthropic.com"
_net_ok_until = 0.0
_net_lock = threading.Lock()


def check_network() -> None:
    """Claude を呼ぶ前に、API に TCP で接続できるかを数秒で確かめる（成功したら 60 秒は確認を省く）。
    プロキシ経由の環境では直接つながらないのが正常なので確認しない。"""
    global _net_ok_until
    if any(os.environ.get(k) for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")):
        return
    with _net_lock:
        if time.time() < _net_ok_until:
            return
        try:
            socket.create_connection((API_HOST, 443), timeout=5).close()
        except OSError as e:
            raise ClaudeError(f"Cannot connect to {API_HOST} from the server ({e.strerror or e}). "
                              "Check the network of the Docker container (see README: Troubleshooting).")
        _net_ok_until = time.time() + 60


def status() -> dict:
    binary = shutil.which(os.environ.get("CLAUDE_BIN", "claude"))
    auth = bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("CLAUDE_ASSUME_AUTH"))
    reason = reason_ja = ""
    if not binary:
        reason = "Claude Code (the claude command) was not found. Run the app from the Docker image."
        reason_ja = "Claude Code（claude コマンド）が見つかりません。Docker イメージで起動してください。"
    elif not auth:
        reason = "Set CLAUDE_CODE_OAUTH_TOKEN in .env (see README)."
        reason_ja = ".env に CLAUDE_CODE_OAUTH_TOKEN を設定してください（README 参照）。"
    return {"available": bool(binary and auth), "reason": reason, "reason_ja": reason_ja, "model": TRANSLATE_MODEL}


# ---------- Claude Code の実行 ----------
TRANSLATE_SYSTEM = """You translate AWS CLI documentation sentences from English into natural, concise Japanese for engineers.
Keep command names, option names, API names, resource names and literal values (e.g. --instance-ids, InstanceId, ARN, us-east-1) in English.
Return only the JSON requested."""


def run(prompt: str, schema: dict, model: str | None = None) -> dict:
    """ツールなし・短いシステムプロンプトで1回だけ実行し、JSON スキーマどおりの結果を返す。"""
    model = model or MODEL
    if not _slots.acquire(timeout=TIMEOUT):
        raise ClaudeError("Too many Claude runs at once. Wait a moment and try again.")
    tmp = tempfile.mkdtemp(prefix="ccb-claude-")
    try:
        settings = {"permissions": {"deny": ["WebFetch", "WebSearch", "Bash", "Read", "Write", "Edit", "Glob", "Grep", "Task"]}}
        cmd = [
            os.environ.get("CLAUDE_BIN", "claude"), "-p",
            "--output-format", "json",
            "--model", model,
            "--tools", "",                       # ツールを使わせない（標準のツール定義も送らない）
            "--system-prompt", TRANSLATE_SYSTEM,  # Claude Code 標準の長いシステムプロンプトを送らない
            "--permission-mode", "dontAsk",
            "--no-session-persistence",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--settings", json.dumps(settings),
            "--json-schema", json.dumps(schema, ensure_ascii=False),
            "--max-budget-usd", MAX_BUDGET,
        ]
        env = {**os.environ,
               "CLAUDE_CONFIG_DIR": os.path.join(tmp, ".claude"),   # 会話ログ等は一時ディレクトリへ（実行後に削除）
               "DISABLE_AUTOUPDATER": "1", "DISABLE_TELEMETRY": "1",
               "DISABLE_ERROR_REPORTING": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
        # 従量課金の API キーは使わない（Claude Code は API キーがあるとプランのトークンより優先するため、必ず外す）
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"):
            env.pop(k, None)
        if not env.get("CLAUDE_CODE_OAUTH_TOKEN"):
            env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                  cwd=tmp, env=env, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            raise ClaudeError(f"Did not finish within {TIMEOUT}s. Narrow the scope to \"Required & filled only\" and try again.")
        except FileNotFoundError:
            raise ClaudeError("The claude command was not found.")
        out = _result(proc.stdout)
        if out is None:
            msg = (proc.stderr or proc.stdout or "").strip()[-600:]
            raise ClaudeError(f"Claude Code failed: {msg or 'no output'}")
        if out.get("is_error") or out.get("subtype") != "success":
            raise ClaudeError(f"Claude Code returned an error: {str(out.get('result') or out.get('subtype'))[:600]}")
        data = out.get("structured_output")
        if not isinstance(data, dict):
            try:
                data = json.loads(out.get("result") or "")
            except ValueError:
                raise ClaudeError("Could not read Claude's result. Please try again.")
        data["_meta"] = {"model": model, "cost_usd": out.get("total_cost_usd"), "duration_ms": out.get("duration_ms")}
        return data
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        _slots.release()


def _result(stdout: str) -> dict | None:
    """出力から最終結果（type=result）を取り出す。1つの JSON でも、1行ずつの JSON（stream-json）でも読める。"""
    try:
        ev = json.loads(stdout)
        if isinstance(ev, dict):
            return ev
        if isinstance(ev, list):
            return next((e for e in reversed(ev) if isinstance(e, dict) and e.get("type") == "result"), None)
    except ValueError:
        pass
    for line in reversed(stdout.splitlines()):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get("type") == "result":
            return ev
    return None


# ---------- タスク ----------
def _params_for(detail: dict, scope: str, filled: list[str]) -> list[dict]:
    ps = detail["params"] + detail.get("pagination", [])
    if scope == "filled":
        ps = [p for p in ps if p.get("required") or p["name"] in filled]
    return ps


def _strings(detail: dict, scope: str, filled: list[str]) -> list[dict]:
    """日本語化の対象（説明文のある項目）"""
    ps = (detail.get("positionals") or []) + _params_for(detail, scope, filled) + detail.get("global_options", [])
    return [{"name": p["name"], "doc": p.get("doc", "")} for p in ps if p.get("doc")]


TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {"ja": {"type": "array", "items": {"type": "string"},
                          "description": "Japanese translations, same order and count as the input lines"}},
    "required": ["ja"],
}


def _translate_chunk(detail: dict, todo: list[str]) -> tuple[dict, dict]:
    from . import ja_docs
    lines = "\n".join(f"{n}\t{t}" for n, t in enumerate(todo, 1))
    prompt = (f"Translate each numbered line ({len(todo)} lines, from `aws {detail['service']} {detail['name']}`) into Japanese. "
              f"Return ja[] with exactly {len(todo)} items in the same order, without the numbers.\n\n{lines}")
    data = run(prompt, TRANSLATE_SCHEMA, model=TRANSLATE_MODEL)
    out = data.get("ja") if isinstance(data.get("ja"), list) else []
    if len(out) != len(todo):
        raise ClaudeError(f"Claude returned {len(out)} of {len(todo)} translations. Please try again.")
    pairs = {t: str(j).strip() for t, j in zip(todo, out) if str(j).strip()}
    ja_docs.learn(pairs)   # 塊ごとに保存（途中で失敗しても、訳せた分は次回 Claude 不要）
    return pairs, data["_meta"]


def translate_aws(detail: dict, scope: str, filled: list[str], use_claude: bool = True) -> dict:
    """辞書で訳せる文は辞書で訳し、辞書にない文だけを Claude に送る（同じ文は1回だけ）。
    辞書にない文は CHUNK 文ずつに分け、並行して翻訳する。"""
    from . import ja, ja_docs
    items = _strings(detail, scope, filled)
    desc = detail.get("description") or detail.get("summary") or ""
    todo = ja_docs.missing([desc] + [i["doc"] for i in items])
    total = len({x for t in [desc] + [i["doc"] for i in items] for x in ja_docs.sentences(t)})
    extra, meta = {}, {"translation_only": True, "sent": 0, "total": total, "failed": 0}
    if todo and use_claude:
        check_network()
        chunks = [todo[i:i + CHUNK] for i in range(0, len(todo), CHUNK)]
        t0, metas, errors = time.time(), [], []
        with ThreadPoolExecutor(max_workers=min(CONCURRENCY, len(chunks))) as ex:
            for chunk, fut in [(c, ex.submit(_translate_chunk, detail, c)) for c in chunks]:
                try:
                    pairs, m = fut.result()
                    extra.update(pairs); metas.append(m)
                except ClaudeError as e:
                    errors.append(e); meta["failed"] += len(chunk)
        if not metas:   # 全部失敗
            raise errors[0]
        meta.update(model=metas[0].get("model"), duration_ms=int((time.time() - t0) * 1000), chunks=len(chunks),
                    cost_usd=sum(m.get("cost_usd") or 0 for m in metas))
        meta["sent"] = len(todo) - meta["failed"]
    meta["untranslated"] = len(todo) - meta["sent"] if use_claude else len(todo)
    meta["dict_hits"] = max(total - len(todo), 0)
    key = f"{detail['service']} {detail['name']}"
    return {
        "title_ja": ja.AWS_COMMAND_JA.get(key, ""),
        "service_ja": detail.get("service_ja", ""),
        "description_ja": ja_docs.translate(desc, extra) if desc else "",
        "params": [{"name": i["name"], "ja": ja_docs.translate(i["doc"], extra)} for i in items],
        "_meta": meta,
    }
