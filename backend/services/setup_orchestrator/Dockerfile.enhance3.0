# ======= Builder Stage =======
FROM python:3.11-slim AS builder

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential curl git \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# ======= Final Stage =======
FROM python:3.11-slim AS prod

WORKDIR /app

# Metadata untuk CI/CD & monitoring
LABEL maintainer="MilkyHoop Team" \
      version="1.0" \
      description="Business Service with SQLAlchemy & FastAPI"

# Set PYTHONPATH jika ada common helper
ENV PYTHONPATH="${PYTHONPATH}:/app"

# Copy Python packages & binary dari builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy source code
COPY --from=builder /app /app

# User non-root dan izin
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# Expose port FastAPI
EXPOSE 50055

# Healthcheck
HEALTHCHECK --interval=30s --timeout=3s \
  CMD curl -f http://localhost:50055/health || exit 1

# Jalankan server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "50055"]
