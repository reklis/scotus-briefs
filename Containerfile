FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58 AS builder
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254
RUN groupadd --system --gid 10001 ragchew \
    && useradd --system --uid 10001 --gid 10001 --home-dir /app ragchew
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY config ./config
COPY resources ./resources
COPY templates ./templates
COPY static ./static
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
# This image is local/migration tooling, not a production web server. Pages receives
# only the validated static candidate produced by this command.
CMD ["ragchew-scotus-static", "--help"]
