# syntax=docker/dockerfile:1.4

# ================================
# Builder Stage
# ================================
FROM python:3.11-slim AS builder

WORKDIR /app
ENV PYTHONPATH="/app"

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY backend/services/action_validator/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/services/action_validator /app/backend/services/action_validator

# ================================
# Runtime Stage
# ================================
FROM python:3.11-slim AS prod

WORKDIR /app/backend/services/action_validator
ENV PYTHONPATH="/app/backend/services/action_validator"
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y curl tini wget && \
    wget -O /usr/local/bin/grpc_health_probe https://github.com/grpc-ecosystem/grpc-health-probe/releases/download/v0.4.14/grpc_health_probe-linux-amd64 && \
    chmod +x /usr/local/bin/grpc_health_probe && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /app /app

RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 5091

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
  CMD grpc_health_probe -addr=localhost:5091 || exit 1

ENTRYPOINT ["tini", "--"]
CMD ["python3", "-m", "app.grpc_server"]

FROM prod AS debug
USER root
