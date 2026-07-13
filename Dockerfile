# Chance Time — paper-first bot image (no secrets baked in)
FROM python:3.12-slim-bookworm

WORKDIR /app

# System deps for scientific stack if needed later
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY config ./config

# Core + dashboard (status UI); mount .env + secrets at runtime
RUN uv sync --frozen --no-dev --extra dashboard \
    && mkdir -p /app/data /app/logs /app/llm_cache /app/secrets

ENV PATH="/app/.venv/bin:$PATH"
ENV PAPER_MODE=true
ENV PYTHONUNBUFFERED=1

VOLUME ["/app/data", "/app/logs", "/app/llm_cache", "/app/secrets"]

# Default: one-shot paper poll (override CMD for long-run or dashboard)
CMD ["chancetime", "run", "--once"]
