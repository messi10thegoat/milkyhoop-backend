# Konfig gerbang unit K8 (Pemindahan Barang DUA LANGKAH via Transit, tanpa jurnal; gudang sistem V497 disaring dari semua pembaca). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-bk8; CABANG=feat/pemindahan-dua-langkah-transit-r2; NAMA_JENDELA=k8
POLA_UNIT='pemindahan|stock_transfer|gudang|warehouse|transit|izin_rute|inventaris_rute|pagar_rute|permission|stok_baris|stok_sesuai|grn|sj_so|so_pengiriman'
HARNESS=scripts/ops/uji-salinan-k8.sh
PENANDA_BERKAS=services/pemindahan_dua_langkah.py; PENANDA='rencana_terima'
A=backend/api_gateway/app; T=tests/unit/test_pemindahan_barang.py; D2F=$A/services/pemindahan_dua_langkah.py
SAB=(
 "c1 kaki masuk Transit hilang (langsung ke tujuan)|$D2F|s#tipe=KIRIM_MASUK, masuk=True, saldo=saldo, gudang_id=transit_id,#tipe=KIRIM_MASUK, masuk=True, saldo=saldo, gudang_id=h[\"to_warehouse_id\"],#|$T"
 "c2 terima tak keluar dari Transit|$D2F|s#tipe=TERIMA_KELUAR, masuk=False, saldo=saldo - a, gudang_id=transit_id,#tipe=TERIMA_KELUAR, masuk=False, saldo=saldo - a, gudang_id=h[\"from_warehouse_id\"],#|$T"
 "c3 terima melebihi tertunda lolos|$D2F|s#if qty > tertunda\[iid\]:#if False:#|$T"
 "c4 sebagian dianggap tuntas|$D2F|s#tuntas = all(l\[\"tertunda_sesudah\"\] <= _NOL for l in lines)#tuntas = True#|$T"
 "c5 teks diterima sebagian hilang|$D2F|s#if _d(diterima) > _NOL else \"Dikirim\"#if False else \"Dikirim\"#|$T"
 "c6 void setelah terima sebagian lolos|$D2F|s#elif any(_d(i\[\"quantity_received\"\]) > _NOL for i in items):#elif False:#|$T"
 "c7 Kirim menulis jurnal|$D2F|s#return {\"ulang\": False}#await conn.execute(\"INSERT INTO journal_entries (id) VALUES (\$1)\", transfer_id); return {\"ulang\": False}#|$T"
 "c8 gudang Transit tak ditandai sistem|$A/services/gudang_transit.py|s#VALUES (\$1, \$2, \$3, false, true, false, true)#VALUES (\$1, \$2, \$3, false, true, false, false)#|$T"
 "r1 bawaan lupa saring is_system|$A/services/gudang_bawaan.py|s# AND NOT is_system##|$T"
 "r2 so_pengiriman lupa saring is_system|$A/services/so_pengiriman.py|s# AND NOT is_system##|$T"
 "r3 ketersediaan jual menghitung stok Transit|$A/services/stok_baris.py|s#AND (il.warehouse_id IS NULL OR NOT EXISTS (SELECT 1 FROM warehouses ws WHERE ws.id = il.warehouse_id AND ws.is_system))#AND 1=1#|$T"
 "r4 daftar gudang menampilkan Transit|$A/routers/warehouses.py|s#, \"NOT is_system\"\]#]#|$T"
 "r5 ringkasan gudang menghitung Transit|$A/routers/warehouses.py|s#WHERE tenant_id = \$1 AND NOT is_system\"#WHERE tenant_id = \$1\"#|$T"
 "r6 gudang sistem bisa diubah/dihapus|$A/routers/warehouses.py|s#if existing\[\"is_system\"\]:#if False:#|$T"
 "r7 penyesuaian lupa saring is_system|$A/services/stok_sesuai.py|s# AND NOT is_system##|$T"
 "r8 nota debit lupa saring is_system|$A/services/nota_debit_gudang.py|s# AND NOT is_system##|$T"
 "r9 stok awal inventory lupa saring|$A/routers/inventory.py|s# AND NOT is_system##|$T"
 "r10 stok awal items lupa saring|$A/routers/items.py|s# AND NOT is_system##|$T"
 "r11 produksi lupa saring|$A/routers/production.py|s# AND NOT is_system##|$T"
 "r12 transaksi lupa saring|$A/routers/transactions.py|s# AND NOT is_system##|$T"
 "r13 GRN lupa saring (Transit bisa dipilih)|$A/services/grn_rencana.py|s# AND NOT is_system##|$T"
 "r14 surat jalan lupa saring|$A/services/sj_so.py|s# AND NOT is_system##|$T"
 "r15 fase lokasi SO lupa saring|$A/services/so_fase_lokasi.py|s# AND NOT is_system##|$T"
 "r16 pemindahan satu langkah boleh pakai Transit|$A/services/pemindahan_barang.py|s# AND NOT is_system##|$T"
 "r17 metode serah lupa saring|$A/routers/metode_serah.py|s# AND NOT is_system##|$T"
 "r18 pencari gudang chatbot lupa saring|$A/services/unified_agent/entity_resolver.py|s# AND NOT is_system##|$T"
 "r19 nota kredit lupa saring|$A/services/nota_kredit_rencana.py|s# AND NOT is_system##|$T"
 "r20 nota kredit router lupa saring|$A/routers/credit_notes.py|s# AND NOT is_system##|$T"
 "c9 terima sebagian menulis header (trigger DB menolak)|$D2F|s#    if rencana\[\"tuntas\"\]:   \# satu-satunya tulisan header sesudah Kirim yang diizinkan trigger (status berubah + received_date/by)#    await conn.execute(\"UPDATE stock_transfers SET updated_at = NOW() WHERE id = \$1\", transfer_id)\n    if rencana[\"tuntas\"]:#|$T"
 "c10 terima menulis baris item (trigger DB menolak)|$D2F|s#^        total += nilai#        await conn.execute(\"UPDATE stock_transfer_items SET quantity_received = 1 WHERE id = \$1\", it[\"id\"]); total += nilai#|$T"
 "c11 diterima dari kolom beku, bukan ledger|$D2F|s#d\[\"quantity_received\"\] = ada.get(d\[\"item_id\"\], _NOL)#d[\"quantity_received\"] = _d(d[\"quantity_received\"])#|$T"
 "r21 alamat kirim PO: gudang sistem bisa dipilih|$A/services/po_alamat_kirim.py|s# AND NOT is_system##|$T"
)
