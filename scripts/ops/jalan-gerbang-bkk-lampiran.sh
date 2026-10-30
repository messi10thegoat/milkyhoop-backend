#!/usr/bin/env bash
# BKK (cetak Bukti Kas Keluar tanpa stempel) + LAMPIRAN-PEMBELIAN F1 (/api/lampiran bill|payment_out): HIJAU-0 (tes lama di DASAR master = WAJIB
# hijau) + MERAH-1 (dasar + spek baru/tertarget = WAJIB merah) + sabotase K1..K3 L1..L5 (WAJIB merah) + gate_izin_rute + tes terfokus + harness
# NYATA kaos (ROLLBACK). HASH app/ dicatat awal / sebelum nyata / sesudah nyata.
W=${W:-/root/mh-bkk}
cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master)" && (git merge-base --is-ancestor deploy/master HEAD && echo on-top || echo MASTER-MOVED)
L=/root/logs/bkk-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L/out; chmod 777 $L/out; echo "log: $L"
hash_app() { echo "HASH_APP $1 $(find backend/api_gateway/app -type f -not -path "*/__pycache__/*" | sort | xargs sha256sum | sha256sum | cut -c1-64)"; }
LAMA="tests/unit/test_lampiran_dokumen.py tests/unit/test_lampiran_so_keluarga.py tests/unit/test_lampiran_induk.py tests/unit/test_lampiran_1b_download.py"
SPEK="tests/unit/test_lampiran_pembelian_f1.py tests/unit/test_p2p_cetak_pembelian.py tests/unit/test_cetak_um_grn.py"
salin() { POHON=$(mktemp -d /root/.sabbkk-XXXX); mkdir -p $POHON/backend
  rsync -a --exclude .pytest_cache --exclude __pycache__ backend/api_gateway $POHON/backend/ && ln -s $W/backend/migrations $POHON/backend/migrations; }
dasar() { for f in app/routers/lampiran_dokumen.py app/routers/sales_order_lampiran.py app/middleware/permission_middleware.py \
                   app/services/cetak_payment_out.py app/services/cetak_vendor_deposit.py app/templates/pdf/kwitansi.html; do
  git show deploy/master:backend/api_gateway/$f > $POHON/backend/api_gateway/$f; done; }
hash_app awal
echo "=== HIJAU-0 tes lama di DASAR deploy/master (WAJIB hijau; berkas cetak lama dari dasar juga)"
salin; dasar; for t in tests/unit/test_p2p_cetak_pembelian.py tests/unit/test_cetak_um_grn.py; do git show deploy/master:backend/api_gateway/$t > $POHON/backend/api_gateway/$t; done
timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $LAMA tests/unit/test_p2p_cetak_pembelian.py tests/unit/test_cetak_um_grn.py 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in |Error" | tail -6; rm -rf $POHON
echo "=== MERAH-1 dasar deploy/master + spek baru/tertarget (WAJIB merah)"
for t in $SPEK; do echo "--- $t"; salin; dasar; timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $t 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in |Error" | tail -8; rm -rf $POHON; done
for s in K1 K2 K3 L1 L2 L3 L4 L5; do
  echo "=== sabotase $s (WAJIB merah)"
  salin; if python3 scripts/ops/sabotase-bkk-lampiran.py $POHON/backend/api_gateway $s; then
    timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $SPEK $LAMA 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -5
  else echo "FAIL sabotase $s tak terpasang"; fi; rm -rf $POHON
done
echo "=== gate_izin_rute.py (== mh-gerbang-merge: kontainer sekali-pakai :ro, skrip dari dasar)"
IZG=$(mktemp /tmp/izg-XXXX.py); git show deploy/master:scripts/gate_izin_rute.py > $IZG
timeout 300 docker run --rm -i --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -v $W/backend:/app/backend:ro -w /app \
  milkyhoop-dev-api_gateway:latest python - < $IZG 2>&1 | grep -E "^(HIJAU|MERAH)|\[X\]" | tail -6; rm -f $IZG
F=$(cd backend/api_gateway && { ls tests/unit/test_izin_*.py tests/unit/test_lampiran*.py 2>/dev/null; grep -rlnE "lampiran_dokumen|sales_order_lampiran|JENIS_KELUARGA|permission_middleware|izin_untuk_jalur|ROUTE_PERMISSIONS|kwitansi|cetak_payment_out|cetak_vendor_deposit|render_receipt|render_payment_out|bill_payments|attachments|inventaris_rute" tests/unit --include=*.py; } | sort -u | tr "\n" " ")
echo "=== tes terfokus ($(echo $F | wc -w) berkas)"
timeout 1500 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F tests/unit/test_pagar_rute_izin.py > $L/unit.log 2>&1
grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -15
hash_app sebelum-nyata
G=milkyhoop-dev-api_gateway; IMG=$(docker inspect $G --format "{{.Config.Image}}"); E=$(mktemp /root/.bkk-XXXX); chmod 600 $E
printf "URL=%s\n" "$(docker exec $G printenv DATABASE_URL)" > $E
echo "=== harness NYATA kaos (ROLLBACK, TES E2E LAMPIRAN-BELI + BKK)"
timeout 600 docker run --rm --network milkyhoop_dev_network --env-file $E -v $W/backend:/app/backend:ro -v $W/scripts/ops/uji-nyata-lampiran-beli.py:/h.py:ro \
  -v $L/out:/out -w /app/backend/api_gateway --entrypoint python $IMG -I /h.py /out/nyata.json > $L/h.log 2>&1 < /dev/null; echo "rc=$?"; tail -32 $L/h.log
rm -f $E
hash_app sesudah-nyata
echo SELESAI_SEMUA
