FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends osmium-tool postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY catalog ./catalog
COPY config ./config
COPY db ./db
COPY frontend/data ./frontend/data
COPY scripts ./scripts

CMD ["python", "--version"]
