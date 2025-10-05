# Multi-stage build untuk ragllm_service dengan requirements.txt fallback
FROM python:3.11-slim as builder

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl git nodejs npm \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python dependencies using requirements.txt
COPY backend/services/ragllm_service/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy service code
COPY backend/services/ragllm_service /app/backend/services/ragllm_service
COPY protos /app/protos
COPY scripts /app/scripts

# Generate proto files
RUN chmod +x ./scripts/generate_protos_py.sh && ./scripts/generate_protos_py.sh

# Copy and generate Prisma
COPY database/schemas/global_schema.prisma /app/database/schemas/global_schema.prisma
RUN pip install --no-cache-dir prisma==0.15.0
RUN python3 -m prisma generate --schema=/app/database/schemas/global_schema.prisma

# Production stage
FROM python:3.11-slim as prod

WORKDIR /app

RUN apt-get update && apt-get install -y curl tini wget && \
    wget https://github.com/grpc-ecosystem/grpc-health-probe/releases/download/v0.4.19/grpc_health_probe-linux-amd64 -O /usr/local/bin/grpc_health_probe && \
    chmod +x /usr/local/bin/grpc_health_probe && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /app /app

RUN useradd -m appuser && chown -R appuser:appuser /app

USER appuser
ENV PYTHONPATH=.:backend/api_gateway/libs:backend/services/ragllm_service
EXPOSE 5000
ENTRYPOINT ["tini", "--"]
CMD ["python3", "/app/backend/services/ragllm_service/app/grpc_server.py"]
