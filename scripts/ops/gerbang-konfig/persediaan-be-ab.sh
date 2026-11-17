# Konfig gerbang paket Persediaan BE-AB (17 Nov 2026): BE-A Batch standar SO + BUG-164 (item_batches, batch_aksi, SA batch, void SA pindah_batch) + BE-B (108 kartu stok campur, 075 isyarat per gudang,
# 221 riwayat Penyesuaian). TANPA migrasi. HARNESS = pembungkus dua harness salinan (rc=0 hanya bila KEDUANYA 0). Pemisah sabotase = "|", sed memakai "@".
W=/root/mh-bea; CABANG=feat/persediaan-be-ab-r2; NAMA_JENDELA=pbeab
POLA_UNIT='batch_aksi|item_batches|stok_baris|teks_laporan_persediaan|stok_sesuai|teks_galat|be_b_kartu|bug272|izin_rute|penyesuaian|inventory_helpers|batch_standar'
HARNESS=scripts/ops/uji-salinan-persediaan-be-ab.sh
PENANDA_BERKAS=services/batch_aksi.py; PENANDA='def rencana_sesuaikan'
A=backend/api_gateway/app
T1=tests/unit/test_batch_standar_so_164.py
T2=tests/unit/test_be_b_kartu_riwayat.py
T3=tests/unit/test_stok_baris.py
SAB=(
 "a1 adjust tanpa penulis posting|$A/routers/item_batches.py|s@SAR\._tulis_posting(conn, ctx, sa_id, r_post)@(None, None)@|$T1"
 "a2 kartu stok tanpa tanda batch|$A/routers/item_batches.py|s@UPDATE inventory_ledger SET batch_id@UPDATE inventory_ledger SET notes@|$T1"
 "a3 pagar batch tak cukup hilang|$A/services/batch_aksi.py|s@    if sebelum + ubah < 0:@    if False:@|$T1"
 "a4 pagar stok barang hilang|$A/services/batch_aksi.py|s@    if stok + ubah < 0:@    if False:@|$T1"
 "a5 void SA menggeser batch dua kali|$A/routers/stock_adjustments.py|s@pindah_batch=False,@pindah_batch=True,@|$T1"
 "a6 helper pembalik tak menghormati pindah_batch|$A/services/inventory_helpers.py|s@if batch_id and pindah_batch:@if batch_id:@|$T1"
 "a7 kedaluwarsa tak dihitung|$A/services/batch_aksi.py|s@    if status == \"active\" and expiry and expiry < hari_ini:@    if False:@|$T1"
 "a8 karantina dua kali tersedia|$A/services/batch_aksi.py|s@a\[\"karantina\"\] = tak(\"Batch sudah dikarantina.\") if st == \"quarantine\" else@a[\"karantina\"] = dict(ada) if True else@|$T1"
 "a9 saringan kedaluwarsa tanpa cabang lewat-tanggal|$A/services/batch_aksi.py|s@ OR ({a}.status = 'active' AND {a}.expiry_date < {h}::date))@)@|$T1"
 "a10 segera kedaluwarsa 30 hari jadi 0|$A/services/batch_aksi.py|s@BATAS_SEGERA = 30@BATAS_SEGERA = 0@|$T1"
 "a11 status tak dipetakan ke kunci|$A/routers/item_batches.py|s@if status in BA.STATUS_KE_KUNCI else \[\]@if False else []@|$T1"
 "a12 aksi tak sah lolos|$A/routers/item_batches.py|s@    if aksi and aksi not in BA.KUNCI:@    if False:@|$T1"
 "a13 PATCH status tanpa rencana|$A/routers/item_batches.py|s@        if body.status in (\"quarantine\", \"active\"):@        if False:@|$T1"
 "a14 field blok pratinjau hilang|$A/services/batch_aksi.py|s@\"field\": KODE_FIELD.get(x\[\"code\"\])@\"field\": None@|$T1"
 "a15 teks stok gudang hilang|$A/services/batch_aksi.py|s@\"stock_text\": stock_text(ws.get(\"quantity\"), unit), \"available_text\"@\"stock_text\": None, \"available_text\"@|$T1"
 "b1 teks campur selalu None|$A/services/teks_laporan_persediaan.py|s@    if not bagian:@    if True:@|$T2"
 "b2 history tanpa konversi|$A/routers/items.py|s@presisi_tenant(conn, tenant_id), konv)@presisi_tenant(conn, tenant_id))@|$T2"
 "b3 riwayat SA kata teknis|$A/services/stok_sesuai.py|s@diterbitkan\", sa\[\"posted_by\"\],@diposting\", sa[\"posted_by\"],@|$T2"
 "b4 isyarat gudang baris diabaikan|$A/services/stok_baris.py|s@        eff = x.get(\"warehouse_id\") or warehouse_id@        eff = warehouse_id@|$T3"
 "b5 kalimat isyarat tanpa nama gudang|$A/services/teks_galat.py|s@{f' di {gudang}' if gudang else ''} per @ per @|$T3"
 "b6 pratinjau SO tak meneruskan gudang kepala|$A/routers/sales_orders.py|s@getattr(body, \"warehouse_id\", None))  # BE-B@None)  # BE-B@|$T3"
)
