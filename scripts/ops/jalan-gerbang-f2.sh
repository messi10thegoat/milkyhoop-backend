#!/usr/bin/env bash
# F2: HIJAU-0 (tes lama blok_void di DASAR master = WAJIB hijau) + MERAH-1 (dasar + spek F2 = WAJIB merah) + sabotase S1..S4 (WAJIB merah)
# + gate_izin_rute + tes terfokus + harness NYATA kaos (ROLLBACK, TES E2E F2). HASH app/ dicatat awal / sebelum nyata / sesudah nyata.
W=${W:-/root/mh-f2}
cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master)" && (git merge-base --is-ancestor deploy/master HEAD && echo on-top || echo MASTER-MOVED)
L=/root/logs/f2-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L/out; chmod 777 $L/out; echo "log: $L"
hash_app() { echo "HASH_APP $1 $(find backend/api_gateway/app -type f -not -path "*/__pycache__/*" | sort | xargs sha256sum | sha256sum | cut -c1-64)"; }
LAMA="tests/unit/test_tagihan_aksi.py tests/unit/test_sa_koreksi.py tests/unit/test_tagihan_grn_d.py"
SPEK="tests/unit/test_f2_void_fpb_nd_tertaut.py"
salin() { POHON=$(mktemp -d /root/.sabf2-XXXX); mkdir -p $POHON/backend
  rsync -a --exclude .pytest_cache --exclude __pycache__ backend/api_gateway $POHON/backend/ && ln -s $W/backend/migrations $POHON/backend/migrations; }
dasar() { git show deploy/master:backend/api_gateway/app/services/tagihan_aksi.py > $POHON/backend/api_gateway/app/services/tagihan_aksi.py; }
hash_app awal
echo "=== HIJAU-0 tes lama di DASAR deploy/master (WAJIB hijau)"
salin; dasar; timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $LAMA 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in |Error" | tail -6; rm -rf $POHON
echo "=== MERAH-1 dasar deploy/master + spek F2 (WAJIB merah)"
salin; dasar; timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $SPEK 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in |Error" | tail -8; rm -rf $POHON
for s in S1 S2 S3 S4; do
  echo "=== sabotase $s (WAJIB merah)"
  salin; if python3 scripts/ops/sabotase-f2.py $POHON/backend/api_gateway $s; then
    timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $SPEK $LAMA 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -5
  else echo "FAIL sabotase $s tak terpasang"; fi; rm -rf $POHON
done
echo "=== gate_izin_rute.py (== mh-gerbang-merge: kontainer sekali-pakai :ro, skrip dari dasar)"
IZG=$(mktemp /tmp/izg-XXXX.py); git show deploy/master:scripts/gate_izin_rute.py > $IZG
timeout 300 docker run --rm -i --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -v $W/backend:/app/backend:ro -w /app \
  milkyhoop-dev-api_gateway:latest python - < $IZG 2>&1 | grep -E "^(HIJAU|MERAH)|\[X\]" | tail -6; rm -f $IZG
F=$(cd backend/api_gateway && { ls tests/unit/test_izin_*.py tests/unit/test_f2_*.py 2>/dev/null; grep -rlnE "tagihan_aksi|blok_void|rencana_void|bills_service|void_bill|ADA_NOTA_DEBIT|vendor_credit|nota_debit|original_bill_id" tests/unit --include=*.py; } | sort -u | tr "\n" " ")
echo "=== tes terfokus ($(echo $F | wc -w) berkas)"
timeout 1200 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F tests/unit/test_pagar_rute_izin.py > $L/unit.log 2>&1
grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -15
hash_app sebelum-nyata
G=milkyhoop-dev-api_gateway; IMG=$(docker inspect $G --format "{{.Config.Image}}"); E=$(mktemp /root/.f2-XXXX); chmod 600 $E
printf "URL=%s\n" "$(docker exec $G printenv DATABASE_URL)" > $E
echo "=== harness NYATA kaos (ROLLBACK, TES E2E F2)"
timeout 600 docker run --rm --network milkyhoop_dev_network --env-file $E -v $W/backend:/app/backend:ro -v $W/scripts/ops/uji-nyata-f2-void-fpb-nd.py:/h.py:ro \
  -v $L/out:/out -w /app/backend/api_gateway --entrypoint python $IMG -I /h.py /out/nyata.json > $L/h.log 2>&1 < /dev/null; echo "rc=$?"; tail -30 $L/h.log
rm -f $E
hash_app sesudah-nyata
echo SELESAI_SEMUA
