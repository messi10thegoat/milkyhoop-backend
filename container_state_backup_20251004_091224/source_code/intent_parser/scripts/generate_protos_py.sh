#!/bin/bash
set -e

PROTO_DIR="protos"
OUT_DIR="backend/api_gateway/libs/milkyhoop_protos"

echo "🐍 [PYTHON] Scanning $PROTO_DIR for .proto files..."
mkdir -p "$OUT_DIR"

# 🧹 Hapus semua stub lama
echo "🧹 Menghapus stub *_pb2*.py sebelumnya..."
find "$OUT_DIR" -name "*_pb2*.py" -delete || true

# 🔁 Generate stub dari setiap file .proto
for PROTO_FILE in $(find "$PROTO_DIR" -maxdepth 1 -name "*.proto"); do
  FILENAME=$(basename -- "$PROTO_FILE")
  echo "🔧 Generating $FILENAME → $OUT_DIR"

  python3 -m grpc_tools.protoc \
    -I "$PROTO_DIR" \
    --python_out="$OUT_DIR" \
    --grpc_python_out="$OUT_DIR" \
    "$PROTO_FILE"
done

# 🩹 Patch import agar relative di *_pb2_grpc.py
echo "🩹 Memperbaiki import relatif di *_pb2_grpc.py..."
for FILE in $(find "$OUT_DIR" -name "*_pb2_grpc.py"); do
  sed -i 's/^import \(.*_pb2\) as/from . import \1 as/' "$FILE"
done

echo "✅ [PYTHON] Stub berhasil digenerate dan import sudah dipatch."
