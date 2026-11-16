# Konfig gerbang unit BUG-272(A) (GET /api/reports/inventory-valuation: nilai persediaan per tanggal per barang/gudang + rekonsiliasi ke GL INVENTORY_MERCHANDISE). Tiap sabotase WAJIB membuat tes merah. Tanpa migrasi, hanya baca.
W=/root/mh-b272; CABANG=feat/bug272-nilai-persediaan; NAMA_JENDELA=b272
POLA_UNIT='bug272|nilai_persediaan|laporan|reports|izin_rute|sql_placeholder'
HARNESS=scripts/ops/uji-salinan-b272.sh
PENANDA_BERKAS=services/laporan_nilai_persediaan.py; PENANDA='SQL_WAC'
A=backend/api_gateway/app; T=backend/api_gateway/tests/unit/test_bug272_nilai_persediaan.py; LF=$A/services/laporan_nilai_persediaan.py; RF=$A/routers/reports.py
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
)
