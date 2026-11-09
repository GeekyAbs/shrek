# syntax=docker/dockerfile:1
# shrek-bot image: one container, one owner, one SQLite file in /data.
# Config = plain env vars (see .env.example); SHREK_HOME=/data keeps the
# ledger AND the .env (with GEMINI_API_KEY/GEMINI_MODEL) on the volume.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# install deps first for layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen --no-install-project

COPY src ./src
COPY README.md ./
RUN uv sync --no-dev --frozen --no-install-project && \
    uv pip install --no-cache --system .

# ledger + BYOK key volume (config.toml lives at $SHREK_HOME too)
ENV SHREK_DB_PATH=/data/ledger.db
ENV SHREK_HOME=/data
VOLUME /data

ENTRYPOINT ["shrek-bot"]
