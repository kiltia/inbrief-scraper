FROM ghcr.io/astral-sh/uv:python3.13-bookworm AS builder

WORKDIR /app

ENV \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONOPTIMIZE=1

COPY shared /shared
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=scraper/uv.lock,target=uv.lock \
    --mount=type=bind,source=scraper/pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=scraper/.python-version,target=.python-version \
    uv sync --frozen --no-install-project --no-dev

FROM python:3.13-slim AS runtime

WORKDIR /app

ENV PATH=/app/bin:$PATH \
    PYTHONOPTIMIZE=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1

ARG RELEASE

ENV RELEASE=${RELEASE}

ENV PATH=/app/.venv/bin:$PATH

COPY --from=builder /app/.venv .venv
COPY scraper/src src

ENV PATH="$WD_NAME/.venv/bin/:$PATH"

ENTRYPOINT ["faststream", "run", "--app-dir", "src", "main:app", "--host", "0.0.0.0"]
