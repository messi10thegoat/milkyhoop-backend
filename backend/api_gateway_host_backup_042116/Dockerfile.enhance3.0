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
COPY backend/api_gateway/requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt --target /app/site-packages && \
    pip install --no-cache-dir grpcio-tools==1.63.0
RUN pip install --no-cache-dir uvicorn==0.29.0

# ✅ Install Prisma CLI (JS) & Prisma Python
RUN npm install prisma@5.17.0 @prisma/client@5.17.0 && \
    pip install --no-cache-dir prisma==0.15.0

# ✅ Copy Prisma schema
COPY database/schemas/global_schema.prisma /app/database/schemas/global_schema.prisma

# ✅ Generate Prisma JS client + binary engine
RUN npx prisma generate --schema=/app/database/schemas/global_schema.prisma

# ✅ Generate Prisma Python client
RUN python3 -m prisma generate --schema=/app/database/schemas/global_schema.prisma

# ✅ Copy Prisma binary engine (query-engine) ke path tetap
RUN mkdir -p /app/libs/milkyhoop_prisma/engine && \
    find /app -type f -name "query-engine-*" \
    -exec cp {} /app/libs/milkyhoop_prisma/engine/query-engine-debian-openssl-3.0.x \; && \
    chmod +x /app/libs/milkyhoop_prisma/engine/query-engine-debian-openssl-3.0.x

# ✅ Copy Prisma Python client output ke path final runtime
RUN mkdir -p /app/backend/api_gateway/libs/milkyhoop_prisma && \
    cp -r /root/.cache/prisma-python/generated/* /app/backend/api_gateway/libs/milkyhoop_prisma || echo "No Prisma Python client found!"

# ✅ Copy service source code (relatif ke konteks build: backend/api_gateway/)
COPY backend/api_gateway /app/backend/api_gateway

# ✅ Copy tests
COPY backend/api_gateway/tests /app/backend/api_gateway/tests



# ✅ Copy protos untuk generate stub
COPY protos /app/protos

# ✅ Generate gRPC Python stubs jika ada skrip generate
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

# Set PYTHONPATH mencakup app root dan app backend api_gateway
ENV PYTHONPATH="/app:/app/backend/api_gateway/app:/app/backend/api_gateway/libs"
ENV PRISMA_QUERY_ENGINE_BINARY="/app/libs/milkyhoop_prisma/engine/query-engine-debian-openssl-3.0.x"
ENV PYTHONUNBUFFERED=1

# Install minimal runtime dependencies + SSL certs
RUN apt-get update && apt-get install -y --no-install-recommends curl tini ca-certificates && \
    apt-get clean && rm -rf /var/lib/apt/lists/*


# Install uvicorn runtime
RUN pip install --no-cache-dir uvicorn==0.29.0

# Copy site-packages & source code dari builder
COPY --from=builder /app/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /app /app

# Buat user non-root
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# Expose port uvicorn default
EXPOSE 8000




# Entrypoint tini (pid 1) dan start FastAPI uvicorn
ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "backend.api_gateway.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
