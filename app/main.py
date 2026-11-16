"""Cloud CLI Builder — AWS CLI コマンドを日本語UIで組み立てる自分用ツール。"""
from __future__ import annotations

import os
from functools import lru_cache
import re
from urllib.parse import urlparse

from flask import Flask, Response, abort, jsonify, render_template, request

from . import aws_catalog, claude_assist, favorites, ja

app = Flask(__name__)
# リクエスト本文の上限（お気に入りのインポートでも十分な大きさ）。超えると 413
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

# ---------- 他のサイト・他の端末からの操作を防ぐ ----------
# 開いてよいホスト名（DNS リバインディング対策）。必要なら ALLOWED_HOSTS=myhost,192.168.1.10 のように追加
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1",
                 *[h.strip().lower() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]}
CSRF_HEADER = "X-CCB"   # 画面の JS だけが付けるヘッダー。他サイトから付けて送ると CORS の事前確認で止まる


@app.before_request
def _guard():
    host = (request.host or "").lower()
    host = host.rsplit(":", 1)[0] if not host.endswith("]") else host
    if host not in ALLOWED_HOSTS:
        return jsonify({"error": "Host not allowed"}), 403
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if request.headers.get(CSRF_HEADER) != "1":
            return jsonify({"error": "Missing request header"}), 403
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc.lower() != (request.host or "").lower():
            return jsonify({"error": "Cross-origin request blocked"}), 403


@app.after_request
def _headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "font-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    return resp
app.json.ensure_ascii = False
app.json.sort_keys = False

CATALOGS = {"aws": aws_catalog}

def _cat(provider: str):
    cat = CATALOGS.get(provider)
    if not cat:
        abort(404)
    return cat


@app.get("/")
def index():
    return render_template("index.html",
                           ja_search={"verbs": ja.SEARCH_VERBS, "nouns": ja.SEARCH_NOUNS},
                           botocore_version=aws_catalog.botocore_version(),
                           claude=claude_assist.status(), ui_ja=ui_ja())


