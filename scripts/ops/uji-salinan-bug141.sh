#!/usr/bin/env bash
# BUG-141 / ROW (t) di SALINAN: klon milkydb -> scratch_b141_<ts>; handler ASLI /bank-accounts/{id}/transactions (baca saja); HAPUS salinan (trap).
# Pakai: POHON=/root/<worktree> scripts/ops/uji-salinan-bug141.sh (cek-sibuk dulu).
set -u
# penjaga: setiap SQL FROM di harness ini wajib tersaring tenant (putusan MASTER 7 Nov) -- gagal SEBELUM klon dibuat
python3 "$(dirname "$0")/cek-saring-tenant.py" "$0" "$(dirname "$0")/uji-salinan-bug141.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; S=scratch_b141_$(date +%s); POHON=${POHON:-/root/mh-stok-satuan}
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null
          echo "salinan $S dihapus; sisa DB scratch_b141%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_b141%'")"; }
trap hapus EXIT
echo "salinan: $S"
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl | psql -q -U postgres -d $S" >/tmp/b141_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/b141_restore.log); baris bank salinan=$(docker exec $P psql -U postgres -d $S -At -c "SELECT count(*) FROM bank_transactions WHERE tenant_id IN ('kaos-biru-konveksi','pt-kita-nyawit-bersama')") prod=$(docker exec $P psql -U postgres -d milkydb -At -c "SELECT count(*) FROM bank_transactions WHERE tenant_id IN ('kaos-biru-konveksi','pt-kita-nyawit-bersama')")"
timeout 900 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S -e TENANT=${TENANT:-kaos-biru-konveksi,pt-kita-nyawit-bersama} \
  -v "$POHON/backend:/app/backend:ro" -v "$(cd "$(dirname "$0")" && pwd)/uji-salinan-bug141.py:/uji.py:ro" -w /app/backend/api_gateway \
  --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py 2>&1 | grep -E "^(PASS|FAIL|HASIL|\[|Traceback|  File)|Error"
echo "rc=${PIPESTATUS[0]}"
