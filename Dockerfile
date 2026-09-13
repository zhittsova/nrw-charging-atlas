FROM node:22-alpine AS frontend-build

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json frontend/tsconfig.json frontend/vite.config.ts ./
RUN npm ci
COPY frontend/index.html frontend/style.css ./
COPY frontend/src ./src
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS etl

ENV DEBIAN_FRONTEND=noninteractive \
    PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends osmium-tool postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev
COPY catalog ./catalog
COPY config ./config
COPY db ./db
COPY scripts ./scripts

CMD ["python", "--version"]

FROM nginx:1.27-alpine AS frontend

COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-build /app/dist /usr/share/nginx/html
