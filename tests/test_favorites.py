"""お気に入り（app/favorites.py と /api/favorites）。"""
import json

import pytest
from conftest import WRITE

from app import favorites

FAV = {"provider": "aws", "title": "who am i", "command": "aws sts get-caller-identity", "memo": "m",
       "service": "sts", "operation": "get-caller-identity",
       "doc_url": "https://docs.aws.amazon.com/cli/latest/reference/sts/get-caller-identity.html",
       "state": {"provider": "aws", "service": "sts", "name": "get-caller-identity", "values": {}}}


def post(client, path, body):
    return client.post(path, data=json.dumps(body), headers={**WRITE, "Content-Type": "application/json"})


def put(client, path, body):
    return client.put(path, data=json.dumps(body), headers={**WRITE, "Content-Type": "application/json"})


def test_create_list_update_delete(client):
    r = post(client, "/api/favorites", FAV)
    assert r.status_code == 201
    fav = r.get_json()
    assert fav["title"] == "who am i" and fav["state"]["name"] == "get-caller-identity" and fav["use_count"] == 0

    assert [f["id"] for f in client.get("/api/favorites").get_json()] == [fav["id"]]

    r = put(client, f"/api/favorites/{fav['id']}", {"memo": "new", "id": 999, "use_count": 50})
    assert r.status_code == 200
    upd = r.get_json()
    assert upd["memo"] == "new" and upd["title"] == "who am i"
    assert upd["id"] == fav["id"] and upd["use_count"] == 0           # FIELDS 以外は変えられない

    r = client.delete(f"/api/favorites/{fav['id']}", headers=WRITE)
    assert r.get_json() == {"ok": True}
    assert client.delete(f"/api/favorites/{fav['id']}", headers=WRITE).get_json() == {"ok": False}
    assert client.get("/api/favorites").get_json() == []


def test_list_order_by_use_count(client):
    a = post(client, "/api/favorites", {**FAV, "title": "a"}).get_json()
    b = post(client, "/api/favorites", {**FAV, "title": "b"}).get_json()
    assert client.post(f"/api/favorites/{b['id']}/use", headers=WRITE).get_json() == {"ok": True}
    assert [f["title"] for f in client.get("/api/favorites").get_json()] == ["b", "a"]
    assert client.get("/api/favorites?provider=aws").get_json()[0]["use_count"] == 1
    assert client.get("/api/favorites?provider=gcp").get_json() == []
    assert a["id"] != b["id"]


@pytest.mark.parametrize("body, msg", [
    ({**FAV, "title": " "}, "title is required"),
    ({**FAV, "command": ""}, "command is required"),
    ({**FAV, "provider": "gcp"}, "provider must be aws"),
    ([FAV], "Expected a JSON object"),
    ("text", "Expected a JSON object"),
])
def test_create_validation(client, body, msg):
    r = post(client, "/api/favorites", body)
    assert r.status_code == 400 and r.get_json()["error"] == msg


def test_update_validation(client):
    fav = post(client, "/api/favorites", FAV).get_json()
    assert put(client, f"/api/favorites/{fav['id']}", [1]).status_code == 400
    assert put(client, f"/api/favorites/{fav['id']}", {"provider": "gcp"}).status_code == 400
    assert put(client, "/api/favorites/99999", {"memo": "x"}).status_code == 404


def test_values_are_cleaned():
    fav = favorites.create({"provider": "aws", "title": ["x"], "command": 123, "memo": None,
                            "state": "not an object", "doc_url": "javascript:alert(1)"})
    assert fav["title"] == "['x']" and fav["command"] == "123" and fav["memo"] == ""
    assert fav["state"] == {}
    assert fav["doc_url"] == ""                                         # https:// 以外のリンクは保存しない


def test_export_and_import(client):
    post(client, "/api/favorites", FAV)
    r = client.get("/api/favorites/export")
    assert r.headers["Content-Disposition"] == "attachment; filename=cli-favorites.json"
    exported = json.loads(r.get_data(as_text=True))
    assert len(exported) == 1

    items = exported + [1, "s", None, [], {**FAV, "provider": "gcp"}, {**FAV, "title": "second"}]
    r = post(client, "/api/favorites/import", items)
    assert r.get_json() == {"imported": 2}                              # 不正な要素は飛ばす
    assert len(client.get("/api/favorites").get_json()) == 3


def test_import_requires_array(client):
    r = post(client, "/api/favorites/import", {"a": 1})
    assert r.status_code == 400
