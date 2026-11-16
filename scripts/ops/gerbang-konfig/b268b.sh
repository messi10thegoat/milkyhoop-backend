# Konfig gerbang unit BUG-268b (snapshot inventory_ledger.average_cost baris PEMBALIK = WAC SESUDAH efek; cadangan pola V480; tanpa migrasi, tanpa UPDATE ledger). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-b268b; CABANG=fix/bug268b-wac-void; NAMA_JENDELA=b268b
POLA_UNIT='bug268b|persediaan|inventory|kosakata_keluar_jual|t10b3b|void_sj_faktur|grn|izin_rute|sql_placeholder'
HARNESS=scripts/ops/uji-salinan-b268b.sh
PENANDA_BERKAS=services/inventory_helpers.py; PENANDA='hitung_wac_sesudah'
A=backend/api_gateway/app; T=backend/api_gateway/tests/unit/test_bug268b_wac_pembalik.py; IF=$A/services/inventory_helpers.py
SAB=(
 "s1 snapshot kembali dihitung SEBELUM efek (perilaku lama)|$IF|s#avg_cost = hitung_wac_sesudah(nilai_sebelum, current_balance, rev_qty_in, rev_qty_out, unit_cost, cadangan)#avg_cost = cadangan#|$T"
 "s2 rumus gross (keluar tak mengurangi nilai)|$IF|s#nilai = nilai_sebelum + masuk \\* unit_cost - keluar \\* unit_cost#nilai = nilai_sebelum + masuk * unit_cost#|$T"
 "s3 qty sesudah tak mengurangi keluar|$IF|s#qty = qty_sebelum + masuk - keluar#qty = qty_sebelum + masuk#|$T"
 "s4 pembulatan ke bawah (bukan HALF_UP)|$IF|s#(nilai / qty).quantize(Decimal(\"0.01\"), rounding=ROUND_HALF_UP)#(nilai / qty).quantize(Decimal(\"0.01\"), rounding=ROUND_DOWN)#|$T"
 "s5 cadangan jadi nol|$IF|s#^    return cadangan#    return Decimal(\"0\")#|$T"
 "s6 qty nol dibagi (syarat >= 0)|$IF|s#    if qty > 0 and nilai >= 0:#    if qty >= 0 and nilai >= 0:#|$T"
 "s7 nilai negatif diterima|$IF|s#    if qty > 0 and nilai >= 0:#    if qty > 0:#|$T"
 "s8 pembalik meng-UPDATE baris lama (Rule 8)|$IF|s#total_cost = quantity_reversed \\* unit_cost#total_cost = quantity_reversed * unit_cost  \\# UPDATE inventory_ledger#|$T"
 "s9 kueri saldo tanpa nilai bersih|$IF|s#COALESCE(SUM(quantity_in \\* unit_cost) - SUM(quantity_out \\* unit_cost), 0) AS nilai#0 AS nilai#|$T"
 "s10 nilai_sebelum diabaikan (0)|$IF|s#nilai_sebelum = Decimal(str(balance_row\\[\"nilai\"\\])) if balance_row and balance_row\\[\"nilai\"\\] is not None else Decimal(\"0\")#nilai_sebelum = Decimal(\"0\")#|$T"
)
