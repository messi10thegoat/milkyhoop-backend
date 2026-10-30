#!/usr/bin/env bash
# FPB-DEBIT-PENUH + RECLASS-COGS-CLASS: HIJAU-0 (tes lama + karakterisasi jalur draf di DASAR = WAJIB hijau) + MERAH-1 (dasar + spek = WAJIB merah) + sabotase P1 R1..R6
# + gate_izin_rute + tes terfokus + harness NYATA kaos (ROLLBACK) di WORKTREE (WAJIB LULUS) DAN di DASAR (WAJIB GAGAL = kontrol merah).
W=${W:-/root/mh-fpbpenuh}
cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master)" && (git merge-base --is-ancestor deploy/master HEAD && echo on-top || echo MASTER-MOVED)
L=/root/logs/fpbpenuh-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L/out $L/out0; chmod 777 $L/out $L/out0; echo "log: $L"
hash_app() { echo "HASH_APP $1 $(find backend/api_gateway/app -type f -not -path "*/__pycache__/*" | sort | xargs sha256sum | sha256sum | cut -c1-64)"; }
LAMA="tests/unit/test_tagihan_aksi.py tests/unit/test_tagihan_grn.py tests/unit/test_tagihan_grn_d.py tests/unit/test_saldo_awal_vendor_a.py tests/unit/test_sa_koreksi.py tests/unit/test_fpb_debit_satu.py"
SPEK="tests/unit/test_fpb_debit_penuh.py"
salin() { POHON=$(mktemp -d /root/.sabfp-XXXX); mkdir -p $POHON/backend
  rsync -a --exclude .pytest_cache --exclude __pycache__ backend/api_gateway $POHON/backend/ && ln -s $W/backend/migrations $POHON/backend/migrations; }
dasar() { for f in app/services/bills_service.py app/routers/journals.py app/middleware/permission_middleware.py tests/unit/inventaris_rute_tulis.txt; do
  git show deploy/master:backend/api_gateway/$f > $POHON/backend/api_gateway/$f; done; rm -f $POHON/backend/api_gateway/app/services/reklas_kelas_beban.py; }
hash_app awal
echo "=== HIJAU-0 tes lama + karakterisasi jalur draf di DASAR (WAJIB hijau)"
salin; dasar; git show deploy/master:backend/api_gateway/tests/unit/test_fpb_debit_satu.py > $POHON/backend/api_gateway/tests/unit/test_fpb_debit_satu.py
timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $LAMA tests/unit/test_fpb_debit_karakterisasi.py 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in |Error" | tail -6; rm -rf $POHON
echo "=== MERAH-1 dasar + spek baru / guard tertarget (WAJIB merah, per berkas)"
for t in $SPEK tests/unit/test_fpb_debit_satu.py; do echo "--- $t"; salin; dasar; timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $t 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -10; rm -rf $POHON; done
for s in P1 R1 R2 R3 R4 R5 R6; do
  echo "=== sabotase $s (WAJIB merah)"
  salin; if python3 scripts/ops/sabotase-fpb-penuh.py $POHON/backend/api_gateway $s; then
    timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $SPEK $LAMA tests/unit/test_fpb_debit_karakterisasi.py 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -5
  else echo "FAIL sabotase $s tak terpasang"; fi; rm -rf $POHON
done
echo "=== gate_izin_rute.py (== mh-gerbang-merge: kontainer sekali-pakai :ro, skrip dari dasar)"
IZG=$(mktemp /tmp/izg-XXXX.py); git show deploy/master:scripts/gate_izin_rute.py > $IZG
timeout 300 docker run --rm -i --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -v $W/backend:/app/backend:ro -w /app \
  milkyhoop-dev-api_gateway:latest python - < $IZG 2>&1 | grep -E "^(HIJAU|MERAH)|\[X\]" | tail -6; rm -f $IZG
F=$(cd backend/api_gateway && { ls tests/unit/test_izin_*.py tests/unit/test_fpb_*.py 2>/dev/null; grep -rlnE "tagihan_aksi|hitung_debit|bills_service|create_bill_v2|post_bill|_emit_bill_debit_lines|account_roles|COGS_SALES|journals|reklas|reclassify|permission_middleware|inventaris_rute" tests/unit --include=*.py; } | sort -u | tr "\n" " ")
echo "=== tes terfokus ($(echo $F | wc -w) berkas)"
timeout 1500 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F tests/unit/test_pagar_rute_izin.py > $L/unit.log 2>&1
grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -15
hash_app sebelum-nyata
G=milkyhoop-dev-api_gateway; IMG=$(docker inspect $G --format "{{.Config.Image}}"); E=$(mktemp /root/.fp-XXXX); chmod 600 $E
printf "URL=%s\n" "$(docker exec $G printenv DATABASE_URL)" > $E
echo "=== harness NYATA kaos di WORKTREE (ROLLBACK; WAJIB LULUS)"
timeout 600 docker run --rm --network milkyhoop_dev_network --env-file $E -v $W/backend:/app/backend:ro -v $W/scripts/ops/uji-nyata-fpb-debit-penuh.py:/h.py:ro \
  -v $L/out:/out -w /app/backend/api_gateway --entrypoint python $IMG -I /h.py /out/nyata.json > $L/h.log 2>&1 < /dev/null; echo "rc=$?"; tail -12 $L/h.log
echo "=== harness NYATA kaos di DASAR (ROLLBACK; WAJIB GAGAL = kontrol merah)"
salin; dasar
timeout 600 docker run --rm --network milkyhoop_dev_network --env-file $E -v $POHON/backend:/app/backend:ro -v $W/scripts/ops/uji-nyata-fpb-debit-penuh.py:/h.py:ro \
  -v $L/out0:/out -w /app/backend/api_gateway --entrypoint python $IMG -I /h.py /out/nyata.json > $L/h0.log 2>&1 < /dev/null; echo "rc=$?"; tail -12 $L/h0.log
rm -rf $POHON; rm -f $E
hash_app sesudah-nyata
echo SELESAI_SEMUA
