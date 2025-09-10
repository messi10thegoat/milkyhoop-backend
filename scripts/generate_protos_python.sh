#!/bin/bash
set -e

# Fallback interpreter
PYTHON_BIN=${PYTHON_BIN:-python3}

# Path absolut
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROTO_DIR="$SCRIPT_DIR/../protos"

echo "🐍 [PYTHON] Scanning $PROTO_DIR for .proto files..."

for PROTO_FILE in $(find "$PROTO_DIR" -name "*.proto"); do
  RELATIVE_PATH="${PROTO_FILE#$PROTO_DIR/}"
  MODULE_DIR="$(dirname "$RELATIVE_PATH")"
  MODULE_NAME="$(basename "$MODULE_DIR")"
  OUT_DIR="$PROTO_DIR"

  echo "🔧 Generating Python proto for $PROTO_FILE..."

  mkdir -p "$OUT_DIR/$MODULE_DIR"
  touch "$OUT_DIR/__init__.py"
  touch "$OUT_DIR/$MODULE_DIR/__init__.py"

  $PYTHON_BIN -m grpc_tools.protoc \
    -I"$PROTO_DIR" \
    -I"$($PYTHON_BIN -c "import grpc_tools; print(grpc_tools.__path__[0] + '/_proto')")" \
    --python_out="$OUT_DIR" \
    --grpc_python_out="$OUT_DIR" \
    "$PROTO_FILE"

  GRPC_PY="$OUT_DIR/$MODULE_DIR/${MODULE_NAME}_pb2_grpc.py"

  if [[ -f "$GRPC_PY" ]]; then
    if grep -q "^from $MODULE_NAME import" "$GRPC_PY"; then
      echo "🛠️  Patching import in $GRPC_PY..."
      if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s/from $MODULE_NAME import/from protos.$MODULE_NAME import/" "$GRPC_PY"
      else
        sed -i "s/from $MODULE_NAME import/from protos.$MODULE_NAME import/" "$GRPC_PY"
      fi
    fi
  else
    echo "⚠️  File $GRPC_PY not found, skipping patch."
  fi
done

echo "✅ [PYTHON] All .proto files processed."
