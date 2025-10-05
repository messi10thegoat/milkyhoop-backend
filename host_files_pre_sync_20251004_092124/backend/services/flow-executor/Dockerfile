# ================================
# 🛠️ Builder Stage
# ================================
FROM golang:1.23.4 AS builder
WORKDIR /app
COPY backend/services/flow-executor/go.mod backend/services/flow-executor/go.sum ./
RUN go mod download
COPY backend/services/flow-executor .
# ✅ Copy flows dari root project
COPY flows ./flows
# ✅ Debug isi folder flows saat build
RUN echo "=== DEBUG CONTENTS /app/flows ===" && ls -R /app/flows || echo "Directory not found"
RUN CGO_ENABLED=0 GOOS=linux go build -a -installsuffix cgo -o flow-executor ./cmd/server


# ================================
# 🚀 Runtime Stage (Production)
# ================================
FROM gcr.io/distroless/static:nonroot AS prod

WORKDIR /

# ✅ Copy binary dan folder flows
COPY --from=builder /app/flow-executor /flow-executor
COPY --from=builder /app/flows /flows

EXPOSE 8088

ENTRYPOINT ["/flow-executor"]

# ================================
# 🐛 Debug Stage (with shell access)
# ================================
FROM debian:bullseye-slim AS debug

WORKDIR /

COPY --from=builder /app/flow-executor /flow-executor
COPY --from=builder /app/flows /flows

RUN apt-get update && apt-get install -y ca-certificates && apt-get clean

EXPOSE 8088
CMD ["/flow-executor"]
