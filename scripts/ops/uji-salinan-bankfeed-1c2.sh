#!/usr/bin/env bash
# BF-1c2 uji di SALINAN: klon penuh milkydb -> scratch_bf1c2_<ts>; DUA pohon kode (LAMA = induk commit ekstraksi, BARU = POHON) pada DB yang sama; golden anonim di /root/fixtures (luar repo;
# hilang = TOLAK rc=4); HAPUS salinan (trap). Pakai: POHON=/root/<worktree> scripts/ops/uji-salinan-bankfeed-1c2.sh (cek-sibuk dulu).
set -u
D="$(dirname "$0")"
python3 "$D/cek-saring-tenant.py" "$0" "$D/uji-salinan-bankfeed-1c2.py" "$D/uji-salinan-bankfeed-1c2-lama.py" "$D/uji_c2_tanda.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
F=/root/fixtures/bankfeed_golden
for n in a-mutasi-2026-08.csv b-catatan-milkyhoop.csv c-jawaban-benar.csv; do
  [ -f $F/$n ] || { echo "TOLAK: fixture golden hilang di $F ($n) (rc=4)"; echo "rc=4"; exit 4; }
done
P=milkyhoop-dev-postgres-1; S=scratch_bf1c2_$(date +%s); POHON=${POHON:-/root/mh-bf1c2}; LAMA=/root/mh-lama-c2
LAMA_SHA=${LAMA_SHA:-$(git -C "$POHON" rev-parse 112946f50^)}
[ -d "$LAMA" ] || git -C /root/milkyhoop-dev worktree add -q --detach "$LAMA" "$LAMA_SHA" || { echo "rc=5 (pohon LAMA gagal)"; exit 5; }
git -C "$LAMA" reset -q --hard "$LAMA_SHA"
SIG=$(mktemp -d /tmp/sig1c2.XXXXXX); chmod 777 "$SIG"
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null; rm -rf "$SIG"
          echo "salinan $S dihapus; sisa DB scratch_bf1c2%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_bf1c2%'")"; }
trap hapus EXIT
echo "salinan: $S; LAMA=$(git -C "$LAMA" rev-parse --short HEAD) BARU=$(git -C "$POHON" rev-parse --short HEAD)"
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl | psql -q -U postgres -d $S" >/tmp/bf1c2_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/bf1c2_restore.log)"
IMG=milkyhoop-dev-postgres-1; ENVF=/root/milkyhoop-dev/.env
echo "== sisi LAMA (pohon sebelum ekstraksi)"
timeout 600 docker run --rm --network milkyhoop_dev_network --env-file $ENVF -e DB_NAME=$S -v "$LAMA/backend:/app/backend:ro" -v "$SIG:/sig" -v "$(cd "$D" && pwd)/uji_c2_tanda.py:/tanda/uji_c2_tanda.py:ro" \
  -v "$(cd "$D" && pwd)/uji-salinan-bankfeed-1c2-lama.py:/uji.py:ro" -w /app/backend/api_gateway --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py 2>&1 | grep -E "^(INFO|PASS|FAIL|TOLAK|Traceback|  File)|Error"
echo "rc-lama=${PIPESTATUS[0]}"
echo "== sisi BARU"
timeout 900 docker run --rm --network milkyhoop_dev_network --env-file $ENVF -e DB_NAME=$S -v $F:/root/fixtures_ro:ro -v "$POHON/backend:/app/backend:ro" -v "$SIG:/sig:ro" \
  -v "$(cd "$D" && pwd)/uji_c2_tanda.py:/tanda/uji_c2_tanda.py:ro" -v "$(cd "$D" && pwd)/uji-salinan-bankfeed-1c2.py:/uji.py:ro" -w /app/backend/api_gateway \
  --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py 2>&1 | grep -E "^(PASS|FAIL|INFO|HASIL|TOLAK|\[|Traceback|  File)|Error|error"
echo "rc=${PIPESTATUS[0]}"
