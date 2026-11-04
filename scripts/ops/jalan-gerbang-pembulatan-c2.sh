#!/usr/bin/env bash
# c-1 + c-2 PEMBULATAN. TAHAP=1 (bawaan): uji scratch V479 (kontrol merah di dalamnya) + tes terfokus LEBAR -- tanpa DDL di prod.
# TAHAP=2 (SESUDAH jendela migrasi V479): harness NYATA kaos (ROLLBACK) di WORKTREE (WAJIB LULUS) DAN di pohon DASAR deploy/master (WAJIB
# GAGAL = kontrol merah). Jalankan lewat nohup + log.
W=${W:-/root/mh-pembulatan-c2}
cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master)" && (git merge-base --is-ancestor deploy/master HEAD && echo on-top || echo MASTER-MOVED)
L=/root/logs/pembulatan-c2-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L/out $L/out0; chmod 777 $L/out $L/out0; echo "log: $L"
if [ "${TAHAP:-1}" = 1 ]; then
echo "=== uji scratch V479"
M=$W/backend/migrations timeout 900 scripts/ops/uji-scratch-v479.sh > $L/scratch.log 2>&1; echo "rc=$?"; grep -E "^(PASS|FAIL|SKIP|RINGKAS)|scratch dihapus" $L/scratch.log
F=$(cd backend/api_gateway && { ls tests/unit/test_pembulatan_pembelian.py tests/unit/test_uang_presisi.py; grep -rlnE "tagihan_aksi|dasar_tagihan|blok_posting|rencana_posting|baris_posting_nd|nota_debit|vendor_credit|bills_service|create_bill_v2|post_bill|ringkasan_cetak|role_resolver|PERAN_SEMAI|account_roles|uang_presisi|tolak_bersen|receive_payments|bill_payments|bayar_keluar" tests/unit --include=*.py; } | sort -u | tr "\n" " ")
echo "=== tes terfokus ($(echo $F | wc -w) berkas)"
timeout 1800 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F > $L/unit.log 2>&1
grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -25
echo SELESAI_SEMUA; exit 0
fi
G=milkyhoop-dev-api_gateway; IMG=$(docker inspect $G --format "{{.Config.Image}}"); E=$(mktemp /root/.c2-XXXX); chmod 600 $E
printf "URL=%s\n" "$(docker exec $G printenv DATABASE_URL)" > $E
nyata() { timeout 600 docker run --rm --network milkyhoop_dev_network --env-file $E \
  -v $1/backend:/app/backend:ro -v $W/scripts/ops/uji-nyata-pembulatan-c2.py:/h.py:ro \
  -v $2:/out -w /app/backend/api_gateway --entrypoint python $IMG -I /h.py /out/nyata.json > $3 2>&1 < /dev/null; echo "rc=$?"; grep -vE "^(INFO|DEBUG|WARNING)" $3 | tail -30; }
echo "=== harness NYATA kaos di WORKTREE (ROLLBACK; WAJIB LULUS)"
nyata $W $L/out $L/h.log
echo "=== harness NYATA kaos di DASAR deploy/master (ROLLBACK; WAJIB GAGAL = kontrol merah)"
DASAR=$(mktemp -d /root/.c2dasar-XXXX); git archive deploy/master backend/api_gateway | tar -x -C $DASAR
nyata $DASAR $L/out0 $L/h0.log
rm -rf $DASAR; rm -f $E
echo SELESAI_SEMUA
