# syntax=docker/dockerfile:1.4
# Image dasar dikunci per DIGEST (pin-freeze 24 Sep 2026): tag python:3.11-slim
# bergerak (patch Python + rilis Debian -> versi pango/cairo = render PDF).
# Digest ini = Python 3.11.15, Debian 13 trixie -- sama dengan image prod.
FROM python:3.11.15-slim-trixie@sha256:90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff AS builder

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
# requirements.txt = kunci LENGKAP (termasuk turunan). --no-deps: resolver tak
# boleh menarik apa pun di luar daftar; pip check membuktikan daftarnya utuh.
# Penjaga: tests/unit/test_requirements_kunci.py
RUN pip install pip==26.1.2 && \
    pip install --no-cache-dir --no-deps -r requirements.txt && \
    pip check

# Copy database schema for Prisma
COPY database/schemas/global_schema.prisma /app/database/schemas/global_schema.prisma

# Install and setup Prisma
RUN npm install prisma@5.17.0 @prisma/client@5.17.0 && \
    pip install --no-cache-dir --no-deps prisma==0.14.0 && \
    pip check

# Generate Prisma client
RUN npx prisma generate --schema=/app/database/schemas/global_schema.prisma
RUN python3 -m prisma generate --schema=/app/database/schemas/global_schema.prisma

# Copy Prisma generated files to libs
RUN mkdir -p /app/backend/api_gateway/libs/milkyhoop_prisma && \
    cp -r /root/.cache/prisma-python/binary/5.17.0/* /app/backend/api_gateway/libs/milkyhoop_prisma/ 2>/dev/null || true

# Production stage
FROM python:3.11.15-slim-trixie@sha256:90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff AS prod

# Install runtime dependencies (including WeasyPrint)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    tini \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-xlib-2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-dejavu-core \
    # SENGAJA TIDAK ADA `fonts-liberation` di sini. Faktur template B memakai
    # Liberation Sans dari BERKAS DI REPO
    # (app/templates/pdf/fonts/, terdaftar lewat @font-face di invoice_b.css),
    # bukan dari paket sistem. Alasannya bukan kemudahan deploy: font sistem
    # membuat hasil cetak bergantung pada image yang KEBETULAN punya paket
    # itu -- dibangun ulang di mesin lain tanpa paketnya, faktur tercetak
    # DejaVu tanpa satu galat pun. Terbukti: dirender di image LAMA yang nol
    # paket liberation, PDF-nya tetap menanam Liberation-Sans; dan saat kedua
    # berkas .ttf disembunyikan, ia jatuh ke DejaVu-Sans (kontrol merah).
    # Efek sampingnya: perubahan font tak lagi menuntut rilis kelas IMAGE.
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy installed packages from builder -- GANTI, bukan tumpuk. COPY hanya
# menimpa: paket bawaan image dasar yang versinya diturunkan di builder
# (terukur: packaging 26.3 -> kunci 26.2) meninggalkan dist-info GANDA, dan
# metadata melaporkan versi yang kodenya tak terpasang. Builder memakai image
# dasar yang sama (digest), jadi isinya = dasar + kunci, lengkap.
RUN rm -rf /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Create directory structure exactly like working container
RUN mkdir -p /app/backend/api_gateway/libs

# Copy application files
COPY backend/api_gateway /app/backend/api_gateway

# Copy accounting kernel for reports (required for AccountingFacade)
RUN mkdir -p /app/backend/services
COPY backend/services/accounting_kernel /app/backend/services/accounting_kernel

# Copy working milkyhoop_protos from production
COPY backend/api_gateway/libs/milkyhoop_protos /app/backend/api_gateway/libs/milkyhoop_protos

# Copy protos for reference
COPY protos /app/protos

# Copy health probe
COPY grpc_health_probe /usr/local/bin/grpc_health_probe
# Validate generated Python code syntax
RUN find /app/backend/api_gateway -name "*.py" -exec python3 -m py_compile {} \; || { echo "Python syntax validation failed"; exit 1; }

# Clean pyc from image layer — volume-mounted source must not compete with stale bytecode
RUN find /app -name "*.pyc" -delete && find /app -name "__pycache__" -type d -empty -delete

RUN chmod +x /usr/local/bin/grpc_health_probe

# Set Python path (includes accounting_kernel for reports)
ENV PYTHONPATH=/app:/app/backend/api_gateway/app:/app/backend/api_gateway/libs:/app/backend/services
ENV PYTHONDONTWRITEBYTECODE=1

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1

# Expose port
EXPOSE 8000

# Use tini as PID 1 and start application EXACTLY like working container
ENTRYPOINT ["tini", "--"]
CMD ["python", "-B", "-m", "uvicorn", "backend.api_gateway.app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Enhanced Cross-Service Validation
RUN echo "=== ENHANCED BUILD VALIDATION ===" && \
    # 1. Protobuf version verification
    python3 -c "import grpc; print(f'gRPC version: {grpc.__version__}')" && \
    # 2. Import validation for critical modules
    python3 -c "from backend.api_gateway.libs.milkyhoop_prisma import Prisma; print('Prisma: OK')" && \
    # 3. Accounting kernel validation
    python3 -c "from accounting_kernel.integration.facade import AccountingFacade; print('AccountingFacade: OK')" && \
    # 4. Generated protobuf validation
    find /app -name "*_pb2_grpc.py" -exec python3 -m py_compile {} \; && \
    echo "✅ All enhanced validations passed"
