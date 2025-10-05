#!/bin/bash
set -e

PROTO_FILE="protos/ragcrud_service.proto"
CLIENT_OUT_DIR="backend/api_gateway/libs/milkyhoop_protos"
SERVER_OUT_DIR="backend/services/ragcrud_service/app"

echo "📦 Generating RAGCRUD proto stub only"

# === Bersihkan stub lama RAGCRUD ===
echo "🧹 Cleaning old RAGCRUD stubs..."
rm -f "$CLIENT_OUT_DIR/ragcrud_service_pb2.py" "$CLIENT_OUT_DIR/ragcrud_service_pb2_grpc.py"
rm -f "$SERVER_OUT_DIR/ragcrud_service_pb2.py" "$SERVER_OUT_DIR/ragcrud_service_pb2_grpc.py"

# === Pastikan direktori ada ===
mkdir -p "$CLIENT_OUT_DIR"
touch "$CLIENT_OUT_DIR/__init__.py"
mkdir -p "$SERVER_OUT_DIR"

# === Generate ke CLIENT ===
echo "🛠️ Generating to CLIENT: $CLIENT_OUT_DIR"
python3 -m grpc_tools.protoc \
  -I protos \
  --python_out="$CLIENT_OUT_DIR" \
  --grpc_python_out="$CLIENT_OUT_DIR" \
  "$PROTO_FILE"

# === Generate ke SERVER ===
echo "🛠️ Generating to SERVER: $SERVER_OUT_DIR"
python3 -m grpc_tools.protoc \
  -I protos \
  --python_out="$SERVER_OUT_DIR" \
  --grpc_python_out="$SERVER_OUT_DIR" \
  "$PROTO_FILE"

# === Patch import di client ===
echo "🩹 Patching import statements in CLIENT stub..."
sed -i 's/^import \(.*_pb2\)/from milkyhoop_protos import \1/' "$CLIENT_OUT_DIR"/*.py

echo "✅ Done: RAGCRUD stubs generated & patched"
