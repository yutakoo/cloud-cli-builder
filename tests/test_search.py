"""コマンドの横断検索（GET /api/aws/search）。"""
from app import main


def search(client, q):
    r = client.get("/api/aws/search", query_string={"q": q})
    assert r.status_code == 200
    return r.get_json()


def test_empty_query(client):
    assert search(client, "") == {"results": [], "descriptions": [], "total": 0, "service": None}
    assert search(client, "aws")["total"] == 0


def test_matches_start_of_words(client):
    res = search(client, "inst")["results"]
    assert any(i["service"] == "ec2" and i["name"] == "describe-instances" for i in res)
    for i in res:   # 単語の途中では一致しない
        assert "-inst" in "-" + i["name"].replace(" ", "-") or "-inst" in "-" + i["service"]


def test_first_word_narrows_to_service(client):
    r = search(client, "ec2 stop")
    assert r["service"] == "ec2"
    assert all(i["service"] == "ec2" for i in r["results"])
    assert "stop-instances" in [i["name"] for i in r["results"]]


def test_exact_command_ranks_first(client):
    r = search(client, "aws ec2 describe-instances")
    assert (r["results"][0]["service"], r["results"][0]["name"]) == ("ec2", "describe-instances")


def test_service_name_ranks_its_commands_first(client):
    assert search(client, "s3")["results"][0]["service"] == "s3"


def test_japanese_query(client):
    res = search(client, "インスタンス 一覧")["results"]
    assert any(i["service"] == "ec2" and i["name"] == "describe-instances" for i in res)
    assert all("instance" in i["name"] for i in res)


def test_description_matches_only_when_few_name_matches(client):
    r = search(client, "describe")
    assert r["total"] >= 10 and r["descriptions"] == []


def test_result_limit(client):
    r = search(client, "list")
    assert len(r["results"]) == 150 and r["total"] > 150


def test_token_matches():
    assert main.token_matches("describe-instances", "", "インスタンス一覧")
    assert main.token_matches("stop-instances", "", "インスタンスを停止")
    assert not main.token_matches("create-bucket", "", "インスタンス一覧")
    assert not main.token_matches("describe-instances", "", "まったく関係ない")
    assert main.token_matches("x", "日本語の説明", "日本語")        # 日本語名・説明にそのまま含まれる
