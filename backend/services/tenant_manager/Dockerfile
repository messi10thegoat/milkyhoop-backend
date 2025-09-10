# syntax=docker/dockerfile:1.4

# =============================
# 🛠️ Builder Stage
# =============================
FROM python:3.11-slim AS builder

WORKDIR /app
ENV PYTHONPATH="/app"

# ✅ Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential curl git nodejs npm \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# ✅ Install Python dependencies
COPY backend/services/tenant_manager/requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt --target /app/site-packages && \
    pip install --no-cache-dir grpcio-tools==1.63.0

# ✅ Install Prisma CLI (JS) & Prisma Python
RUN npm install prisma@5.17.0 @prisma/client@5.17.0 && \
    pip install --no-cache-dir prisma==0.15.0

# ✅ Copy Prisma schema
COPY database/schemas/global_schema.prisma /app/database/schemas/global_schema.prisma

# ✅ Generate Prisma Python client
RUN python3 -m prisma generate --schema=/app/database/schemas/global_schema.prisma

# ✅ Copy Prisma binary engine (query-engine)
RUN find /app -type f -name "query-engine-debian-openssl-3.0.x" \
    -exec cp {} /app/prisma-query-engine-debian-openssl-3.0.x \;

# ✅ Copy Prisma Python client output ke path final
RUN mkdir -p /app/backend/api_gateway/libs/milkyhoop_prisma && \
    cp -r /root/.cache/prisma-python/generated/* /app/backend/api_gateway/libs/milkyhoop_prisma || echo "No Prisma Python client found!"

# ✅ Copy service source code (relatif ke konteks build: backend/services/<service-name>/)
COPY . .

# ✅ Generate gRPC Python stubs
RUN if [ -f ./scripts/generate_protos_py.sh ]; then \
    chmod +x ./scripts/generate_protos_py.sh && ./scripts/generate_protos_py.sh; \
    fi

# ✅ Patch import path agar backend.api_gateway.libs tersedia di runtime
RUN ln -s /app /backend && \
    mkdir -p /app/backend/api_gateway && \
    ln -s /app/backend/api_gateway/libs /app/backend/api_gateway/libs

# =============================
# ✅ Runtime Stage
# =============================
FROM python:3.11-slim AS prod

WORKDIR /app
ENV PYTHONPATH="/app:/app/common:/app/protos:/app/backend/api_gateway/libs:/app/backend/services/tenant_manager"
ENV PRISMA_QUERY_ENGINE_BINARY="/app/prisma-query-engine-debian-openssl-3.0.x"
ENV PYTHONUNBUFFERED=1

# ✅ Install minimal runtime dependencies
RUN apt-get update && apt-get install -y curl tini && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# ✅ Copy site-packages & source code
COPY --from=builder /app/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /app /app

# ✅ Non-root user
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# ✅ Expose default gRPC port (bisa override via .env)
EXPOSE 5009

# ✅ Entrypoint
ENTRYPOINT ["tini", "--"]

# ✅ Start gRPC server
CMD ["python3", "/app/backend/services/tenant_manager/app/grpc_server.py"]



# ----------------------------------------
# 🔍 Debug Stage (opsional root shell)
# ----------------------------------------
FROM prod AS debug
USER root
