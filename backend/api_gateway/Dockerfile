# syntax=docker/dockerfile:1.4
FROM python:3.11-slim AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    nodejs \
    npm \
    tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install Python requirements
COPY backend/api_gateway/requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy database schema for Prisma
COPY database/schemas/global_schema.prisma /app/database/schemas/global_schema.prisma

# Install and setup Prisma
RUN npm install prisma@5.17.0 @prisma/client@5.17.0 && \
    pip install --no-cache-dir prisma==0.14.0

# Generate Prisma client
RUN npx prisma generate --schema=/app/database/schemas/global_schema.prisma
RUN python3 -m prisma generate --schema=/app/database/schemas/global_schema.prisma

# Copy Prisma generated files to libs
RUN mkdir -p /app/backend/api_gateway/libs/milkyhoop_prisma && \
    cp -r /root/.cache/prisma-python/binary/5.17.0/* /app/backend/api_gateway/libs/milkyhoop_prisma/ 2>/dev/null || true

# Production stage
FROM python:3.11-slim AS prod

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    curl \
    tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Create directory structure exactly like working container
RUN mkdir -p /app/backend/api_gateway/libs

# Copy application files
COPY backend/api_gateway /app/backend/api_gateway

# Copy working milkyhoop_protos from production
COPY backend/api_gateway/libs/milkyhoop_protos /app/backend/api_gateway/libs/milkyhoop_protos

# Copy protos for reference
COPY protos /app/protos

# Copy health probe
COPY grpc_health_probe /usr/local/bin/grpc_health_probe
# Validate generated Python code syntax
RUN find /app/backend/api_gateway -name "*.py" -exec python3 -m py_compile {} \; || { echo "Python syntax validation failed"; exit 1; }

RUN chmod +x /usr/local/bin/grpc_health_probe

# Set Python path EXACTLY like working container
ENV PYTHONPATH=/app:/app/backend/api_gateway/app:/app/backend/api_gateway/libs

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1

# Expose port
EXPOSE 8000

# Use tini as PID 1 and start application EXACTLY like working container
ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "backend.api_gateway.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
