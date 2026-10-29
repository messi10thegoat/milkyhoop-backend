#!/usr/bin/env bash
# ND-BUAT: HIJAU-0 (karakterisasi buat ND lama di DASAR master = WAJIB hijau) + MERAH-1 (dasar + spek baru) + sabotase S1..S8 (WAJIB merah)
# + gate_izin_rute + tes terfokus + harness NYATA kaos (ROLLBACK, TES E2E). HASH app/ dicatat awal / sebelum nyata / sesudah nyata.
W=${W:-/root/mh-ndbuat}
cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master)" && (git merge-base --is-ancestor deploy/master HEAD && echo on-top || echo MASTER-MOVED)
L=/root/logs/ndbuat-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L/out; chmod 777 $L/out; echo "log: $L"
hash_app() { echo "HASH_APP $1 $(find backend/api_gateway/app -type f -not -path "*/__pycache__/*" | sort | xargs sha256sum | sha256sum | cut -c1-64)"; }
KAR="tests/unit/test_nd_buat_karakterisasi.py"
SPEK="tests/unit/test_nd_buat.py"
salin() { POHON=$(mktemp -d /root/.sabnd-XXXX); mkdir -p $POHON/backend
  rsync -a --exclude .pytest_cache --exclude __pycache__ backend/api_gateway $POHON/backend/ && ln -s $W/backend/migrations $POHON/backend/migrations; }
dasar() { for f in app/routers/vendor_credits.py app/schemas/vendor_credits.py app/schemas/uang_json.py app/services/inventory_helpers.py app/services/nota_debit.py; do
  git show deploy/master:backend/api_gateway/$f > $POHON/backend/api_gateway/$f; done; }
hash_app awal
echo "=== HIJAU-0 karakterisasi di DASAR deploy/master (WAJIB hijau)"
salin; dasar; timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $KAR 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in |Error" | tail -6; rm -rf $POHON
echo "=== MERAH-1 dasar deploy/master + spek baru (WAJIB merah)"
salin; dasar; timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $SPEK 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in |Error" | tail -4; rm -rf $POHON
for s in S1 S2 S3 S4 S5 S6 S7 S8 S9 S10; do
  echo "=== sabotase $s (WAJIB merah)"
  salin; if python3 scripts/ops/sabotase-nd-buat.py $POHON/backend/api_gateway $s; then
    timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $KAR $SPEK 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -5
  else echo "FAIL sabotase $s tak terpasang"; fi; rm -rf $POHON
done
for g in G1 G2; do
  echo "=== sabotase penjaga lama $g -> test_p2p3b_nd_buat_pratinjau + test_p2p7_uang_decimal (WAJIB merah)"
  salin; if python3 scripts/ops/sabotase-nd-buat.py $POHON/backend/api_gateway $g; then
    timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider tests/unit/test_p2p3b_nd_buat_pratinjau.py tests/unit/test_p2p7_uang_decimal.py 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -5
  else echo "FAIL sabotase $g tak terpasang"; fi; rm -rf $POHON
done
echo "=== gate_izin_rute.py (== mh-gerbang-merge: kontainer sekali-pakai :ro, skrip dari dasar)"
IZG=$(mktemp /tmp/izg-XXXX.py); git show deploy/master:scripts/gate_izin_rute.py > $IZG
timeout 300 docker run --rm -i --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -v $W/backend:/app/backend:ro -w /app \
  milkyhoop-dev-api_gateway:latest python - < $IZG 2>&1 | grep -E "^(HIJAU|MERAH)|\[X\]" | tail -6; rm -f $IZG
F=$(cd backend/api_gateway && { ls tests/unit/test_nd*.py tests/unit/test_nota_debit*.py tests/unit/test_vendor_credit*.py tests/unit/test_izin_*.py 2>/dev/null; grep -rlnE "inventory_helpers|record_inventory_outbound|vendor_credits|nota_debit|uang_json|UangJson|tax_factor|resolve_dpp_factor|bulk_hapus_beli|permission_middleware|inventaris_rute_tulis" tests/unit --include=*.py; } | sort -u | tr "\n" " ")
echo "=== tes terfokus ($(echo $F | wc -w) berkas)"
timeout 1200 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F tests/unit/test_pagar_rute_izin.py > $L/unit.log 2>&1
grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -15
hash_app sebelum-nyata
G=milkyhoop-dev-api_gateway; IMG=$(docker inspect $G --format "{{.Config.Image}}"); E=$(mktemp /root/.nd-XXXX); chmod 600 $E
printf "URL=%s\n" "$(docker exec $G printenv DATABASE_URL)" > $E
echo "=== harness NYATA kaos (ROLLBACK, TES E2E)"
timeout 600 docker run --rm --network milkyhoop_dev_network --env-file $E -v $W/backend:/app/backend:ro -v $W/scripts/ops/uji-nyata-nd-buat.py:/h.py:ro \
  -v $L/out:/out -w /app/backend/api_gateway --entrypoint python $IMG -I /h.py /out/nyata.json > $L/h.log 2>&1 < /dev/null; echo "rc=$?"; tail -30 $L/h.log
rm -f $E
hash_app sesudah-nyata
echo SELESAI_SEMUA
