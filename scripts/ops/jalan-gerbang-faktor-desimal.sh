#!/usr/bin/env bash
# Faktor konversi desimal (putusan pemilik LANGSUNG 5 Nov): SEMUA tes beririsan + sabotase (validator menerima 7 desimal -> WAJIB merah)
# + harness NYATA kaos (ROLLBACK) BARU lulus / DASAR gagal.
W=${W:-/root/mh-faktor}; cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master)"
L=/root/logs/faktor-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L/out $L/out0; chmod 777 $L/out $L/out0; echo "log: $L"
F="tests/unit/test_aturan_ubah_tipe.py tests/unit/test_faktor_konversi_desimal.py tests/unit/test_harga_jual.py tests/unit/test_hpp_neto.py tests/unit/test_item_aliases.py tests/unit/test_item_field_utuh.py tests/unit/test_item_nama_jenis.py tests/unit/test_item_sku_kode_status.py tests/unit/test_kirim_non_stok.py tests/unit/test_konversi_terkunci.py tests/unit/test_milkybook_t0.py tests/unit/test_nama_barang_409.py tests/unit/test_pemindahan_barang.py tests/unit/test_q018_akui_pendapatan_nonstok.py tests/unit/test_qty_dasar.py tests/unit/test_saldo_awal_stok.py tests/unit/test_so_kirim_pratinjau.py "
timeout 1200 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F > $L/unit.log 2>&1; grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -10
echo "=== sabotase: validator tak memeriksa jumlah desimal (WAJIB merah)"
P=$(mktemp -d /root/.sabfd-XXXX); mkdir -p $P/backend; rsync -a --exclude __pycache__ --exclude .pytest_cache backend/api_gateway $P/backend/; ln -s $W/backend/migrations $P/backend/migrations
S=$P/backend/api_gateway/app/services/satuan_dasar.py; sed -i 's/ or f.as_tuple().exponent < -6:/:/' $S; grep -c "as_tuple().exponent < -6" $S | sed 's/^/sisa_penanda=/'
timeout 600 scripts/jalankan_unit.sh $P -q -p no:cacheprovider tests/unit/test_faktor_konversi_desimal.py 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -6
rm -rf $P
G=milkyhoop-dev-api_gateway; IMG=$(docker inspect $G --format "{{.Config.Image}}"); E=$(mktemp /root/.fd-XXXX); chmod 600 $E
printf "URL=%s\n" "$(docker exec $G printenv DATABASE_URL)" > $E
nyata() { timeout 600 docker run --rm --network milkyhoop_dev_network --env-file $E -v $1/backend:/app/backend:ro \
  -v $W/scripts/ops/uji-nyata-faktor-desimal.py:/h.py:ro -v $2:/out -w /app/backend/api_gateway --entrypoint python $IMG -I /h.py /out/nyata.json \
  > $3 2>&1 < /dev/null; echo "rc=$?"; grep -E "^(OK|GAGAL|HASIL)" $3 | tail -20; }
echo "=== harness NYATA kaos di WORKTREE (ROLLBACK; WAJIB LULUS)"; nyata $W $L/out $L/h.log
echo "=== harness NYATA kaos di DASAR deploy/master (ROLLBACK; WAJIB GAGAL)"
DASAR=$(mktemp -d /root/.fddasar-XXXX); git archive deploy/master backend/api_gateway | tar -x -C $DASAR; nyata $DASAR $L/out0 $L/h0.log
rm -rf $DASAR; rm -f $E; echo SELESAI_SEMUA
