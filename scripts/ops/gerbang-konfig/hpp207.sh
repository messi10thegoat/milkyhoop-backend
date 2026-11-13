# Konfig gerbang unit BUG-207 (peringatan konfirmasi jual di bawah HPP + biaya terima janggal; angka hanya bagi REPORT R). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-b207r2; CABANG=feat/peringatan-jual-bawah-hpp-r2; NAMA_JENDELA=hpp207
POLA_UNIT='sales_|quote|purchase_order|po_|peringatan|hpp|izin_rute|inventaris_rute|pagar_rute|permission|satuan'
HARNESS=scripts/ops/uji-salinan-hpp207.sh
PENANDA_BERKAS=services/jual_bawah_hpp.py; PENANDA='AMBANG_SELISIH'
A=backend/api_gateway/app; T=tests/unit/test_peringatan_207.py; JBF=$A/services/jual_bawah_hpp.py
SAB=(
 "j1 ambang terima 30 jadi 50|$JBF|s#        if selisih > AMBANG_SELISIH:#        if selisih > Decimal(\"0.50\"):#|$T"
 "j2 jual: satuan diabaikan (qty baris, bukan dasar)|$JBF|s#harga = SD.biaya_dasar(x.get(\"nilai\"), h\[\"qty_dasar\"\])#harga = SD.biaya_dasar(x.get(\"nilai\"), x.get(\"quantity\"))#|$T"
 "j3 angka bocor ke tak berizin|$JBF|s#        if boleh_hpp:#        if True:#|$T"
 "j4 terima hanya satu arah (murah)|$JBF|s#selisih = abs(biaya - b\[\"hpp\"\]) / b\[\"hpp\"\]#selisih = (b[\"hpp\"] - biaya) / b[\"hpp\"]#|$T"
 "j5 izin salah modul|$JBF|s#JALUR_IZIN_HPP = \"/api/reports/profit-loss\"#JALUR_IZIN_HPP = \"/api/items\"#|$T"
 "j6 harga pokok 0 ikut dihitung (terima)|$JBF|s#        if b is None or b\[\"hpp\"\] <= NOL:#        if b is None:#|$T"
 "j7 SO calculate tak memanggil penentu|$A/routers/sales_orders.py|s#_peringatan = await JB.peringatan_jual(#_peringatan = [] and await JB.peringatan_jual(#|$T"
 "j8 Penawaran calculate tak memanggil penentu|$A/routers/quotes.py|s#_peringatan = await JB.peringatan_jual(#_peringatan = [] and await JB.peringatan_jual(#|$T"
 "j9 Faktur calculate tak memanggil penentu|$A/routers/sales_invoices.py|s#_peringatan = await JB.peringatan_jual(#_peringatan = [] and await JB.peringatan_jual(#|$T"
 "j10 terima tulis menghitung SESUDAH stok masuk (HPP pembanding bergeser)|$A/routers/purchase_orders.py|s#_peringatan_terima = await JB.peringatan_terima(#_peringatan_terima = [] or await JB.peringatan_terima(#|$T"
 "j11 respons SO tanpa kunci peringatan|$A/routers/sales_orders.py|s#\"peringatan\": _peringatan,#\"peringatan_x\": _peringatan,#|$T"
 "j12 skema Faktur menyaring peringatan|$A/schemas/sales_invoices.py|s#    peringatan: List\[Dict\[str, Any\]\] = \[\]#    peringatan_x: List[Dict[str, Any]] = []#|$T"
 "j13 Faktur post/preview tanpa peringatan|$A/routers/sales_invoices.py|s#_peringatan = await JB.peringatan_tersimpan(#_peringatan = [] and await JB.peringatan_tersimpan(#|$T"
 "j14 SO confirm/preview tanpa peringatan|$A/routers/sales_orders.py|s#\"peringatan\": await JB.peringatan_tersimpan(#\"peringatan\": [] and await JB.peringatan_tersimpan(#|$T"
 "j15 Penawaran send/preview tanpa peringatan|$A/routers/quotes.py|s#_peringatan.extend(await JB.peringatan_tersimpan(#_peringatan.extend([] and await JB.peringatan_tersimpan(#|$T"
 "j17 dokumen tersimpan: urutan tabel salah|$JBF|s#\"i.sort_order, i.id\"#\"i.id\"#|$T"
 "p1 REGRESI jumlah_baris NULL diperlakukan 0|$JBF|s#\"jumlah_baris\": r\[\"jumlah_baris\"\]}#\"jumlah_baris\": (r[\"jumlah_baris\"] or 0)}#|$T"
 "p2 harga satuan tersimpan diabaikan|$JBF|s#\"unit_price\": r\[\"unit_price\"\],#\"unit_price\": 0,#|$T"
 "p3 diskon dokumen tersimpan diabaikan|$JBF|s#doc_discount_amount=diskon#doc_discount_amount=Decimal(0)#|$T"
 "p4 diskon baris persen diabaikan|$JBF|s#\"discount_percent\": r\[\"discount_percent\"\],#\"discount_percent\": 0,#|$T"
 "p5 diskon baris nominal (faktur) diabaikan|$JBF|s#\"discount_amount\": r\[\"discount_amount\"\],#\"discount_amount\": None,#|$T"
 "p6 kalkulator bersama tak dipakai (nilai mentah jumlah_baris)|$JBF|s#baris_dari_hasil(compute_document(baris, doc_discount_amount=diskon, presisi=presisi)\[\"items\"\])#[{\"item_id\": b[\"item_id\"], \"quantity\": b[\"quantity\"], \"unit\": b[\"unit\"], \"nilai\": Decimal(str(b[\"jumlah_baris\"] or 0))} for b in baris]#|$T"
)
