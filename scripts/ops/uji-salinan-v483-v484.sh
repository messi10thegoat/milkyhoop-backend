#!/usr/bin/env bash
# Uji SALINAN dua sisi V483 + V484 (BUG-098, BUG-099(b)): klon penuh milkydb -> scratch_mig483_<ts>; fase sebelum -> terapkan berkas
# migrasi APA ADANYA (psql -1 -v ON_ERROR_STOP=1 == apply_mig.sh) -> sesudah -> pembalik _ROLLBACK -> pembalik; HAPUS salinan (trap).
# Pakai: POHON=/root/<worktree> scripts/ops/uji-salinan-v483-v484.sh (cek-sibuk dulu). TIDAK menyentuh milkydb.
set -u
P=milkyhoop-dev-postgres-1; S=scratch_mig483_$(date +%s); POHON=${POHON:-/root/mh-mig-098-099}; M=$POHON/backend/migrations
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null
          echo "salinan $S dihapus; sisa DB scratch_mig483%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_mig483%'")"; }
trap hapus EXIT
echo "salinan: $S"
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl | psql -q -U postgres -d $S" >/tmp/mig483_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/mig483_restore.log); ledger salinan=$(docker exec $P psql -U postgres -d $S -At -c 'SELECT count(*) FROM inventory_ledger') prod=$(docker exec $P psql -U postgres -d milkydb -At -c 'SELECT count(*) FROM inventory_ledger')"
uji() { timeout 300 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S \
  -v "$POHON/backend:/app/backend:ro" -v "$(cd "$(dirname "$0")" && pwd)/uji-salinan-v483-v484.py:/uji.py:ro" -w /app/backend/api_gateway \
  --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py "$@" 2>&1 | grep -E "^(PASS|FAIL|HASIL|NOMOR_LAMA|\[|Traceback|  File|[A-Za-z]+Error)"; }
terap() { for f in "$@"; do echo "terapkan $(basename $f)"; docker exec -i $P psql -U postgres -d $S -1 -v ON_ERROR_STOP=1 -q < "$f" || { echo "GAGAL $f"; exit 3; }; done; }
OUT=$(uji sebelum); echo "$OUT"; LAMA=$(echo "$OUT" | sed -n 's/^NOMOR_LAMA=//p')
terap $M/V483__nomor_rcv_yymm_bulanan.sql $M/V484__saldo_stok_dari_mutasi.sql
uji sesudah "$LAMA"
terap $M/V484__saldo_stok_dari_mutasi_ROLLBACK.sql $M/V483__nomor_rcv_yymm_bulanan_ROLLBACK.sql
uji pembalik
