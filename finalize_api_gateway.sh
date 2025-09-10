#!/bin/bash
set -euo pipefail

echo "🚀 Starting finalization for module: api_gateway"

# 1️⃣ Remove old __pycache__ & .pyc
echo "🧹 Cleaning old __pycache__ and .pyc..."
find backend/api_gateway -type d -name "__pycache__" -exec rm -rf {} +
find backend/api_gateway -name "*.pyc" -delete

# 2️⃣ Generate new stubs for gRPC client
echo "📦 Generating new gRPC stubs (if needed)..."
PROTO_DIR=protos
OUT_DIR=backend/api_gateway/app

python3 -m grpc_tools.protoc --proto_path=$PROTO_DIR \
  --python_out=$OUT_DIR \
  --grpc_python_out=$OUT_DIR \
  $PROTO_DIR/*.proto || echo "⚠️ No proto files found, skipping stub generation."

# 3️⃣ Patch relative imports in *_pb2_grpc.py
echo "✏️ Patching relative imports in *_pb2_grpc.py..."
for grpc_file in $(find $OUT_DIR -name "*_pb2_grpc.py"); do
  sed -i -E "s/^import[[:space:]]+([a-zA-Z0-9_]+_pb2)[[:space:]]+as/from . import \1 as/g" "$grpc_file"
done

# 4️⃣ Prisma sync schema & generate
echo "🔧 Syncing Prisma schema and regenerating client..."
npx prisma db push --schema=database/schemas/global_schema.prisma
npx prisma generate --schema=database/schemas/global_schema.prisma

# 5️⃣ Reminder for testing
echo "⚠️ REMINDER: Please test your gRPC client call & REST API endpoints manually!"
echo "✅ Finalization script for api_gateway completed!"
