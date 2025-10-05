#!/usr/bin/env bash
# -----------------------------------------
# PATCH STUB IMPORTS untuk modul baru MilkyHoop
# Fix import absolute → relative & nama alias di stub Python
#
# Cara pakai:
# bash scripts/patch_stub_imports.sh <path_ke_file_pb2_grpc.py>
# -----------------------------------------

TARGET_FILE=$1

if [ -z "$TARGET_FILE" ]; then
  echo "❌ Harus kasih path file .py (misal: backend/services/memory_service/app/memory_service_pb2_grpc.py)"
  exit 1
fi

echo "🔍 Patching file: $TARGET_FILE ..."

# 1️⃣ Fix import absolute → relative
sed -i 's/^import \(.*_pb2\)/from . import \1/' "$TARGET_FILE"

# 2️⃣ Fix nama alias __ jadi _
sed -i 's/\([a-z]*\)__\([a-z]*\)_pb2/\1_\2_pb2/g' "$TARGET_FILE"

echo "✅ Patch selesai!"
