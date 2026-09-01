FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 ragchew \
    && useradd --system --uid 10001 --gid 10001 --home-dir /app ragchew
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY config ./config
COPY resources ./resources
COPY templates ./templates
COPY static ./static
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8080 8081
CMD ["ragchew-api"]
