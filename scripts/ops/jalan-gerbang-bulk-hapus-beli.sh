#!/usr/bin/env bash
# B1b-2: HIJAU-0 (karakterisasi hapus draf tunggal 4 modul di DASAR master = WAJIB hijau) + MERAH-1 + sabotase S1..S8 (WAJIB merah)
# + gate_izin_rute + tes terfokus + harness NYATA kaos (ROLLBACK, penanda TES E2E; kontainer sekali pakai, image gateway yang sama).
W=${W:-/root/mh-bulkhapus}
cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master)" && (git merge-base --is-ancestor deploy/master HEAD && echo on-top || echo MASTER-MOVED)
L=/root/logs/bulkhapus-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L/out; chmod 777 $L/out; echo "log: $L"
hash_app() { echo "HASH_APP $1 $(find backend/api_gateway/app -type f -not -path "*/__pycache__/*" | sort | xargs sha256sum | sha256sum | cut -c1-64)"; }
KAR="tests/unit/test_beli_hapus_karakterisasi.py"
SPEK="tests/unit/test_bulk_hapus_beli_b1b2.py tests/unit/test_urutan_rute_bulk.py"
salin() { POHON=$(mktemp -d /root/.sabhb-XXXX); mkdir -p $POHON/backend
  rsync -a --exclude .pytest_cache --exclude __pycache__ backend/api_gateway $POHON/backend/ && ln -s $W/backend/migrations $POHON/backend/migrations; }
dasar() { for f in app/services/bills_service.py app/routers/bill_payments.py app/routers/vendor_deposits.py app/routers/vendor_credits.py app/services/bulk.py app/main.py app/middleware/permission_middleware.py tests/unit/inventaris_rute_tulis.txt; do
  git show deploy/master:backend/api_gateway/$f > $POHON/backend/api_gateway/$f; done; rm -f $POHON/backend/api_gateway/app/routers/bulk_hapus_beli.py; }
hash_app awal
echo "=== HIJAU-0 karakterisasi di DASAR deploy/master (WAJIB hijau)"
salin; dasar; timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $KAR 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in |Error" | tail -6; rm -rf $POHON
echo "=== MERAH-1 dasar deploy/master + spek baru (WAJIB merah)"
salin; dasar; timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $SPEK 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in |Error" | tail -6; rm -rf $POHON
for s in S1 S2 S3 S4 S5 S6 S7 S8; do
  echo "=== sabotase $s (WAJIB merah)"
  salin; if python3 scripts/ops/sabotase-bulk-hapus-beli.py $POHON/backend/api_gateway $s; then
    timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $KAR $SPEK tests/unit/test_bulk_f1.py 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -5
  else echo "FAIL sabotase $s tak terpasang"; fi; rm -rf $POHON
done
declare -A GT=([G1]=test_as_gp2_idem_tagihan.py [G2]=test_as_gp3_aksi_bayar.py [G3]=test_as_gp4.py [G4]=test_g5_bills_po.py [G5]=test_lampiran_yatim.py
  [G6]=test_p2p3a_nd_nk_ubah_hapus.py [G7]=test_tagihan_aksi.py [G8]=test_um_vendor_pratinjau.py [G9]=test_bulk_hapus_beli_b1b2.py [G10]=test_p2p3a_nd_nk_ubah_hapus.py)
for g in G1 G2 G3 G4 G5 G6 G7 G8 G9 G10; do
  echo "=== sabotase penjaga $g -> ${GT[$g]} (WAJIB merah)"
  salin; if python3 scripts/ops/sabotase-bulk-hapus-beli.py $POHON/backend/api_gateway $g; then
    timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider tests/unit/${GT[$g]} tests/unit/test_p2p3a_nd_nk_ubah_hapus.py 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -6
  else echo "FAIL sabotase $g tak terpasang"; fi; rm -rf $POHON
done
echo "=== gate_izin_rute.py (== mh-gerbang-merge: kontainer sekali-pakai :ro, skrip dari dasar)"
IZG=$(mktemp /tmp/izg-XXXX.py); git show deploy/master:scripts/gate_izin_rute.py > $IZG
timeout 300 docker run --rm -i --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -v $W/backend:/app/backend:ro -w /app \
  milkyhoop-dev-api_gateway:latest python - < $IZG 2>&1 | grep -E "^(HIJAU|MERAH)|\[X\]" | tail -6; rm -f $IZG
F=$(cd backend/api_gateway && { ls tests/unit/test_bulk*.py tests/unit/test_izin_*.py tests/unit/test_bill*.py tests/unit/test_vendor_*.py tests/unit/test_bayar_*.py 2>/dev/null; grep -rlnE "bills_service|routers import bills|bill_payments|vendor_deposits|vendor_credits|bulk_hapus_beli|services import bulk|routers import bulk|permission_middleware|inventaris_rute_tulis|idem_buat" tests/unit --include=*.py; } | sort -u | tr "\n" " ")
echo "=== tes terfokus ($(echo $F | wc -w) berkas)"
timeout 1200 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F tests/unit/test_pagar_rute_izin.py tests/unit/test_urutan_rute_bulk.py > $L/unit.log 2>&1
grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -15
[ "${LEWATI_NYATA:-0}" = 1 ] && { echo "harness NYATA dilewati (kode app tak berubah sejak jalan LULUS sebelumnya)"; echo SELESAI_SEMUA; exit 0; }
hash_app sesudah-unit-sebelum-nyata
G=milkyhoop-dev-api_gateway; IMG=$(docker inspect $G --format "{{.Config.Image}}"); E=$(mktemp /root/.hb-XXXX); chmod 600 $E
printf "URL=%s\n" "$(docker exec $G printenv DATABASE_URL)" > $E
echo "=== harness NYATA kaos (ROLLBACK, TES E2E)"
timeout 600 docker run --rm --network milkyhoop_dev_network --env-file $E -v $W/backend:/app/backend:ro -v $W/scripts/ops/uji-nyata-bulk-hapus-beli.py:/h.py:ro \
  -v $L/out:/out -w /app/backend/api_gateway --entrypoint python $IMG -I /h.py /out/nyata.json > $L/h.log 2>&1 < /dev/null; echo "rc=$?"; tail -30 $L/h.log
rm -f $E
hash_app sesudah-nyata
echo SELESAI_SEMUA