def ui_ja() -> dict:
    """画面の文言（英語 → 日本語）。日本語化ボタンが成功したときだけ使う。"""
    import json
    from pathlib import Path
    try:
        return json.loads((Path(__file__).parent / "ui_ja.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@app.get("/api/<provider>/services")
def api_services(provider):
    return jsonify(_cat(provider).services())


@app.get("/api/<provider>/services/<service>")
def api_commands(provider, service):
    return jsonify(_cat(provider).commands(service))


@app.get("/api/<provider>/command")
def api_command(provider):
    d = _cat(provider).command_detail(request.args.get("service", ""), request.args.get("name", ""))
    if not d:
        abort(404)
    return jsonify(d)


def token_matches(name: str, hay: str, token: str) -> bool:
    """検索語1つが、コマンド（name）またはテキスト（hay）にマッチするか。
    「インスタンス一覧」のような日本語は辞書で英単語に分解して判定する。"""
    if token in hay:
        return True
    words = re.split(r"[\s\-]+", name)
    rest, used = token, False
    for jw in sorted({**ja.SEARCH_VERBS, **ja.SEARCH_NOUNS}, key=len, reverse=True):
        if jw not in rest:
            continue
        if jw in ja.SEARCH_VERBS:
            ok = any(e in words for e in ja.SEARCH_VERBS[jw])
        else:
            ok = any(e in name for e in ja.SEARCH_NOUNS[jw])
        if not ok:
            return False
        rest, used = rest.replace(jw, ""), True
    rest = rest.strip("のをにでと 　")
    return used and (not rest or rest in hay)


_WORDS = re.compile(r"[\s\-]+")


@lru_cache(maxsize=4)
def _prepared(provider: str) -> list[tuple]:
    """検索用に、コマンド名などを一度だけ加工しておく（検索のたびに加工しない）"""
    out = []
    for i in _cat(provider).search_index():
        out.append((i, "-" + _WORDS.sub("-", i["name"].lower()), "-" + _WORDS.sub("-", i["service"].lower()),
                    set(_WORDS.split(i["name"])), " ".join([i.get("ja", ""), i.get("service_ja", "")]).lower(),
                    i.get("summary") or ""))
    return out


def classify(prep: tuple, tokens: list[tuple]):
    """None: 不一致 / (0, ...): コマンド名・サービス名で一致 / (1, ...): 説明文で一致"""
    item, name_n, svc_n, words, ja_hay, summary = prep
    via_desc, exact, in_name = False, 0, 0
    for t, t_n, t_re in tokens:
        if t_re is None:   # 日本語は辞書で英単語に置き換えてコマンド名と照合（日本語名も対象）
            if not token_matches(item["name"], ja_hay, t):
                return None
            in_name += 1
        elif t_n in name_n:
            in_name += 1
            exact += t == item["name"] or bool(words & {t, t + "s", t + "es"})   # 単数形でも完全一致扱い
        elif t_n in svc_n:
            exact += t == item["service"]
        elif len(t) >= 2 and t_re.search(summary):
            via_desc = True
        else:
            return None
    return (1 if via_desc else 0), exact, in_name


@app.get("/api/<provider>/search")
def api_search(provider):
    """コマンド横断検索。
    - コマンド名・サービス名は単語の先頭で一致（部分一致はしない）
    - 1語目がサービス名なら、そのサービスに絞る（例: "ec2 stop"）
    - 説明文での一致は単語単位のみ。コマンド名での一致が少ないときだけ別枠で返す"""
    q = request.args.get("q", "").strip().lower()
    tokens = q.split()
    if tokens and tokens[0] == "aws":
        tokens = tokens[1:]
    if not tokens:
        return jsonify({"results": [], "descriptions": [], "total": 0, "service": None})
    index = _prepared(provider)
    service = None
    if len(tokens) > 1 and tokens[0] in {p[0]["service"] for p in index}:
        service, tokens = tokens[0], tokens[1:]
        index = [p for p in index if p[0]["service"] == service]
    # 検索語も一度だけ加工する（英字: 単語の先頭一致用の形と、説明文の単語一致用の正規表現）
    toks = [(t, "-" + _WORDS.sub("-", t), re.compile(r"(?<![\w-])" + re.escape(t) + r"(?![\w-])", re.I)) if t.isascii()
            else (t, None, None) for t in tokens]
    primary, secondary = [], []
    for p in index:
        c = classify(p, toks)
        if c:
            (secondary if c[0] else primary).append((c, p[0]))
    joined = " ".join(tokens)

    def rank(x):
        (_, exact, in_name), i = x
        return (0 if joined in (i["name"], f"{i['service']} {i['name']}") else 1,
                0 if i["service"] in tokens else 1,   # "s3" ならまず S3 のコマンド
                -exact, -in_name,
                0 if i.get("curated") else 1,
                0 if i.get("service_ja") else 1,   # 主要サービスを先に
                len(i["name"]), i["service"], i["name"])
    primary.sort(key=rank)
    secondary.sort(key=rank)
    return jsonify({"results": [i for _, i in primary[:150]],
                    "descriptions": [i for _, i in secondary[:40]] if len(primary) < 10 else [],
                    "total": len(primary), "service": service})


# ---------- お気に入り ----------
@app.get("/api/favorites")
def fav_list():
    return jsonify(favorites.list_all(request.args.get("provider")))


@app.post("/api/favorites")
def fav_create():
    try:
        return jsonify(favorites.create(request.get_json(force=True))), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.put("/api/favorites/<int:fid>")
def fav_update(fid):
    try:
        r = favorites.update(fid, request.get_json(force=True))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(r) if r else abort(404)


@app.post("/api/favorites/<int:fid>/use")
def fav_use(fid):
    favorites.touch(fid)
    return jsonify({"ok": True})


@app.delete("/api/favorites/<int:fid>")
def fav_delete(fid):
    return jsonify({"ok": favorites.delete(fid)})


@app.get("/api/favorites/export")
def fav_export():
    import json
    body = json.dumps(favorites.list_all(), ensure_ascii=False, indent=2)
    return Response(body, mimetype="application/json",
                    headers={"Content-Disposition": "attachment; filename=cli-favorites.json"})


@app.post("/api/favorites/import")
def fav_import():
    items = request.get_json(force=True)
    if not isinstance(items, list):
        return jsonify({"error": "Expected a JSON array"}), 400
    return jsonify({"imported": favorites.import_many(items)})


# ---------- Claude 連携（ボタンを押したときだけ実行・結果は保持しない） ----------
@app.post("/api/claude/localize")
def claude_localize():
    st = claude_assist.status()
    body = request.get_json(force=True) or {}
    if not st["available"]:   # Claude 連携していなければ、日本語化（辞書分も含めて）もしない
        return jsonify({"error": st["reason"]}), 503
    dict_only = body.get("scope") == "dict"
    provider = body.get("provider")
    detail = _cat(provider).command_detail(body.get("service", ""), body.get("name", ""))
    if not detail:
        abort(404)
    scope = "filled" if body.get("scope") == "filled" else "all"
    filled = [str(x) for x in body.get("filled") or []]
    try:
        data = claude_assist.translate_aws(detail, scope, filled, use_claude=not dict_only)
    except claude_assist.ClaudeError as e:
        return jsonify({"error": str(e)}), 502
    resp = jsonify(data)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/healthz")
def healthz():
    return {"ok": True}


favorites.init()
# 起動時に一度だけ全コマンドの索引を作る（gunicorn --preload ならワーカー間で共有される）
aws_catalog.warmup()
_prepared("aws")   # 検索用の下ごしらえも起動時に済ませる

if __name__ == "__main__":
    # 開発用。デバッガーは外部に公開しない（FLASK_DEBUG=1 のときだけ有効）
    app.run(host="127.0.0.1", port=8080, debug=os.environ.get("FLASK_DEBUG") == "1")
