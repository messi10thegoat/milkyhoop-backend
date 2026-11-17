# Konfig gerbang unit BUG-272 (A: GET /api/reports/inventory-valuation nilai persediaan + rekonsiliasi GL; teks tampil server aditif di profitability/items dan kartu stok K9). Tiap sabotase WAJIB membuat tes merah. Tanpa migrasi, hanya baca.
W=/root/mh-b272; CABANG=feat/bug272-nilai-persediaan; NAMA_JENDELA=b272
POLA_UNIT='bug272|nilai_persediaan|teks_persediaan|laporan|reports|profitab|izin_rute|sql_placeholder'
HARNESS=scripts/ops/uji-salinan-b272.sh
PENANDA_BERKAS=services/laporan_nilai_persediaan.py; PENANDA='SQL_WAC'
A=backend/api_gateway/app; T=backend/api_gateway/tests/unit/test_bug272_nilai_persediaan.py; T2=backend/api_gateway/tests/unit/test_bug272_teks_persediaan.py
LF=$A/services/laporan_nilai_persediaan.py; RF=$A/routers/reports.py; TF=$A/services/teks_laporan_persediaan.py; PF=$A/routers/reports_profitability.py; IF=$A/routers/items.py
SAB=(
 "v1 HPP urut movement_date bukan urutan aplikasi|$LF|s#ORDER BY il.product_id, il.created_at DESC, il.id DESC#ORDER BY il.product_id, il.movement_date DESC#|$T"
 "v2 per tanggal diabaikan di ledger|$LF|s#il.movement_date <= \$2#TRUE#g|$T"
 "v3 qty tak disaring gudang|$LF|s# AND (\$3::uuid IS NULL OR il.warehouse_id = \$3)##g|$T"
 "v4 ledger tanpa saring tenant|$LF|s#WHERE il.tenant_id = \$1 AND il.movement_date#WHERE il.movement_date#|$T"
 "v5 GL tanpa jurnal efektif|$LF|s# AND is_effective_journal(je.id)##|$T"
 "v6 GL tanpa batas tanggal|$LF|s# AND je.journal_date <= \$3##|$T"
 "v7 toleransi rekonsiliasi nol (pembulatan jadi selisih)|$LF|s#Decimal(\"0.005\")#Decimal(\"0\")#|$T"
 "v8 selisih disembunyikan (selalu cocok)|$LF|s#ok = abs(sel) <= tol#ok = True#|$T"
 "v9 rekonsiliasi hanya barang tersaring|$LF|s#semua = await conn.fetch(SQL_QTY_SEMUA, tid, per_tanggal)#semua = [r for r in await conn.fetch(SQL_QTY_BARANG, tid, per_tanggal, None)]#|$T"
 "v10 batas baris tak berlaku|$LF|s#if len(baris) > BATAS_BARIS:#if False:#|$T"
 "v11 barang tanpa peran dipetakan dikarang cocok|$LF|s#if gl is None:#if False:#|$T"
 "v12 qty nol tampil bawaan|$LF|s#if sertakan_nol or _d(r\[\"qty\"\]) != 0#if True#|$T"
 "v13 laporan baca-tulis (tanpa readonly)|$RF|/async def get_inventory_valuation/,/return {\"success\": True, \"data\": data}/s/, conn.transaction(readonly=True)//|$T"
 "v14 HPP satuan nilai persediaan dibulatkan presisi tenant|$LF|s#angka_laporan(_r2(hpp), 2)#angka_laporan(_r2(hpp), presisi)#|$T"
 "x1 teks uang tanpa kurung negatif|$TF|s#return None if x is None else tg.angka_laporan(_d(x), presisi)#return None if x is None else str(_d(x))#|$T2"
 "x2 harga satuan dibulatkan (sen hilang)|$TF|s#tg.angka_laporan(_d(x), 2)#tg.angka_laporan(_d(x), 0)#|$T2"
 "x3 persen tanpa tanda minus|$TF|s#return f\"{'-' if d < 0 else ''}{teks}%\"#return f\"{teks}%\"#|$T2"
 "x4 qty tanpa satuan|$TF|s#return BD._q(_d(x)) + (f\" {satuan}\" if satuan else \"\")#return BD._q(_d(x))#|$T2"
 "x5 status rekonsiliasi dibalik|$TF|s#\"minor_drift\": \"Selisih kecil dengan Laba Rugi\"#\"minor_drift\": \"Cocok dengan Laba Rugi\"#|$T2"
 "x6 periode_text penjualan hilang|$TF|s#data\[\"periode_text\"\] = tg.rentang_tanggal(p\[\"start_date\"\], p\[\"end_date\"\])#data[\"periode_text\"] = None#|$T2"
 "x7 arah kartu stok terbalik|$TF|s#arah=\"masuk\" if masuk > 0 and keluar == 0 else#arah=\"keluar\" if masuk > 0 and keluar == 0 else#|$T2"
 "x8 perubahan tanpa tanda plus|$TF|s#f\"+{qty(masuk - keluar, satuan)}\" if masuk - keluar > 0 else#qty(masuk - keluar, satuan) if masuk - keluar > 0 else#|$T2"
 "x9 saldo awal kartu stok tanpa teks|$TF|s#hasil\[\"saldo_awal_text\"\] = qty(hasil\[\"saldo_awal\"\], satuan)#hasil[\"saldo_awal_text\"] = None#|$T2"
 "x10 profitability/items tak memakai teks|$PF|s#TLP.tambah_teks_penjualan_barang({#dict({#|$T2"
 "x11 kartu stok K9 tak memakai teks|$IF|s#TLP.tambah_teks_kartu_stok({#dict({#|$T2"
 "x12 kartu stok K9 tak membaca satuan barang|$IF|s#COALESCE(NULLIF(base_unit, ''), satuan) AS satuan FROM products WHERE id = \$1 AND tenant_id = \$2#id FROM products WHERE id = \$1 AND tenant_id = \$2#|$T2"
)
