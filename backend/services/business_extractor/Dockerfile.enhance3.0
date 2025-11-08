# syntax=docker/dockerfile:1.4

# =============================
# 🛠️ Builder Stage
# =============================
FROM python:3.11-slim AS builder

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential curl git \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/services/template-service-python/requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt --target /app/site-packages && \
    pip install --no-cache-dir grpcio-tools==1.63.0

# Copy service source code
COPY backend/services/template-service-python /app

# Copy protos
COPY protos /app/protos
RUN touch /app/protos/__init__.py

# Generate gRPC Python stubs
COPY scripts/generate_protos_py.sh /app/scripts/generate_protos_py.sh
RUN chmod +x /app/scripts/generate_protos_py.sh && /app/scripts/generate_protos_py.sh

# =============================
# ✅ Runtime Stage
# =============================
FROM python:3.11-slim AS prod

WORKDIR /app
ENV PYTHONPATH="/app"
ENV PYTHONUNBUFFERED=1

# Install minimal runtime dependencies
RUN apt-get update && apt-get install -y curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy pre-installed Python packages and app code
COPY --from=builder /app/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /app /app

# Create non-root user
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# Default gRPC port (configurable via ENV)
EXPOSE 5009

# Start service
CMD ["python3", "app/grpc_server.py"]

# ----------------------------------------
# 🔍 Debug Stage (opsional root shell)
# ----------------------------------------
FROM prod AS debug
USER root
