#!/usr/bin/env bash
# K1 satuan pemindahan (putusan pemilik LANGSUNG 5 Nov): SEMUA berkas tes beririsan (pelajaran ROW 79) + sabotase (rencana pemindahan
# mengabaikan satuan -> tes 2 Dus = 24 Botol WAJIB merah).
W=${W:-/root/mh-k1}; cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master)"
L=/root/logs/k1-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L; echo "log: $L"
F="tests/unit/test_bulk_pembelian_b1a.py tests/unit/test_grn.py tests/unit/test_grn_seragam.py tests/unit/test_harga_jual.py tests/unit/test_hpp_neto.py tests/unit/test_kosakata_keluar_jual.py tests/unit/test_param_idx_ganda.py tests/unit/test_pemindahan_barang.py tests/unit/test_po_draf_terima.py tests/unit/test_po_tagih_posted.py tests/unit/test_qty_dasar.py tests/unit/test_so_kirim_pratinjau.py tests/unit/test_t10b2_tanggal_bisnis_gaji_stok.py tests/unit/test_tagihan_grn_d.py tests/unit/test_terima_tabel.py tests/unit/test_v426_flag_koreksi_dp_grapgrap.py "
timeout 900 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F > $L/unit.log 2>&1; grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -10
echo "=== sabotase: rencana pemindahan mengabaikan satuan (WAJIB merah)"
P=$(mktemp -d /root/.sabk1-XXXX); mkdir -p $P/backend; rsync -a --exclude __pycache__ --exclude .pytest_cache backend/api_gateway $P/backend/; ln -s $W/backend/migrations $P/backend/migrations
S=$P/backend/api_gateway/app/services/pemindahan_barang.py; sed -i 's/        qty = h_sd\["qty_dasar"\]/        pass/' $S; grep -c 'qty = h_sd\["qty_dasar"\]' $S | sed 's/^/sisa_penanda=/'
timeout 600 scripts/jalankan_unit.sh $P -q -p no:cacheprovider tests/unit/test_pemindahan_barang.py 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -6
rm -rf $P; echo SELESAI_SEMUA
