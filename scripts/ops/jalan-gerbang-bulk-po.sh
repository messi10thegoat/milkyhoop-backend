#!/usr/bin/env bash
# B1b-1: HIJAU-0 (karakterisasi di DASAR master = WAJIB hijau) + MERAH-1 (dasar master + spek baru) + sabotase S1..S7 (WAJIB merah)
# + gate_izin_rute + tes terfokus + harness NYATA kaos (TULIS, penanda TES E2E; kontainer sekali pakai, image gateway yang sama).
W=${W:-/root/mh-bulkpo}
cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master)" && (git merge-base --is-ancestor deploy/master HEAD && echo on-top || echo MASTER-MOVED)
L=/root/logs/bulkpo-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L/out; chmod 777 $L/out; echo "log: $L"
KAR="tests/unit/test_po_aksi_karakterisasi.py"
SPEK="tests/unit/test_bulk_po_b1b1.py tests/unit/test_urutan_rute_bulk.py"
salin() { POHON=$(mktemp -d /root/.sabpo-XXXX); mkdir -p $POHON/backend
  rsync -a --exclude .pytest_cache --exclude __pycache__ backend/api_gateway $POHON/backend/ && ln -s $W/backend/migrations $POHON/backend/migrations; }
dasar() { for f in app/routers/purchase_orders.py app/main.py app/middleware/permission_middleware.py tests/unit/inventaris_rute_tulis.txt; do
  git show deploy/master:backend/api_gateway/$f > $POHON/backend/api_gateway/$f; done; rm -f $POHON/backend/api_gateway/app/routers/bulk_po.py; }
echo "=== HIJAU-0 karakterisasi di DASAR deploy/master (WAJIB hijau)"
salin; dasar; timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $KAR 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in |Error" | tail -6; rm -rf $POHON
echo "=== MERAH-1 dasar deploy/master + spek baru (WAJIB merah)"
salin; dasar; timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $SPEK 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in |Error" | tail -6; rm -rf $POHON
for s in S1 S2 S3 S4 S5 S6 S7; do
  echo "=== sabotase $s (WAJIB merah)"
  salin; if python3 scripts/ops/sabotase-bulk-po.py $POHON/backend/api_gateway $s; then
    timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $KAR $SPEK tests/unit/test_po_tulis.py 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -5
  else echo "FAIL sabotase $s tak terpasang"; fi; rm -rf $POHON
done
echo "=== gate_izin_rute.py (== mh-gerbang-merge: kontainer sekali-pakai :ro, skrip dari dasar)"
IZG=$(mktemp /tmp/izg-XXXX.py); git show deploy/master:scripts/gate_izin_rute.py > $IZG
timeout 300 docker run --rm -i --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -v $W/backend:/app/backend:ro -w /app \
  milkyhoop-dev-api_gateway:latest python - < $IZG 2>&1 | grep -E "^(HIJAU|MERAH)|\[X\]" | tail -6; rm -f $IZG
F=$(cd backend/api_gateway && { ls tests/unit/test_bulk*.py tests/unit/test_izin_*.py tests/unit/test_po_*.py tests/unit/test_po*.py 2>/dev/null; grep -rlnE "purchase_orders|po_rencana|bulk_po|routers import bulk|permission_middleware|inventaris_rute_tulis|idem_buat" tests/unit --include=*.py; } | sort -u | tr "\n" " ")
echo "=== tes terfokus ($(echo $F | wc -w) berkas)"
timeout 1200 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F tests/unit/test_pagar_rute_izin.py tests/unit/test_urutan_rute_bulk.py > $L/unit.log 2>&1
grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -15
G=milkyhoop-dev-api_gateway; IMG=$(docker inspect $G --format "{{.Config.Image}}"); E=$(mktemp /root/.bp-XXXX); chmod 600 $E
printf "URL=%s\n" "$(docker exec $G printenv DATABASE_URL)" > $E
echo "=== harness NYATA kaos (TULIS, TES E2E)"
timeout 600 docker run --rm --network milkyhoop_dev_network --env-file $E -v $W/backend:/app/backend:ro -v $W/scripts/ops/uji-nyata-bulk-po.py:/h.py:ro \
  -v $L/out:/out -w /app/backend/api_gateway --entrypoint python $IMG -I /h.py /out/nyata.json > $L/h.log 2>&1 < /dev/null; echo "rc=$?"; tail -30 $L/h.log
rm -f $E
echo SELESAI_SEMUA
