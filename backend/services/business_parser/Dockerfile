# syntax=docker/dockerfile:1.4

# ================================
# 🛠️ Builder Stage
# ================================
FROM python:3.11-slim AS builder

WORKDIR /app
ENV PYTHONPATH="/app"

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl openssl libssl-dev libpq-dev gcc build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install --no-cache-dir poetry==1.8.2

# Copy pyproject.toml & poetry.lock
COPY backend/services/business_parser/pyproject.toml backend/services/business_parser/poetry.lock* ./

# Install Python dependencies via Poetry without virtualenv
RUN poetry config virtualenvs.create false && poetry install --no-root

# Install grpcio-tools for proto generation
RUN pip install --no-cache-dir grpcio-tools==1.63.0

# Copy source code
COPY backend/services/business_parser /app/backend/services/business_parser

# Copy protos & scripts
COPY protos /app/protos
COPY scripts /app/scripts

# Generate gRPC protobuf Python stubs
RUN chmod +x ./scripts/generate_protos_py.sh && ./scripts/generate_protos_py.sh

# ================================
# 🚀 Runtime Stage
# ================================
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH="/app"

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl openssl libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Install grpc_health_probe for healthcheck
RUN GRPC_HEALTH_PROBE_VERSION=v0.4.19 && \
    curl -Lo /usr/local/bin/grpc_health_probe \
    https://github.com/grpc-ecosystem/grpc-health-probe/releases/download/${GRPC_HEALTH_PROBE_VERSION}/grpc_health_probe-linux-amd64 && \
    chmod +x /usr/local/bin/grpc_health_probe

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY --from=builder /app/backend/services/business_parser /app/backend/services/business_parser
COPY --from=builder /app/protos /app/protos

# Set working directory to service
WORKDIR /app/backend/services/business_parser/app

# Expose gRPC port
EXPOSE 5018

# Health check
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD grpc_health_probe -addr=localhost:5018 || exit 1

# Run gRPC server
CMD ["python3", "grpc_server.py"]