#!/usr/bin/env bash
# CETAK UM vendor + GRN: gate_izin_rute + tes terfokus (penyentuh dokumen/pdf_service/kwitansi/surat jalan/middleware + SEMUA test_izin_*).
W=/root/mh-cetak; REPO=/root/milkyhoop-dev
cd $W && echo "HEAD $(git rev-parse --short HEAD) master $(git -C $REPO rev-parse --short HEAD)"
L=/root/logs/cetak-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L; echo "log: $L"
echo "=== gate_izin_rute.py"
git -C $REPO show master:scripts/gate_izin_rute.py > /tmp/.izg-cetak.py
docker run --rm -i --network milkyhoop_dev_network --env-file $REPO/.env -v $W/backend:/app/backend:ro -w /app milkyhoop-dev-api_gateway:latest python - < /tmp/.izg-cetak.py 2>&1 | grep -E "^(HIJAU|MERAH): |\[X\]"; rm -f /tmp/.izg-cetak.py
F=$(cd backend/api_gateway && { grep -rlnE "routers import dokumen|routers\.dokumen|dokumen as DK|pdf_service|PDFService|kwitansi|delivery_note|render_receipt|permission_middleware|cetak_|po_cetak" tests/unit --include=*.py; ls tests/unit/test_izin_*.py; } | sort -u | tr "\n" " ")
echo "=== tes terfokus ($(echo $F | wc -w) berkas)"
timeout 900 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F tests/unit/test_pagar_rute_izin.py > $L/unit.log 2>&1
grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -20

salin() { POHON=$(mktemp -d /root/.sabcetak-XXXX); mkdir -p $POHON/backend
  rsync -a --exclude .pytest_cache --exclude __pycache__ backend/api_gateway $POHON/backend/ && ln -s $W/backend/migrations $POHON/backend/migrations
  python3 /tmp/sabotase-cetak.py $POHON/backend/api_gateway $1; }
for s in S_tenant S_cache S_abu S_izin S_stempel S_retur; do
  echo "=== sabotase $s (WAJIB merah)"
  if salin $s; then timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider tests/unit/test_cetak_um_grn.py 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -4
  else echo "FAIL sabotase $s tak terpasang"; fi
  rm -rf $POHON
done
echo SELESAI_SEMUA
