#!/usr/bin/env bash
# GRN aturan D (gabungan sisi penerimaan BACKEND2 + sisi tagihan BACKEND): tes terfokus + harness rollback-only kaos (baru + merah).
W=/root/mh-grnd
cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master)" && (git merge-base --is-ancestor deploy/master HEAD && echo on-top || echo MASTER-MOVED)
echo "=== tes terfokus"
timeout 600 scripts/jalankan_unit.sh $W -q -p no:cacheprovider tests/unit/test_grn.py tests/unit/test_grn_pasang.py tests/unit/test_tagihan_grn.py \
  tests/unit/test_tagihan_grn_d.py tests/unit/test_nomor_jurnal_satu_ruang.py tests/unit/test_idem_sumber_basi.py tests/unit/test_bulk_f5.py \
  tests/unit/test_rute_endpoint_bukan_helper.py tests/unit/test_pagar_rute_izin.py tests/unit/test_izin_rute_pratinjau.py \
  tests/unit/test_t10b_tanggal_bisnis_arap.py 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in |Error" | head -15
G=milkyhoop-dev-api_gateway; IMG=$(docker inspect $G --format "{{.Config.Image}}"); E=$(mktemp /root/.grnd-XXXX); chmod 600 $E
printf "URL=%s\n" "$(docker exec $G printenv DATABASE_URL)" > $E
jalan() { timeout 600 docker run --rm --network milkyhoop_dev_network --env-file $E -v $W/backend:/app/backend:ro -v $1:/h.py:ro \
  -w /app/backend/api_gateway --entrypoint python $IMG -I /h.py "${@:2}" 2>&1; echo "rc=$?"; }
for m in "" merah merah_fifo merah_d; do
  echo "=== e2e $m"; jalan $W/scripts/ops/uji-nyata-grn-e2e.py baru $m | grep -E "^(PASS|FAIL|SELESAI)|^rc=|Traceback|Error" | tail -60
done
echo "=== harness GRN lama (baru)"; jalan $W/scripts/ops/uji-nyata-grn.py baru | grep -E "^(FAIL|SELESAI)|^rc=|Traceback" | tail -8
rm -f $E
echo "sisa PO TES E2E GRN: $(docker exec -i milkyhoop-dev-postgres-1 psql -U postgres -d milkydb -At -c "SELECT count(*) FROM purchase_orders WHERE po_number LIKE 'TES E2E GRN%' OR ref_no LIKE 'TES E2E GRN%'") flag: $(docker exec -i milkyhoop-dev-postgres-1 psql -U postgres -d milkydb -At -c "SELECT count(*) FROM tenant_features WHERE feature = 'penerimaan_barang_v1'")"
echo SELESAI_SEMUA
