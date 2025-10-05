#!/bin/bash
set -e

# === Konfigurasi path ===
CLIENT_OUT_DIR="backend/api_gateway/libs/milkyhoop_protos"
SERVER_OUT_DIR="backend/services/chatbot_service/app"

echo "🧹 Cleaning old stubs..."
rm -rf "$CLIENT_OUT_DIR" "$SERVER_OUT_DIR/chatbot_service_pb2.py" "$SERVER_OUT_DIR/chatbot_service_pb2_grpc.py"

mkdir -p "$CLIENT_OUT_DIR"
touch "$CLIENT_OUT_DIR/__init__.py"

# === Generate untuk semua .proto ke client ===
echo "📦 Generating all .proto files to CLIENT: $CLIENT_OUT_DIR"
for proto_file in protos/*.proto; do
  echo "   → $proto_file"
  python3 -m grpc_tools.protoc \
    -I protos \
    --python_out="$CLIENT_OUT_DIR" \
    --grpc_python_out="$CLIENT_OUT_DIR" \
    "$proto_file"
done

# === Generate khusus chatbot_service.proto ke server ===
echo "📦 Generating chatbot_service.proto to SERVER: $SERVER_OUT_DIR"
python3 -m grpc_tools.protoc \
  -I protos \
  --python_out="$SERVER_OUT_DIR" \
  --grpc_python_out="$SERVER_OUT_DIR" \
  protos/chatbot_service.proto

# === Patch import di client ===
echo "🛠️ Patching import statements in client stub to absolute imports..."
find "$CLIENT_OUT_DIR" -type f -name "*.py" | while read file; do
  sed -i 's/^import \(.*_pb2\)/from milkyhoop_protos import \1/' "$file"
done

echo "✅ All protos successfully regenerated & patched"
