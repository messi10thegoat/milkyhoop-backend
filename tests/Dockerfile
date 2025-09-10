# ================================
# 🏗️ STAGE 1 — Builder
# ================================
FROM python:3.12-slim AS builder

WORKDIR /app
ENV PYTHONPATH="/app"

# System dependencies
RUN apt-get update && apt-get install -y \
    build-essential curl libpq-dev nodejs npm \
 && rm -rf /var/lib/apt/lists/*

# Install Poetry & Prisma CLI JS
RUN pip install --upgrade pip poetry==1.8.2 && npm install -g prisma@5.17.0 @prisma/client@5.17.0

# Copy schema & env
COPY database/schemas/global_schema.prisma ./schema.prisma
COPY backend/services/auth-service/pyproject.toml backend/services/auth-service/poetry.lock ./
COPY backend/api_gateway/.env_template .env

# Install Python dependencies
RUN poetry config virtualenvs.create false && poetry install --no-root
RUN pip install prisma==0.15.0 pytest pytest-asyncio httpx

# Copy source code
COPY backend ./backend
COPY protos ./protos
COPY tests ./tests
COPY common ./common
COPY backend/api_gateway/libs/milkyhoop_prisma /app/backend/api_gateway/libs/milkyhoop_prisma

# Generate Prisma binary engine (JS)
RUN DATABASE_URL=$(grep DATABASE_URL .env | cut -d '=' -f2-) npx prisma generate --schema=schema.prisma

# Salin query-engine ke path tetap
RUN find /app -type f -name "query-engine-*" -exec cp {} /app/prisma-query-engine-debian-openssl-3.0.x \;

# Generate Prisma Python client
RUN DATABASE_URL=$(grep DATABASE_URL .env | cut -d '=' -f2-) prisma generate --schema=schema.prisma


# ================================
# ✅ STAGE 2 — Runtime
# ================================
FROM python:3.12-slim AS prod

WORKDIR /app
ENV PYTHONPATH="/app:/app/backend/api_gateway/libs:/app/backend/services/auth-service"
ENV PRISMA_QUERY_ENGINE_BINARY="/app/prisma-query-engine-debian-openssl-3.0.x"

# System dependencies
RUN apt-get update && apt-get install -y libpq-dev gcc && rm -rf /var/lib/apt/lists/*

# ✅ Install Poetry lagi di runtime
RUN pip install --no-cache-dir poetry==1.8.2

# ✅ Copy file hasil builder
COPY --from=builder /app /app
COPY --from=builder /app/prisma-query-engine-debian-openssl-3.0.x /app/prisma-query-engine-debian-openssl-3.0.x

# ✅ Install deps pakai poetry (no venv)
RUN poetry config virtualenvs.create false && poetry install --no-root

# ✅ Jalankan test
ENTRYPOINT ["sh", "-c", "pytest --cov --cov-report=term tests"]
