# syntax=docker/dockerfile:1.4

# ================================
# Builder Stage
# ================================
FROM python:3.11-slim AS builder

WORKDIR /app
ENV PYTHONPATH="/app"

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/services/action_planner/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY backend/services/action_planner /app/backend/services/action_planner

# ================================
# Runtime Stage
# ================================
FROM python:3.11-slim AS prod

# WORKDIR set to service root so "python -m app.grpc_server" resolves correctly
WORKDIR /app/backend/services/action_planner
ENV PYTHONPATH="/app/backend/services/action_planner"
ENV PYTHONUNBUFFERED=1

# Install runtime dependencies + grpc_health_probe
RUN apt-get update && apt-get install -y curl tini wget && \
    wget -O /usr/local/bin/grpc_health_probe https://github.com/grpc-ecosystem/grpc-health-probe/releases/download/v0.4.14/grpc_health_probe-linux-amd64 && \
    chmod +x /usr/local/bin/grpc_health_probe && \
    rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages

# Copy source code
COPY --from=builder /app /app

# Create non-root user
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 5090

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
  CMD grpc_health_probe -addr=localhost:5090 || exit 1

ENTRYPOINT ["tini", "--"]

CMD ["python3", "-m", "app.grpc_server"]

FROM prod AS debug
USER root
