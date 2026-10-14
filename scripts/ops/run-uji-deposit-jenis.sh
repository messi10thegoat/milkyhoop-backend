#!/bin/bash
# Runner harness DEPOSIT-JENIS: env gateway (DB) lewat berkas 600, dihapus sesudahnya; nilai tak dicetak.
# $1 = worktree, $2 = skrip harness, $3 = folder keluaran PDF (host). Sesudahnya: pdftotext tiap PDF.
set -u
OUT="$3"; rm -rf "$OUT"; mkdir -p "$OUT"
E=$(mktemp /tmp/env-dj.XXXX); chmod 600 "$E"
docker inspect milkyhoop-dev-api_gateway --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^(DATABASE_URL|MINIO_[A-Z_]+)=' > "$E"
IMG=$(docker inspect milkyhoop-dev-api_gateway --format '{{.Config.Image}}')
docker run --rm --network milkyhoop_dev_network --env-file "$E" -v "$1/backend:/app/backend:ro" -v "$2:/u.py:ro" \
  -v "$OUT:/out" -w /app/backend/api_gateway "$IMG" python /u.py 2>&1 \
  | grep -E "^(JSON|PASS|FAIL|RINGKAS|Traceback|  File|[A-Za-z.]+Error)" | cut -c1-1500
rm -f "$E"
gagal_pdf=0
for f in "$OUT"/*.pdf; do
  [ -e "$f" ] || continue
  t=$(pdftotext -layout "$f" - | tr -s ' \n' ' ')
  n=$(basename "$f" .pdf)
  case "$n" in *pelunasan) harap="Untuk Pelunasan Pesanan";; *) harap="Untuk Uang Muka Pesanan";; esac
  for frasa in "Bukti Penerimaan" "$harap" "Total Pembayaran Diterima"; do
    if echo "$t" | grep -qiF "$frasa"; then echo "PASS PDF $n memuat '$frasa'"; else echo "FAIL PDF $n TANPA '$frasa'"; gagal_pdf=$((gagal_pdf+1)); fi
  done
  if echo "$t" | grep -qF "Total Uang Muka Diterima"; then echo "FAIL PDF $n masih 'Total Uang Muka Diterima'"; gagal_pdf=$((gagal_pdf+1)); fi
done
berkas=$(ls "$OUT"/*.pdf 2>/dev/null | wc -l)
# harap 5 PDF: grapgrap pelunasan+dp, kaos pelunasan+dp, kaos-baru-pelunasan
if [ "$berkas" -lt 5 ]; then echo "FAIL PDF hanya $berkas berkas (harap 5)"; gagal_pdf=$((gagal_pdf+1)); fi
echo "RINGKAS PDF gagal=$gagal_pdf berkas=$berkas"
