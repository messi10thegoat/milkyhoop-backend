#!/bin/bash
set -e

# Absolute path to protos directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROTO_DIR="$SCRIPT_DIR/../protos"
ROOT_DIR="$SCRIPT_DIR/.."

echo "🐹 [GOLANG] Scanning $PROTO_DIR for .proto files..."

for PROTO_FILE in $(find "$PROTO_DIR" -name "*.proto"); do
  RELATIVE_PATH="${PROTO_FILE#$PROTO_DIR/}"          # e.g. template_service.proto
  SERVICE_NAME=$(basename "$RELATIVE_PATH" .proto | cut -d'_' -f1)   # e.g. template
  TARGET_SERVICE=$(find "$ROOT_DIR/backend/services" -type d -name "$SERVICE_NAME-service-golang" | head -n 1)

  if [[ -z "$TARGET_SERVICE" ]]; then
    echo "⚠️  No matching service found for $SERVICE_NAME. Skipping."
    continue
  fi

  OUT_DIR="$TARGET_SERVICE/internal/delivery/pb/$SERVICE_NAME"
  echo "🔧 Generating Go proto for $RELATIVE_PATH → $OUT_DIR"

  mkdir -p "$OUT_DIR"

  protoc \
    --proto_path="$PROTO_DIR" \
    --go_out="$OUT_DIR" \
    --go-grpc_out="$OUT_DIR" \
    --go_opt=paths=source_relative \
    --go-grpc_opt=paths=source_relative \
    "$PROTO_FILE"
done

echo "✅ [GOLANG] All .proto files generated."
