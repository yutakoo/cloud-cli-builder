"""botocore からのコマンド定義（app/aws_catalog.py）と、その API。"""
from app import aws_catalog as cat


# ---------- サービス ----------
def test_services_contains_botocore_and_custom_services():
    svcs = {s["name"]: s for s in cat.services()}
    assert {"ec2", "s3api", "lambda", "s3", "configure"} <= set(svcs)
    assert "config" not in svcs and "configservice" in svcs           # botocore 名は CLI の名前に直す
    assert svcs["ec2"]["title"] == "Amazon Elastic Compute Cloud"
    assert svcs["ec2"]["ja"] and svcs["ec2"]["count"] > 100
    assert svcs["ec2"]["doc_url"] == "https://docs.aws.amazon.com/cli/latest/reference/ec2/index.html"
    assert svcs["s3"]["title"] == "Amazon S3 (high-level commands)"
    names = [s["name"] for s in cat.services()]
    assert names == sorted(names)


def test_service_rank_follows_ja_py_order():
    svcs = {s["name"]: s for s in cat.services()}
    assert svcs["s3"]["rank"] < svcs["ec2"]["rank"] < svcs["lambda"]["rank"]
    no_ja = next(s for s in cat.services() if not s["ja"])
    assert no_ja["rank"] == 9999


# ---------- コマンド一覧 ----------
def test_commands_of_ec2():
    cmds = {c["name"]: c for c in cat.commands("ec2")}
    d = cmds["describe-instances"]
    assert d["op"] == "DescribeInstances" and d["required"] == 0
    assert d["ja"] == "EC2インスタンスの一覧・詳細を表示する"           # app/ja.py の AWS_COMMAND_JA
    w = cmds["wait instance-running"]
    assert w["waiter"] is True
    assert w["doc_url"].endswith("/ec2/wait/instance-running.html")
    assert cmds["create-vpc"]["ja"] == "vpc を作成する"                 # 動詞から自動で作る説明


def test_commands_of_custom_service():
    cmds = {c["name"]: c for c in cat.commands("s3")}
    assert {"ls", "cp", "sync", "mb", "rb"} <= set(cmds)
    assert cmds["cp"]["custom"] is True and cmds["cp"]["op"] is None
    assert cmds["cp"]["required"] == 2                                  # source と dest


def test_commands_of_unknown_service():
    assert cat.commands("no-such-service") == []


def test_commands_cache_is_bounded():
    assert cat.commands.cache_info().maxsize is not None


# ---------- コマンドの詳細 ----------
def test_command_detail_params():
    d = cat.command_detail("ec2", "describe-instances")
    p = {x["name"]: x for x in d["params"]}
    assert p["--instance-ids"]["type"] == "list"
    assert p["--dry-run"]["type"] == "boolean" and p["--dry-run"]["neg"] == "--no-dry-run"
    f = p["--filters"]
    assert f["type"] == "json" and f["shorthand"] == "Name=string,Values=string,string"
    assert f["example"].startswith('[{"Name": "string"')
    assert d["doc_url"] == "https://docs.aws.amazon.com/cli/latest/reference/ec2/describe-instances.html"
    assert d["global_options"][0]["name"] == "--region"


def test_command_detail_pagination_hides_tokens():
    d = cat.command_detail("ec2", "describe-instances")
    names = {x["name"] for x in d["params"]}
    assert "--next-token" not in names and "--max-results" not in names
    assert [x["name"] for x in d["pagination"]] == ["--max-items", "--page-size", "--starting-token", "--no-paginate"]


def test_command_detail_required_first():
    d = cat.command_detail("ec2", "create-vpc")
    req = [x["required"] for x in d["params"]]
    assert req == sorted(req, reverse=True)


def test_command_detail_enum_and_hint():
    d = cat.command_detail("s3api", "put-object")
    p = {x["name"]: x for x in d["params"]}
    assert p["--acl"]["type"] == "enum" and "private" in p["--acl"]["choices"]
    assert p["--body"]["hint"] == "To pass a file: fileb://path"


def test_command_detail_waiter():
    d = cat.command_detail("ec2", "wait instance-running")
    assert d["waiter"] is True and d["op"] == "DescribeInstances"
    assert "It will poll every 15 seconds" in d["description"]


def test_command_detail_custom():
    d = cat.command_detail("s3", "cp")
    assert d["custom"] is True
    assert [x["name"] for x in d["positionals"]] == ["source", "dest"]
    assert d["pagination"] == []


def test_command_detail_cli_specific_parts():
    p = {x["name"] for x in cat.command_detail("swf", "register-activity-type")["params"]}
    assert "--activity-version" in p and "--version" not in p          # app/aws_arg_renames.json
    d = cat.command_detail("lambda", "invoke")
    assert [x["name"] for x in d["positionals"]] == ["outfile"]        # EXTRA_POSITIONALS


def test_command_detail_unknown():
    assert cat.command_detail("ec2", "no-such-command") is None
    assert cat.command_detail("no-such-service", "x") is None
    assert cat.command_detail("ec2", "wait no-such-waiter") is None


# ---------- 文字列の加工 ----------
def test_strip_html_and_first_sentence():
    assert cat.strip_html("<p>a &amp; <b>b</b></p>") == "a & b"
    assert cat.strip_html("x" * 20, limit=5) == "xxxxx…"
    assert cat.strip_html(None) == ""
    assert cat.first_sentence("First one. Second one.") == "First one."


# ---------- API ----------
def test_api_services_and_commands(client):
    svcs = client.get("/api/aws/services").get_json()
    assert any(s["name"] == "ec2" for s in svcs)
    cmds = client.get("/api/aws/services/ec2").get_json()
    assert any(c["name"] == "describe-instances" for c in cmds)
    assert client.get("/api/aws/services/no-such").get_json() == []


def test_api_command(client):
    r = client.get("/api/aws/command?service=ec2&name=describe-instances")
    assert r.status_code == 200 and r.get_json()["op"] == "DescribeInstances"
    assert client.get("/api/aws/command?service=ec2&name=nope").status_code == 404
    assert client.get("/api/aws/command").status_code == 404
