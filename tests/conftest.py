"""テストの共通設定。

アプリは読み込んだ時点でお気に入りの DB を作り、botocore の索引を作るので、
その前に保存先（DATA_DIR）をテスト用の一時ディレクトリに向けておく。
Claude の認証情報は消しておき、実際の Claude は呼ばない。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="ccb-test-")
for _k in ("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_ASSUME_AUTH", "ALLOWED_HOSTS", "HTTPS_PROXY", "https_proxy",
           "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from app import favorites, ja_docs, main  # noqa: E402

WRITE = {"X-CCB": "1"}   # 書き込み系の API に必要なヘッダー


@pytest.fixture
def client():
    return main.app.test_client()


@pytest.fixture(autouse=True)
def _clean_favorites():
    with favorites._conn() as c:
        c.execute("DELETE FROM favorites")
    yield


@pytest.fixture(autouse=True)
def learned(tmp_path, monkeypatch):
    """学習辞書はテストごとに空のファイルを使う。"""
    path = tmp_path / "ja_learned.json"
    monkeypatch.setattr(ja_docs, "LEARNED", path)
    monkeypatch.setattr(ja_docs, "LEARN", True)
    monkeypatch.setattr(ja_docs, "_cache", None)
    return path
