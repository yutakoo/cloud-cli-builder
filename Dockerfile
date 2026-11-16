# 元にするイメージとバージョンは固定しています（作り直しても同じ中身になるように）。
# 更新するときは、次のコマンドで新しい digest を調べて書き換えてください。
#   docker buildx imagetools inspect node:22-bookworm-slim
#   docker buildx imagetools inspect python:3.12-slim-bookworm
#   npm view @anthropic-ai/claude-code version
#
# ステージ: claude（Claude Code を取得）→ base（共通部分）→ test（テスト用）→ app（本番。最後なので既定でこれが作られる）

# ---- Claude Code（claude コマンド）を取得するステージ ----
# npm パッケージに同梱されているネイティブ版の実行ファイルだけを取り出します（実行時に Node.js は不要）
FROM node:22-bookworm-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee AS claude
ARG CLAUDE_CODE_VERSION=2.1.325
RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && cp "$(npm root -g)/@anthropic-ai/claude-code/bin/claude.exe" /claude \
 && /claude --version

# ---- 共通部分（Python とアプリ） ----
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    TZ=Asia/Tokyo

WORKDIR /srv

COPY requirements.txt .
# botocore は AWS CLI と同じサービス定義。バージョンは requirements.txt で固定
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# ---- テスト（docker compose run --rm test）。本番のイメージには入らない ----
FROM base AS test
# JS のテスト（node --test）用に Node.js の実行ファイルだけを使う
COPY --from=claude /usr/local/bin/node /usr/local/bin/node
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY tests ./tests
CMD ["sh", "-c", "python -m pytest -q tests && node --test tests/js/*.test.js"]

# ---- 本番 ----
FROM base AS app
COPY --from=claude /claude /usr/local/bin/claude

RUN useradd -m -u 1000 app && mkdir -p /data && chown app:app /data
USER app
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8080/healthz')"

# Claude の処理は数十秒〜数分かかるため、タイムアウトを長めにしています
CMD ["gunicorn", "-w", "2", "--threads", "4", "--timeout", "600", "-b", "0.0.0.0:8080", "--preload", "app.main:app"]
