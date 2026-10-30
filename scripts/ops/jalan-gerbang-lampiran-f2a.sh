#!/usr/bin/env bash
# LAMPIRAN-PEMBELIAN F2a: HIJAU-0 (tes lama di DASAR = WAJIB hijau) + MERAH-1 per berkas (spek baru + guard F1 tertarget, dasar = WAJIB merah)
# + sabotase A1..A5 (WAJIB merah) + gate_izin_rute + tes terfokus + harness NYATA kaos (ROLLBACK). HASH app/ awal / sebelum / sesudah nyata.
W=${W:-/root/mh-lampf2a}
cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master)" && (git merge-base --is-ancestor deploy/master HEAD && echo on-top || echo MASTER-MOVED)
L=/root/logs/lampf2a-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L/out; chmod 777 $L/out; echo "log: $L"
hash_app() { echo "HASH_APP $1 $(find backend/api_gateway/app -type f -not -path "*/__pycache__/*" | sort | xargs sha256sum | sha256sum | cut -c1-64)"; }
LAMA="tests/unit/test_lampiran_dokumen.py tests/unit/test_lampiran_so_keluarga.py tests/unit/test_lampiran_induk.py tests/unit/test_lampiran_1b_download.py tests/unit/test_lampiran_pembelian_f1.py"
SPEK="tests/unit/test_lampiran_pembelian_f2a.py"
salin() { POHON=$(mktemp -d /root/.sabf2a-XXXX); mkdir -p $POHON/backend
  rsync -a --exclude .pytest_cache --exclude __pycache__ backend/api_gateway $POHON/backend/ && ln -s $W/backend/migrations $POHON/backend/migrations; }
dasar() { for f in app/routers/lampiran_dokumen.py app/routers/sales_order_lampiran.py app/routers/lampiran_induk.py app/main.py \
                   app/middleware/permission_middleware.py tests/unit/inventaris_rute_tulis.txt; do
  git show deploy/master:backend/api_gateway/$f > $POHON/backend/api_gateway/$f; done; }
hash_app awal
echo "=== HIJAU-0 tes lama di DASAR (WAJIB hijau; guard F1 versi dasar)"
salin; dasar; git show deploy/master:backend/api_gateway/tests/unit/test_lampiran_pembelian_f1.py > $POHON/backend/api_gateway/tests/unit/test_lampiran_pembelian_f1.py
timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $LAMA 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in |Error" | tail -6; rm -rf $POHON
echo "=== MERAH-1 dasar + spek baru / guard tertarget (WAJIB merah, per berkas)"
for t in $SPEK tests/unit/test_lampiran_pembelian_f1.py; do echo "--- $t"; salin; dasar; timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $t 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -8; rm -rf $POHON; done
for s in A1 A2 A3 A4 A5; do
  echo "=== sabotase $s (WAJIB merah)"
  salin; if python3 scripts/ops/sabotase-lampiran-f2a.py $POHON/backend/api_gateway $s; then
    timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $SPEK $LAMA 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -5
  else echo "FAIL sabotase $s tak terpasang"; fi; rm -rf $POHON
done
echo "=== gate_izin_rute.py (== mh-gerbang-merge: kontainer sekali-pakai :ro, skrip dari dasar)"
IZG=$(mktemp /tmp/izg-XXXX.py); git show deploy/master:scripts/gate_izin_rute.py > $IZG
timeout 300 docker run --rm -i --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -v $W/backend:/app/backend:ro -w /app \
  milkyhoop-dev-api_gateway:latest python - < $IZG 2>&1 | grep -E "^(HIJAU|MERAH)|\[X\]" | tail -6; rm -f $IZG
F=$(cd backend/api_gateway && { ls tests/unit/test_izin_*.py tests/unit/test_lampiran*.py 2>/dev/null; grep -rlnE "lampiran_dokumen|lampiran_induk|sales_order_lampiran|JENIS_KELUARGA|permission_middleware|izin_untuk_jalur|ROUTE_PERMISSIONS|inventaris_rute|purchase_orders|vendor_credits|documents\b|attach" tests/unit --include=*.py; } | sort -u | tr "\n" " ")
echo "=== tes terfokus ($(echo $F | wc -w) berkas)"
timeout 1500 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F tests/unit/test_pagar_rute_izin.py > $L/unit.log 2>&1
grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -15
hash_app sebelum-nyata
G=milkyhoop-dev-api_gateway; IMG=$(docker inspect $G --format "{{.Config.Image}}"); E=$(mktemp /root/.f2a-XXXX); chmod 600 $E
printf "URL=%s\n" "$(docker exec $G printenv DATABASE_URL)" > $E
echo "=== harness NYATA kaos (ROLLBACK, TES E2E LAMPIRAN-F2A)"
timeout 600 docker run --rm --network milkyhoop_dev_network --env-file $E -v $W/backend:/app/backend:ro -v $W/scripts/ops/uji-nyata-lampiran-f2a.py:/h.py:ro \
  -v $L/out:/out -w /app/backend/api_gateway --entrypoint python $IMG -I /h.py /out/nyata.json > $L/h.log 2>&1 < /dev/null; echo "rc=$?"; grep -E "^(OK|GAGAL|HASIL)|Error" $L/h.log | tail -25
rm -f $E
hash_app sesudah-nyata
echo SELESAI_SEMUA
