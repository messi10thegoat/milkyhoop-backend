#!/usr/bin/env bash
# Jalankan suite unit di kontainer SEKALI-PAKAI.
#
# Kenapa bukan `docker exec` ke kontainer produksi: kontainer itu melayani
# pengguna sungguhan. Harness tidak boleh memasang paket ke dalamnya, dan
# suite yang menyentuhnya bukan lagi suite yang aman dijalankan kapan saja.
#
# Pemakaian:  ./scripts/jalankan_unit.sh [path-worktree]
# Contoh:     ./scripts/jalankan_unit.sh /root/mh-harness
set -euo pipefail
POHON="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
GW="$POHON/backend/api_gateway"
MIG="$POHON/backend/migrations"
[ -d "$GW/tests/unit" ] || { echo "tests/unit tidak ada di $GW"; exit 2; }

# Sebagian tes membaca DDL sebagai KEBENARAN — mis. tests/unit/test_t200_proforma.py
# yang menegaskan `proformas` tidak menyimpan angka terbayar (terbayar =
# TURUNAN). Tes itu mencari `<parent>/backend/migrations/V225__proformas.sql`,
# jadi tanpa mount ini ia MERAH PERMANEN dengan sebab "berkas tidak ditemukan"
# — merah yang tak ada hubungannya dengan kode.
#
# Itu bukan gangguan kecil: merah yang tak pernah bisa hijau MELATIH PEMBACA
# MENGABAIKAN TES MERAH, dan lama-lama yang benar-benar merah ikut tersaring.
#
# Gagal-keras kalau direktorinya tak ada, supaya kekurangan mount tidak pernah
# lagi menyamar jadi kegagalan tes.
[ -d "$MIG" ] || { echo "backend/migrations tidak ada di $POHON"; exit 2; }

docker run --rm \
  -v "$GW:/app/backend/api_gateway" \
  -v "$MIG:/app/backend/migrations:ro" \
  -w /app/backend/api_gateway \
  --entrypoint bash \
  milkyhoop-dev-api_gateway:latest -c '
    pip install -q pytest pytest-asyncio 2>&1 | tail -2
    python -m pytest -c pytest-unit.ini "$@"
  ' -- "${@:2}"
