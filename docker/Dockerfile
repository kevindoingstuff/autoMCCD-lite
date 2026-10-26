# The api image. Build from the repo root:
#   docker build -f docker/Dockerfile -t automccd-lite-api .
FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.6.14 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY prompts ./prompts
COPY config ./config
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm
RUN useradd --system --uid 10001 --create-home automccd \
    && apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build --chown=automccd:automccd /app /app
# data/ is bind-mounted by compose so the code cache the api writes lands on the host.
RUN mkdir -p /app/data && chown automccd:automccd /app/data
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER automccd
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=6 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1
CMD ["python", "-m", "automccd_lite.api"]
