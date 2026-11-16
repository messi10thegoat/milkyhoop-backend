# Konfig gerbang unit BUG-268b + BUG-268c (void POS + pembalik WO memakai penentu yang sama; snapshot inventory_ledger.average_cost baris PEMBALIK = WAC SESUDAH efek; cadangan pola V480; tanpa migrasi, tanpa UPDATE ledger). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-b268b; CABANG=fix/bug268b-wac-void; NAMA_JENDELA=b268b
POLA_UNIT='bug268b|bug268c|sales_receipt|produksi|production|persediaan|inventory|kosakata_keluar_jual|t10b3b|void_sj_faktur|grn|izin_rute|sql_placeholder'
HARNESS=scripts/ops/uji-salinan-b268b.sh
PENANDA_BERKAS=services/inventory_helpers.py; PENANDA='snapshot_wac_sesudah'
A=backend/api_gateway/app; T=backend/api_gateway/tests/unit/test_bug268b_wac_pembalik.py; T2=backend/api_gateway/tests/unit/test_bug268c_wac_pembalik_lain.py
IF=$A/services/inventory_helpers.py; SRF=$A/routers/sales_receipts.py; PRF=$A/routers/production.py
SAB=(
 "s1 snapshot kembali dihitung SEBELUM efek (perilaku lama)|$IF|s#return hitung_wac_sesudah(nilai, qty, Decimal(str(masuk or 0)), Decimal(str(keluar or 0)), uc, Decimal(str(lama)) if lama else uc)#return Decimal(str(lama)) if lama else uc#|$T"
 "s2 rumus gross (keluar tak mengurangi nilai)|$IF|s#nilai = nilai_sebelum + masuk \\* unit_cost - keluar \\* unit_cost#nilai = nilai_sebelum + masuk * unit_cost#|$T"
 "s3 qty sesudah tak mengurangi keluar|$IF|s#qty = qty_sebelum + masuk - keluar#qty = qty_sebelum + masuk#|$T"
 "s4 pembulatan ke bawah (bukan HALF_UP)|$IF|s#(nilai / qty).quantize(Decimal(\"0.01\"), rounding=ROUND_HALF_UP)#(nilai / qty).quantize(Decimal(\"0.01\"), rounding=ROUND_DOWN)#|$T"
 "s5 cadangan jadi nol|$IF|s#^    return cadangan#    return Decimal(\"0\")#|$T"
 "s6 qty nol dibagi (syarat >= 0)|$IF|s#    if qty > 0 and nilai >= 0:#    if qty >= 0 and nilai >= 0:#|$T"
 "s7 nilai negatif diterima|$IF|s#    if qty > 0 and nilai >= 0:#    if qty > 0:#|$T"
 "s8 pembalik meng-UPDATE baris lama (Rule 8)|$IF|s#total_cost = quantity_reversed \\* unit_cost#total_cost = quantity_reversed * unit_cost  \\# UPDATE inventory_ledger#|$T"
 "s9 kueri saldo tanpa nilai bersih|$IF|s#COALESCE(SUM(quantity_in \\* unit_cost) - SUM(quantity_out \\* unit_cost), 0) AS nilai#0 AS nilai#|$T"
 "s10 nilai bersih diabaikan (selalu 0) di saldo_dan_nilai|$IF|s#Decimal(str(r\[\"nilai\"\])) if r and r\[\"nilai\"\] is not None else Decimal(\"0\"))#Decimal(\"0\"))#|$T"
 "p1 void POS kembali average_cost = unit_cost|$SRF|s#\$9, \$10, \$15,#\$9, \$10, \$9,#|$T2"
 "p2 void POS tak memakai penentu bersama|$SRF|s#_IH.snapshot_wac_sesudah(conn, ctx\[\"tenant_id\"\], item\[\"item_id\"\]#_IH.snapshot_x(conn, ctx[\"tenant_id\"], item[\"item_id\"]#|$T2"
 "p3 pembalik WO kembali average_cost = unit_cost|$PRF|s#\$12, \$13, \$19,#\$12, \$13, \$12,#|$T2"
 "p4 pembalik WO tak memakai penentu bersama|$PRF|s#_IH.snapshot_wac_sesudah(conn, tenant_id, r\[\"product_id\"\]#_IH.snapshot_x(conn, tenant_id, r[\"product_id\"]#|$T2"
 "p5 cadangan penentu bersama jadi nol|$IF|s#if lama else uc)#if lama else Decimal(\"0\"))#|$T2"
 "p6 saldo dari pemanggil diabaikan|$IF|s#qty, nilai = saldo if saldo is not None else await saldo_dan_nilai(conn, tenant_id, product_id)#qty, nilai = await saldo_dan_nilai(conn, tenant_id, product_id)#|$T2"
)
