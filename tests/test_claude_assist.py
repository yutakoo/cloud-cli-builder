"""Claude 連携（app/claude_assist.py と POST /api/claude/localize）。実際の Claude は呼ばない。"""
import json
import os
import subprocess

import pytest
from conftest import WRITE

from app import claude_assist as ca
from app import ja_docs

# 辞書にない文だけでできた、架空のコマンド
DETAIL = {
    "service": "ec2", "name": "describe-instances",
    "description": "Alpha unknown sentence. Beta unknown sentence.",
    "positionals": [],
    "params": [{"name": "--req", "doc": "Gamma unknown sentence.", "required": True},
               {"name": "--opt", "doc": "Delta unknown sentence.", "required": False},
               {"name": "--nodoc", "doc": "", "required": False}],
    "pagination": [], "global_options": [],
}


class FakeClaude:
    """claude_assist.run の代わり。送られた文に「訳:」を付けて返し、呼ばれた回数と送られた文を記録する。"""

    def __init__(self, fail_on=None):
        self.calls, self.fail_on = [], fail_on

    def __call__(self, prompt, schema, model=None):
        lines = [ln.split("\t", 1)[1] for ln in prompt.split("\n\n", 1)[1].splitlines()]
        self.calls.append(lines)
        if self.fail_on and any(self.fail_on in ln for ln in lines):
            raise ca.ClaudeError("boom")
        return {"ja": [f"訳:{ln}" for ln in lines], "_meta": {"model": model, "cost_usd": 0.01, "duration_ms": 5}}


@pytest.fixture
def fake(monkeypatch):
    f = FakeClaude()
    monkeypatch.setattr(ca, "run", f)
    monkeypatch.setattr(ca, "check_network", lambda: None)
    return f


# ---------- 使えるかの確認 ----------
def test_status(monkeypatch):
    monkeypatch.setattr(ca.shutil, "which", lambda _: None)
    st = ca.status()
    assert st["available"] is False and "not found" in st["reason"]

    monkeypatch.setattr(ca.shutil, "which", lambda _: "/usr/local/bin/claude")
    st = ca.status()
    assert st["available"] is False and "CLAUDE_CODE_OAUTH_TOKEN" in st["reason"]

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "dummy")
    st = ca.status()
    assert st["available"] is True and st["reason"] == ""
    assert "dummy" not in json.dumps(st)                               # トークンは返さない


def test_check_network(monkeypatch):
    monkeypatch.setattr(ca, "_net_ok_until", 0.0)

    def refuse(*a, **k):
        raise OSError(111, "Connection refused")
    monkeypatch.setattr(ca.socket, "create_connection", refuse)
    with pytest.raises(ca.ClaudeError, match="Cannot connect"):
        ca.check_network()
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:3128")             # プロキシ経由なら確認しない
    ca.check_network()


# ---------- claude の実行 ----------
def ok_result(**extra):
    return json.dumps({"type": "result", "subtype": "success", "is_error": False,
                       "structured_output": {"ja": ["訳"]}, "total_cost_usd": 0.02, "duration_ms": 10, **extra})


def test_run_command_and_environment(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        seen["config_dir_existed"] = os.path.isdir(os.path.dirname(kw["env"]["CLAUDE_CONFIG_DIR"]))
        return subprocess.CompletedProcess(cmd, 0, ok_result(), "")

    monkeypatch.setattr(ca.subprocess, "run", fake_run)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-passed")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    data = ca.run("prompt text", ca.TRANSLATE_SCHEMA, model="haiku")

    cmd = seen["cmd"]
    assert cmd[1] == "-p"
    assert cmd[cmd.index("--tools") + 1] == ""                          # ツールを一切使わせない
    assert cmd[cmd.index("--model") + 1] == "haiku"
    for flag in ("--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands"):
        assert flag in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert "Bash" in json.loads(cmd[cmd.index("--settings") + 1])["permissions"]["deny"]
    assert seen["input"] == "prompt text"
    assert "shell" not in seen                                          # シェルを通さない
    env = seen["env"]
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDE_CODE_USE_BEDROCK" not in env
    assert seen["config_dir_existed"] and not os.path.exists(seen["cwd"])  # 一時ディレクトリは実行後に削除
    assert data == {"ja": ["訳"], "_meta": {"model": "haiku", "cost_usd": 0.02, "duration_ms": 10}}


@pytest.mark.parametrize("stdout, msg", [
    (json.dumps({"type": "result", "subtype": "error_max_budget_usd", "is_error": True, "result": "over"}), "returned an error"),
    ("not json", "failed"),
    (json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "no json here"}), "Could not read"),
])
def test_run_errors(monkeypatch, stdout, msg):
    monkeypatch.setattr(ca.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout, ""))
    with pytest.raises(ca.ClaudeError, match=msg):
        ca.run("p", ca.TRANSLATE_SCHEMA)


