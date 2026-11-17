# Konfig gerbang unit BUG-259 (Lembar Penghitungan Stok: GET /api/warehouses/{id}/count-sheet + /pdf; stok buku per gudang per tanggal dari inventory_ledger; kolom Fisik kosong di PDF). Tiap sabotase WAJIB membuat tes merah. Tanpa migrasi, hanya baca. Bergantung V502 (kolom merek) -> cabang turunan fix/bug260-items-teks-stok.
W=/root/mh-b259; CABANG=feat/bug259-lembar-hitung; NAMA_JENDELA=b259
POLA_UNIT='bug259|lembar_hitung|warehouse|gudang|izin_rute|sql_placeholder'
HARNESS=scripts/ops/uji-salinan-b259.sh
PENANDA_BERKAS=services/lembar_hitung_stok.py; PENANDA='SQL_LEMBAR'
A=backend/api_gateway/app; T=backend/api_gateway/tests/unit/test_bug259_lembar_hitung.py; LF=$A/services/lembar_hitung_stok.py; TP=$A/templates/pdf/lembar_hitung_stok.html; WH=$A/routers/warehouses.py
SAB=(
 "l1 stok tak dibatasi gudang itu|$LF|s# AND il.warehouse_id = \$2##|$T"
 "l2 per tanggal diabaikan|$LF|s#il.movement_date <= \$3#TRUE#g|$T"
 "l3 bawaan semua barang (bukan yang pernah bergerak)|$LF|s#HAVING \$6::boolean OR#HAVING TRUE OR#|$T"
 "l4 barang nonaktif ikut|$LF|s# AND p.status = 'active'##|$T"
 "l5 ledger tanpa saring tenant|$LF|s# AND il.tenant_id = \$1##|$T"
 "l6 jasa/non-stok ikut|$LF|s#p.item_type = 'goods' AND p.track_inventory = true AND ##|$T"
 "l7 teks stok tanpa satuan|$LF|s#return BD._q(Decimal(str(x))) + (f\" {satuan}\" if satuan else \"\")#return BD._q(Decimal(str(x)))#|$T"
 "l8 stok minus tak ditandai|$LF|s#\"minus\": s < 0#\"minus\": False#|$T"
 "l9 batas baris tak berlaku|$LF|s#if len(rows) > BATAS_BARIS:#if False:#|$T"
 "l10 kolom Fisik hilang di PDF|$TP|s#<th class=\"lh-fisik\">Fisik</th>##|$T"
 "l11 kolom Fisik terisi stok buku|$TP|s#class=\"lh-fisik\">\&nbsp;</td>#class=\"lh-fisik\">{{ b.stok_buku_text }}</td>#|$T"
 "l12 gudang sistem (Transit) terbuka|$WH|s#NOT is_system\", warehouse_id, ctx\[\"tenant_id\"\])#TRUE\", warehouse_id, ctx[\"tenant_id\"])#|$T"
)
