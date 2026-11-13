# Konfig gerbang unit BUG-205 (syarat bayar PO = snapshot di PO, V496; tagihan mewarisi; PDF mencetak snapshot). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-b205; CABANG=feat/po-syarat-bayar-snapshot; NAMA_JENDELA=syaratpo205
POLA_UNIT='purchase_order|po_|termin|tagih|jatuh_tempo|pdf|izin_rute|inventaris_rute|pagar_rute|permission'
HARNESS=scripts/ops/uji-salinan-syaratpo205.sh
PENANDA_BERKAS=services/termin_bayar.py; PENANDA='po_terms'
A=backend/api_gateway/app; T=tests/unit/test_po_syarat_bayar_205.py; TBF=$A/services/termin_bayar.py; POR=$A/routers/purchase_orders.py; PCF=$A/services/po_cetak.py; TPO=$A/templates/pdf/purchase_order.html; SCH=$A/schemas/purchase_orders.py; MG=backend/migrations/V496__po_payment_terms_days.sql
SAB=(
 "s1 termin PO diabaikan di tagihan|$TBF|s#    if po_terms is not None and 0 <= int(po_terms) <= HARI_MAKS:#    if False:#|$T"
 "s2 sumber salah dilabeli|$TBF|s#\"due_date_source\": \"po_terms\"#\"due_date_source\": \"vendor_terms\"#|$T"
 "s3 termin 0 bukan Tunai|$TBF|s#\"Tunai\" if n_po == 0 else#\"Tunai\" if n_po == 99 else#|$T"
 "s4 PDF tak mencetak syarat bayar|$TPO|s#{% if po.syarat_bayar %}#{% if False %}#|$T"
 "s5 bawaan tak memakai termin vendor|$POR|s#    return int(n) if n is not None and 0 <= int(n) <= HARI_MAKS else None#    return None#|$T"
 "s6 isian pengguna diabaikan|$POR|s#        return int(termin_body)#        return None#|$T"
 "s7 buat tak menyimpan snapshot|$POR|s#^                    terms,#                    None,#|$T"
 "s8 PATCH boleh mengosongkan|$SCH|s#raise ValueError(\"Syarat bayar tidak bisa dikosongkan; isi jumlah hari (0 = Tunai).\")#return v#|$T"
 "s9 CHECK batas atas dilonggarkan|$MG|s#payment_terms_days <= 365#payment_terms_days <= 3650#|$T"
 "s10 pratinjau tagih tak meneruskan termin PO|$POR|s#, po_terms=po.get(\"payment_terms_days\"))#)#|$T"
 "s11 detail tanpa teks syarat bayar|$POR|s#\"syarat_bayar_text\": termin_syarat(po.get(\"payment_terms_days\")),#\"syarat_bayar_text\": None,#|$T"
 "s12 PDF membaca termin vendor|$PCF|s#po_data\[\"syarat_bayar\"\] = termin_syarat(po.get(\"payment_terms_days\"))#po_data[\"syarat_bayar\"] = termin_syarat(30)#|$T"
)
