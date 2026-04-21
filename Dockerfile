# Vocence Subnet - Validator image
# Build: docker build -t vocence-validator .
# Run: docker-compose up -d (see docker-compose.yml)

# Stage 1: build (needs gcc for netifaces / chutes)
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir --upgrade pip uv \
    && uv sync --no-dev --no-install-project

COPY . .
RUN uv sync --no-dev

# Stage 2: runtime (no build tools)
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/vocence /app/vocence
COPY --from=builder /app/pyproject.toml /app/
COPY --from=builder /app/uv.lock /app/

RUN useradd -m -u 1000 validator \
    && chown -R validator:validator /app \
    && mkdir -p /home/validator/.bittensor \
    && chown validator:validator /home/validator/.bittensor
USER validator

ENV PYTHONUNBUFFERED=1
ENV NETWORK=finney
ENV NETUID=78
ENV LOG_LEVEL=INFO
ENV PATH="/app/.venv/bin:$PATH"

# Single process: sample generation + weight setting (same as vocence serve)
CMD ["vocence", "serve"]
