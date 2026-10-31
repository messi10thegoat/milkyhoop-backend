#!/usr/bin/env bash
# L7 bagikan massal Pembelian (31 Okt 2026): HIJAU-0 (tes lama versi DASAR di app DASAR) + MERAH-1 (spek baru + tes retarget di app DASAR,
# per berkas) + sabotase S1..S9 + gate_izin_rute + tes terfokus. HASH app/ awal / akhir.
W=${W:-/root/mh-bagikan}
cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master)" && (git merge-base --is-ancestor deploy/master HEAD && echo on-top || echo MASTER-MOVED)
L=/root/logs/bagikanbeli-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L; echo "log: $L"
hash_app() { echo "HASH_APP $1 $(find backend/api_gateway/app -type f -not -path "*/__pycache__/*" | sort | xargs sha256sum | sha256sum | cut -c1-64)"; }
APPUBAH=$(git diff --name-only deploy/master -- backend/api_gateway/app | tr "\n" " ")
RETARGET="tests/unit/test_grn_kolom_dok.py tests/unit/test_pdf_po.py tests/unit/test_p2p_cetak_pembelian.py tests/unit/test_bulk_pembelian_b1a.py tests/unit/test_bulk_f5.py"
SPEK="tests/unit/test_bagikan_massal_beli.py"
salin() { POHON=$(mktemp -d /root/.sabbb-XXXX); mkdir -p $POHON/backend
  rsync -a --exclude .pytest_cache --exclude __pycache__ backend/api_gateway backend/migrations $POHON/backend/; }
dasar() { for f in $APPUBAH; do git show deploy/master:$f > $POHON/$f; done; rm -f $POHON/backend/migrations/V468__*; }
dasar_tes() { for f in $RETARGET; do git show deploy/master:backend/api_gateway/$f > $POHON/backend/api_gateway/$f; done; }
echo "app berubah: $APPUBAH"
hash_app awal
echo "=== HIJAU-0 tes lama (versi dasar) di app DASAR"
salin; dasar; dasar_tes; timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $RETARGET 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in |Error" | tail -6; rm -rf $POHON
echo "=== MERAH-1 app DASAR + spek baru / tes retarget (WAJIB merah, per berkas)"
for t in $SPEK $RETARGET; do echo "--- $t"
  salin; dasar; timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $t 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -4; rm -rf $POHON
done
for s in S1 S2 S3 S4 S5 S6 S7 S8 S9 S10 S11; do
  echo "=== sabotase $s (WAJIB merah)"
  salin; if python3 scripts/ops/sabotase-bagikan-beli.py $POHON/backend $s; then
    timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $SPEK $RETARGET 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -4
  else echo "FAIL sabotase $s tak terpasang"; fi; rm -rf $POHON
done
echo "=== gate_izin_rute.py"
IZG=$(mktemp /tmp/izg-XXXX.py); git show deploy/master:scripts/gate_izin_rute.py > $IZG
timeout 300 docker run --rm -i --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -v $W/backend:/app/backend:ro -w /app \
  milkyhoop-dev-api_gateway:latest python - < $IZG 2>&1 | grep -E "^(HIJAU|MERAH)|\[X\]" | tail -6; rm -f $IZG
F=$(cd backend/api_gateway && { ls tests/unit/test_izin_*.py $SPEK $RETARGET tests/unit/test_kontrak_sent*.py tests/unit/test_u5b_dokumen_nota_kredit.py 2>/dev/null; grep -rlnE "bulk_dokumen|routers import dokumen|routers\.dokumen|dokumen_bagikan|dokumen_bundel|po_daftar|tagihan_daftar|grn_daftar|uang_muka_vendor_daftar|pembayaran_keluar_daftar|nota_debit_daftar|permission_middleware|JENIS_BAGIKAN|document_shares|bills_service|routers import purchase_orders|routers\.purchase_orders" tests/unit --include=*.py; } | sort -u | tr "\n" " ")
echo "=== tes terfokus ($(echo $F | wc -w) berkas)"
timeout 1500 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F tests/unit/test_pagar_rute_izin.py > $L/unit.log 2>&1
grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -15
hash_app akhir
echo SELESAI_SEMUA
