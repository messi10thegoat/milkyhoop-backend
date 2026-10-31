#!/usr/bin/env bash
# GRN-SENT-NONE (putusan MASTER A, 31 Okt 2026): HIJAU-0 (tes lama di DASAR) + MERAH-1 (spek baru di DASAR) + sabotase S1..S3 + gate_izin_rute
# + tes terfokus. HASH app/ awal / akhir.
W=${W:-/root/mh-lampf2b}
cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master)" && (git merge-base --is-ancestor deploy/master HEAD && echo on-top || echo MASTER-MOVED)
L=/root/logs/grnsent-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L; echo "log: $L"
hash_app() { echo "HASH_APP $1 $(find backend/api_gateway/app -type f -not -path "*/__pycache__/*" | sort | xargs sha256sum | sha256sum | cut -c1-64)"; }
LAMA="tests/unit/test_grn.py tests/unit/test_grn_seragam.py tests/unit/test_grn_kolom_dok.py"
SPEK="tests/unit/test_kontrak_sent_state_bundel.py"
salin() { POHON=$(mktemp -d /root/.sabgs-XXXX); mkdir -p $POHON/backend
  rsync -a --exclude .pytest_cache --exclude __pycache__ backend/api_gateway $POHON/backend/ && ln -s $W/backend/migrations $POHON/backend/migrations; }
dasar() { git show deploy/master:backend/api_gateway/app/services/grn_daftar.py > $POHON/backend/api_gateway/app/services/grn_daftar.py; }
hash_app awal
echo "=== HIJAU-0 tes lama di DASAR"
salin; dasar; timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $LAMA 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in |Error" | tail -6; rm -rf $POHON
echo "=== MERAH-1 dasar + spek baru (WAJIB merah)"
salin; dasar; timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $SPEK 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -8; rm -rf $POHON
for s in S1 S2 S3; do
  echo "=== sabotase $s (WAJIB merah)"
  salin; if python3 scripts/ops/sabotase-grn-sent-none.py $POHON/backend/api_gateway $s; then
    timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $SPEK 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -5
  else echo "FAIL sabotase $s tak terpasang"; fi; rm -rf $POHON
done
echo "=== gate_izin_rute.py"
IZG=$(mktemp /tmp/izg-XXXX.py); git show deploy/master:scripts/gate_izin_rute.py > $IZG
timeout 300 docker run --rm -i --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -v $W/backend:/app/backend:ro -w /app \
  milkyhoop-dev-api_gateway:latest python - < $IZG 2>&1 | grep -E "^(HIJAU|MERAH)|\[X\]" | tail -6; rm -f $IZG
F=$(cd backend/api_gateway && { ls tests/unit/test_izin_*.py tests/unit/test_grn*.py tests/unit/test_kontrak_sent*.py 2>/dev/null; grep -rlnE "grn_daftar|goods_receipts|dokumen_bundel|dokumen_bagikan|po_daftar|nota_debit_daftar|pembayaran_keluar_daftar|tagihan_daftar|bacaBundel|\"sent\"" tests/unit --include=*.py; } | sort -u | tr "\n" " ")
echo "=== tes terfokus ($(echo $F | wc -w) berkas)"
timeout 1500 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F tests/unit/test_pagar_rute_izin.py > $L/unit.log 2>&1
grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -15
hash_app akhir
echo SELESAI_SEMUA
