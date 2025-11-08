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

# Install Node.js & Prisma CLI JS
RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    npm install -g prisma@5.17.0 && \
    rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install --no-cache-dir poetry==1.8.2

# Copy pyproject.toml & poetry.lock spesifik module
COPY backend/services/complaint_service/pyproject.toml backend/services/complaint_service/poetry.lock* ./

# Install Python dependencies via Poetry tanpa buat virtualenv
RUN poetry config virtualenvs.create false && poetry install --no-root

# Copy source code
COPY backend/services/complaint_service /app/backend/services/complaint_service

# Copy protos & scripts
COPY protos /app/protos
COPY scripts /app/scripts

# Generate gRPC protobuf Python stubs
RUN chmod +x ./scripts/generate_protos_py.sh && ./scripts/generate_protos_py.sh

# Copy Prisma schema
COPY database/schemas/global_schema.prisma /app/database/schemas/global_schema.prisma

# Install Prisma Python client
RUN pip install --no-cache-dir prisma==0.15.0

# Generate Prisma Python client
RUN python3 -m prisma generate --schema=/app/database/schemas/global_schema.prisma

# Copy Prisma binary engine
RUN find /app -type f -name "query-engine-*" -exec cp {} /app/prisma-query-engine-debian-openssl-3.0.x \;

# ================================
# ✅ Runtime Stage
# ================================
FROM python:3.11-slim AS prod

WORKDIR /app
ENV PYTHONPATH=".:backend/api_gateway/libs:backend/services/complaint_service"
ENV PRISMA_QUERY_ENGINE_BINARY="/app/prisma-query-engine-debian-openssl-3.0.x"
ENV PYTHONUNBUFFERED=1

# Install runtime dependencies + grpc_health_probe
RUN apt-get update && apt-get install -y curl tini wget && \
    wget -O /usr/local/bin/grpc_health_probe https://github.com/grpc-ecosystem/grpc-health-probe/releases/download/v0.4.14/grpc_health_probe-linux-amd64 && \
    chmod +x /usr/local/bin/grpc_health_probe && \
    rm -rf /var/lib/apt/lists/*

# Copy installed Python packages dari builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages

# Copy source code
COPY --from=builder /app /app

# Buat user non-root
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 5009

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
  CMD grpc_health_probe -addr=localhost:5009 || exit 1

ENTRYPOINT ["tini", "--"]

CMD ["python3", "/app/backend/services/complaint_service/app/grpc_server.py"]

# ----------------------------------------
# 🔍 Debug Stage (opsional root shell)
# ----------------------------------------
FROM prod AS debug
USER root
