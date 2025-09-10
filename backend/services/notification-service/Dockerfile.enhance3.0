# ================================
# 🛠️ Builder Stage
# ================================
FROM golang:1.22 AS builder

WORKDIR /app

# Install protoc + alat pendukung (opsional future use)
RUN apt-get update && apt-get install -y --no-install-recommends \
    unzip curl git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Install buf CLI (opsional, future proto)
RUN curl -sSL https://github.com/bufbuild/buf/releases/download/v1.28.1/buf-Linux-x86_64 -o /usr/local/bin/buf \
 && chmod +x /usr/local/bin/buf

# ✅ Install grpc_health_probe (for healthcheck binary)
RUN curl -sSL https://github.com/grpc-ecosystem/grpc-health-probe/releases/download/v0.4.11/grpc_health_probe-linux-amd64 \
    -o /usr/local/bin/grpc_health_probe && \
    chmod +x /usr/local/bin/grpc_health_probe

# Copy Go mod files & download deps
COPY ./go.mod ./go.sum ./
RUN go mod download

# Copy seluruh source code
COPY . .

# Build binary utama
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /app/bin/notification ./cmd/server


# ================================
# 🚀 Runtime Stage
# ================================
FROM gcr.io/distroless/static:nonroot AS prod

WORKDIR /app

# Copy binary hasil build
COPY --from=builder /app/bin/notification /app/notification

# ✅ Copy grpc_health_probe untuk healthcheck
COPY --from=builder /usr/local/bin/grpc_health_probe /usr/local/bin/grpc_health_probe

# Jalankan sebagai user non-root
USER nonroot:nonroot

EXPOSE 5005
ENTRYPOINT ["/app/notification"]
