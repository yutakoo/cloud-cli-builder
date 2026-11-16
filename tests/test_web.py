"""画面・共通の仕組み（ホスト名・X-CCB・セキュリティヘッダー・大きさの上限）。"""
import json
import re

from conftest import WRITE

from app import main

FAV = {"provider": "aws", "title": "t", "command": "aws sts get-caller-identity"}


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.get_json() == {"ok": True}


def test_allowed_hosts(client):
    for base in ("http://localhost", "http://127.0.0.1:8080", "http://[::1]:8080"):
        assert client.get("/healthz", base_url=base).status_code == 200, base


def test_unknown_host_is_rejected(client):
    r = client.get("/healthz", base_url="http://evil.example")
    assert r.status_code == 403
    assert client.get("/api/aws/services", base_url="http://rebind.example:8080").status_code == 403


def test_write_requires_header(client):
    r = client.post("/api/favorites", json=FAV)
    assert r.status_code == 403 and "header" in r.get_json()["error"]
    for method in (client.put, client.delete):
        assert method("/api/favorites/1").status_code == 403


def test_cross_origin_write_is_rejected(client):
    r = client.post("/api/favorites", json=FAV, headers={**WRITE, "Origin": "http://evil.example"})
    assert r.status_code == 403
    r = client.post("/api/favorites", json=FAV, headers={**WRITE, "Origin": "http://localhost"})
    assert r.status_code == 201


def test_security_headers(client):
    r = client.get("/")
    csp = r.headers["Content-Security-Policy"]
    script_src = re.search(r"script-src ([^;]+)", csp).group(1)
    assert "'unsafe-inline'" not in script_src
    assert "frame-ancestors 'none'" in csp
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_index_embeds_boot_data_without_inline_script(client):
    html = client.get("/").get_data(as_text=True)
    assert "<script>" not in html                       # インラインのスクリプトはない（CSP で禁止）
    m = re.search(r'<script type="application/json" id="bootData">(.*?)</script>', html, re.S)
    boot = json.loads(m.group(1))
    assert set(boot) == {"ja_search", "claude", "ui_ja"}
    assert boot["claude"]["available"] is False         # テストでは認証情報なし
    assert "インスタンス" in boot["ja_search"]["nouns"]
    for js in ("paste.js", "quote.js", "app.js"):
        assert js in html


def test_boot_data_is_escaped(client, monkeypatch):
    """埋め込む JSON に </script> が入っても、script 要素を抜け出さない。"""
    monkeypatch.setattr(main, "ui_ja", lambda: {"x": "</script><script>alert(1)</script>"})
    html = client.get("/").get_data(as_text=True)
    assert "</script><script>alert(1)" not in html


def test_request_body_limit(client):
    r = client.post("/api/favorites/import", data=b"[" + b" " * (6 * 1024 * 1024) + b"]",
                    headers={**WRITE, "Content-Type": "application/json"})
    assert r.status_code == 413


def test_unknown_provider_is_404(client):
    assert client.get("/api/gcp/services").status_code == 404
    assert client.get("/api/gcp/search?q=x").status_code == 404
