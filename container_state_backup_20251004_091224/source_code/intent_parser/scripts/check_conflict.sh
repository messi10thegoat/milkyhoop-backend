#!/bin/bash

echo "🔍 Menjalankan pengecekan file konflik dengan modul Python bawaan..."

# Daftar nama file yang sering bentrok dengan modul bawaan Python
DANGEROUS_FILES=("types.py" "http.py" "async.py" "string.py" "client.py")

# Lokasi root source code (ubah sesuai kebutuhan)
SRC_DIR="backend/api_gateway/libs/prisma_generated"

# Cari file mencurigakan
for filename in "${DANGEROUS_FILES[@]}"; do
  FOUND=$(find "$SRC_DIR" -type f -name "$filename")
  if [[ ! -z "$FOUND" ]]; then
    echo "❌ Ditemukan file berpotensi konflik: $FOUND"
  fi
done

# Cek relative import yang bisa bermasalah
echo "🔍 Menjalankan pengecekan relative import mencurigakan..."
grep -rnw "$SRC_DIR" -e "from . import types"
grep -rnw "$SRC_DIR" -e "from . import http"

echo "✅ Pengecekan selesai. Jika ada peringatan ❌ di atas, segera rename dan update import-nya."

