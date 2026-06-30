# Multi-platform build: supports linux/amd64 and linux/arm64.
# python:3.12-slim and the uv image both publish multi-platform manifests, so
# buildx resolves the correct variant per target architecture automatically.
# Build command: see scripts/cf-deploy.sh or .github/workflows/docker.yml

# -- Stage 1: install Python dependencies with uv -----------------------------
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-install-project --no-dev

# -- Stage 2: final runtime image ---------------------------------------------
FROM python:3.12-slim AS final

WORKDIR /app

# Python virtualenv from builder
COPY --from=builder /app/.venv /app/.venv

# Entrypoint (flat copy into /app so ENTRYPOINT path matches)
COPY docker/entrypoint.sh ./entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Application source
COPY server.py      ./
COPY exceptions.py  ./
COPY confluence/    ./confluence/
COPY models/        ./models/

# Baked-in defaults (all overridable via env vars; SERVER_PORT set in entrypoint.sh)
ENV SERVER_TRANSPORT=streamable-http \
    SERVER_HOST=0.0.0.0 \
    JSON_LOGS=true \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 CMD /app/.venv/bin/python -c "import urllib.request, os; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('SERVER_PORT', os.getenv('PORT', '8765')) + '/health')"

ENTRYPOINT ["/app/entrypoint.sh"]
