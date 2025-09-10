#!/bin/bash
set -euo pipefail

# Input nama modul (misal: order_service)
MODULE_NAME=$1

if [ -z "$MODULE_NAME" ]; then
  echo "Usage: ./finalize_module.sh <module_name>"
  exit 1
fi

echo "🚀 Starting finalization for module: $MODULE_NAME"

# 1. Remove old module
echo "🧹 Removing old module folder..."
rm -rf backend/services/$MODULE_NAME

# 2. Remove old proto file
echo "🧹 Removing old proto file..."
rm -f protos/$MODULE_NAME.proto

# 3. Remove old stubs recursively
echo "🧹 Removing old gRPC stubs..."
find backend/ -name "${MODULE_NAME}_pb2*.py" -delete

# 4.a Clone new module template via CLI
echo "📥 Cloning new module template..."
python3 scripts/milky_cli.py $MODULE_NAME --lang=python --prisma



# 4.b 🔧 Patch prisma_client.py import supaya dari milkyhoop_prisma, bukan prisma standar
PRISMA_CLIENT_FILE="backend/services/$MODULE_NAME/app/prisma_client.py"
if [ -f "$PRISMA_CLIENT_FILE" ]; then
  sed -i 's/^from prisma import Prisma/from milkyhoop_prisma import Prisma/' "$PRISMA_CLIENT_FILE"
  echo "✅ prisma_client.py import sudah diganti ke milkyhoop_prisma"
fi



# 5. Generate new stubs
echo "📦 Generating new gRPC stubs..."
python3 -m grpc_tools.protoc --proto_path=protos \
  --python_out=backend/services/$MODULE_NAME/app \
  --grpc_python_out=backend/services/$MODULE_NAME/app \
  protos/$MODULE_NAME.proto

# 6. Patch relative imports in *_pb2_grpc.py robustly
GRPC_STUB_FILE="backend/services/$MODULE_NAME/app/${MODULE_NAME}_pb2_grpc.py"
echo "✏️ Patching relative imports in $GRPC_STUB_FILE..."

if [ ! -f "$GRPC_STUB_FILE" ]; then
  echo "❌ File $GRPC_STUB_FILE not found, skipping patch."
else
  # Patch absolute import to relative import (sed compatible with spaces)
  sed -i -E "s/^import[[:space:]]+${MODULE_NAME}_pb2[[:space:]]+as/import . ${MODULE_NAME}_pb2 as/g" "$GRPC_STUB_FILE"

  # Additional patch to fix syntax error: 
  # Replace 'import . memory_service_pb2 as ...' with 'from . import memory_service_pb2 as ...'
  sed -i -E "s/^import[[:space:]]*\.[[:space:]]*([a-zA-Z0-9_]+_pb2)[[:space:]]+as[[:space:]]+/from . import \1 as /" "$GRPC_STUB_FILE"

  if grep -q "from \. import ${MODULE_NAME}_pb2" "$GRPC_STUB_FILE"; then
    echo "✅ Relative import patch applied successfully."
  else
    echo "⚠️ Warning: Relative import patch may have failed. Please verify manually."
  fi
fi

# 7. Run prisma db push + generate
echo "🔧 Syncing Prisma schema and regenerating client..."
npx prisma db push --schema=database/schemas/global_schema.prisma
npx prisma generate --schema=database/schemas/global_schema.prisma

# 8. Reminder for manual steps
echo "⚠️ REMINDER: Please check:"
echo " - prisma_client.py must import from milkyhoop_prisma"
echo " - CRUD logic implemented in backend/services/$MODULE_NAME/app/services/"
echo " - gRPC Servicer methods added and tested"
echo " - PYTHONPATH environment variable includes backend/api_gateway/libs and backend/services/$MODULE_NAME/app"

echo "✅ Finalization script for $MODULE_NAME completed!"

# 9. Clean __pycache__ again to avoid stale imports
echo "🧹 Cleaning __pycache__ to avoid stale import errors..."
find backend/services/$MODULE_NAME -type d -name "__pycache__" -exec rm -rf {} +


# 10. Make sure __init__.py exists in app folder
echo "📝 Making sure __init__.py exists in app folder..."
touch backend/services/$MODULE_NAME/app/__init__.py


echo "⚠️ Manual Step: To test gRPC server, run:"
echo "   cd /root/milkyhoop"
echo "   PYTHONPATH=backend/api_gateway/libs:backend/services/$MODULE_NAME python3 -m backend.services.$MODULE_NAME.app.grpc_server"