def test_run_timeout_releases_slot(monkeypatch):
    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)
    monkeypatch.setattr(ca.subprocess, "run", slow)
    for _ in range(ca.CONCURRENCY + 1):                                 # 失敗しても同時実行の枠が戻る
        with pytest.raises(ca.ClaudeError, match="Did not finish"):
            ca.run("p", ca.TRANSLATE_SCHEMA)


def test_result_parsing():
    assert ca._result(ok_result())["subtype"] == "success"
    assert ca._result(json.dumps([{"type": "system"}, {"type": "result", "x": 1}]))["x"] == 1
    assert ca._result('{"type": "system"}\n{"type": "result", "x": 2}\n')["x"] == 2
    assert ca._result("garbage") is None


# ---------- 翻訳 ----------
def test_translate_sends_only_missing_sentences(fake):
    data = ca.translate_aws(DETAIL, "all", [])
    sent = [s for call in fake.calls for s in call]
    assert sent == ["Alpha unknown sentence.", "Beta unknown sentence.", "Gamma unknown sentence.", "Delta unknown sentence."]
    assert data["description_ja"] == "訳:Alpha unknown sentence.訳:Beta unknown sentence."
    assert data["params"] == [{"name": "--req", "ja": "訳:Gamma unknown sentence."},
                              {"name": "--opt", "ja": "訳:Delta unknown sentence."}]
    assert data["title_ja"] == "EC2インスタンスの一覧・詳細を表示する"
    m = data["_meta"]
    assert (m["sent"], m["failed"], m["untranslated"], m["dict_hits"]) == (4, 0, 0, 0)


def test_translations_are_learned(fake):
    ca.translate_aws(DETAIL, "all", [])
    ca.translate_aws(DETAIL, "all", [])
    assert len(fake.calls) == 1                                         # 2回目は学習辞書で訳せる
    assert ja_docs.missing([DETAIL["description"]]) == []


def test_scope_filled(fake):
    data = ca.translate_aws(DETAIL, "filled", [])
    assert [p["name"] for p in data["params"]] == ["--req"]            # 必須のものだけ
    data = ca.translate_aws(DETAIL, "filled", ["--opt"])
    assert [p["name"] for p in data["params"]] == ["--req", "--opt"]


def test_dictionary_only_does_not_call_claude(fake):
    data = ca.translate_aws(DETAIL, "all", [], use_claude=False)
    assert fake.calls == []
    assert data["description_ja"] == DETAIL["description"]             # 訳せない文は英語のまま
    assert data["_meta"]["untranslated"] == 4 and data["_meta"]["sent"] == 0


def test_chunks_and_partial_failure(monkeypatch):
    f = FakeClaude(fail_on="Gamma")
    monkeypatch.setattr(ca, "run", f)
    monkeypatch.setattr(ca, "check_network", lambda: None)
    monkeypatch.setattr(ca, "CHUNK", 2)
    data = ca.translate_aws(DETAIL, "all", [])
    assert len(f.calls) == 2                                            # 4文を2文ずつ
    m = data["_meta"]
    assert (m["sent"], m["failed"], m["chunks"]) == (2, 2, 2)
    assert data["description_ja"].startswith("訳:Alpha")                # 成功した分は使う
    assert data["params"][0]["ja"] == "Gamma unknown sentence."


def test_all_chunks_fail(monkeypatch):
    monkeypatch.setattr(ca, "run", FakeClaude(fail_on="unknown"))
    monkeypatch.setattr(ca, "check_network", lambda: None)
    with pytest.raises(ca.ClaudeError):
        ca.translate_aws(DETAIL, "all", [])


# ---------- API ----------
def localize(client, **body):
    payload = {"provider": "aws", "service": "ec2", "name": "describe-instances", "scope": "all", **body}
    return client.post("/api/claude/localize", data=json.dumps(payload),
                       headers={**WRITE, "Content-Type": "application/json"})


@pytest.fixture
def available(monkeypatch):
    monkeypatch.setattr(ca, "status", lambda: {"available": True, "reason": "", "reason_ja": "", "model": "sonnet"})


def test_localize_unavailable(client):
    r = localize(client, scope="dict")
    assert r.status_code == 503                                         # 未設定なら辞書だけでも日本語化しない


def test_localize_dictionary_only(client, available, fake):
    r = localize(client, scope="dict")
    assert r.status_code == 200 and fake.calls == []
    assert r.headers["Cache-Control"] == "no-store"
    assert r.get_json()["title_ja"] == "EC2インスタンスの一覧・詳細を表示する"


def test_localize_with_claude(client, available, fake):
    r = localize(client, scope="filled", filled=["--instance-ids"])
    assert r.status_code == 200
    names = [p["name"] for p in r.get_json()["params"]]
    assert "--instance-ids" in names and "--filters" not in names


def test_localize_errors(client, available, monkeypatch):
    assert localize(client, name="no-such-command").status_code == 404
    assert localize(client, provider="gcp").status_code == 404

    def fail(*a, **k):
        raise ca.ClaudeError("Claude Code failed: x")
    monkeypatch.setattr(ca, "translate_aws", fail)
    r = localize(client)
    assert r.status_code == 502 and r.get_json()["error"] == "Claude Code failed: x"
