#!/bin/bash
# check_conflict.sh — Deteksi nama file yang bentrok dengan modul Python standar

# Daftar modul Python standar yang rawan bentrok
STANDARD_MODULES=("types" "http" "json" "socket" "os" "sys" "logging" "asyncio" "email")

# Folder target (dibatasi ke folder internal milik proyek)
TARGET_DIRS=(
  "backend/api_gateway/libs"
  "backend/services"
  "common"
)

echo "🔍 Mengecek konflik nama file di folder internal proyek..."
echo "----------------------------------------------------------"

conflict_found=false

for dir in "${TARGET_DIRS[@]}"; do
  if [ -d "$dir" ]; then
    while IFS= read -r file; do
      filename=$(basename "$file" .py)
      for module in "${STANDARD_MODULES[@]}"; do
        if [[ "$filename" == "$module" ]]; then
          echo "⚠️  Konflik ditemukan: $file"
          conflict_found=true
        fi
      done
    done < <(find "$dir" -type f -name "*.py" ! -path "*/venv/*")
  fi
done

if [ "$conflict_found" = false ]; then
  echo "✅ Tidak ada konflik ditemukan. Aman!"
else
  echo "🚨 Harap rename file yang konflik untuk mencegah error runtime."
  exit 1
fi
