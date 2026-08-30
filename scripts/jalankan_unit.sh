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
[ -d "$GW/tests/unit" ] || { echo "tests/unit tidak ada di $GW"; exit 2; }

docker run --rm \
  -v "$GW:/app/backend/api_gateway" \
  -w /app/backend/api_gateway \
  --entrypoint bash \
  milkyhoop-dev-api_gateway:latest -c '
    pip install -q pytest pytest-asyncio 2>&1 | tail -2
    python -m pytest -c pytest-unit.ini "$@"
  ' -- "${@:2}"
