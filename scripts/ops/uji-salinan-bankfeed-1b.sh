#!/usr/bin/env bash
# BF-1b penentu tebakan Bank Feed diuji di SALINAN: klon penuh milkydb -> scratch_bf1b_<ts>; rute/penentu ASLI + golden anonim di /root/fixtures (luar repo; hilang = TOLAK rc=4);
# benih b-catatan -> bank_transactions + jurnal (hanya di salinan); HAPUS salinan (trap). Pakai: POHON=/root/<worktree> scripts/ops/uji-salinan-bankfeed-1b.sh (cek-sibuk dulu).
set -u
python3 "$(dirname "$0")/cek-saring-tenant.py" "$0" "$(dirname "$0")/uji-salinan-bankfeed-1b.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
F=/root/fixtures/bankfeed_golden
for n in a-mutasi-2026-08.csv b-catatan-milkyhoop.csv c-jawaban-benar.csv d-kasus-pengecualian.csv; do
  [ -f $F/$n ] || { echo "TOLAK: fixture golden hilang di $F ($n) (rc=4)"; echo "rc=4"; exit 4; }
done
P=milkyhoop-dev-postgres-1; S=scratch_bf1b_$(date +%s); POHON=${POHON:-/root/mh-bf1b}
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null
          echo "salinan $S dihapus; sisa DB scratch_bf1b%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_bf1b%'")"; }
trap hapus EXIT
echo "salinan: $S"
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl | psql -q -U postgres -d $S" >/tmp/bf1b_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/bf1b_restore.log); bt salinan=$(docker exec $P psql -U postgres -d $S -At -c "SELECT count(*) FROM bank_transactions WHERE tenant_id IN ('kaos-biru-konveksi','pt-kita-nyawit-bersama')") prod=$(docker exec $P psql -U postgres -d milkydb -At -c "SELECT count(*) FROM bank_transactions WHERE tenant_id IN ('kaos-biru-konveksi','pt-kita-nyawit-bersama')")"
timeout 900 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S -v $F:/root/fixtures_ro:ro \
  -v "$POHON/backend:/app/backend:ro" -v "$(cd "$(dirname "$0")" && pwd)/uji-salinan-bankfeed-1b.py:/uji.py:ro" -w /app/backend/api_gateway \
  --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py 2>&1 | grep -E "^(PASS|FAIL|INFO|HASIL|TOLAK|\[|Traceback|  File)|Error|error"
echo "rc=${PIPESTATUS[0]}"
