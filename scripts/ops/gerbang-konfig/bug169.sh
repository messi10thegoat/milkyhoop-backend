# Konfig gerbang unit BUG-169 (ringkas_per_akun + akun_nol_netto, tampilan saja) + BUG-176 (nomor jurnal biaya = nomor dokumen). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-bug169; CABANG=feat/bug169-176; NAMA_JENDELA=bug169
POLA_UNIT='faktur_terbit|terbit|biaya|expense|sales_invoice|nomor_jurnal|bug169|izin_rute|inventaris_rute|pagar_rute'
HARNESS=scripts/ops/uji-salinan-bug169-176.sh
PENANDA_BERKAS=services/faktur_terbit_rencana.py; PENANDA='def ringkas_per_akun'
A=backend/api_gateway/app; T=tests/unit/test_bug169_176.py; FT=$A/services/faktur_terbit_rencana.py
SAB=(
 "k1 akun nol-neto tak dibuang dari ringkasan|$FT|s#        if n == 0:#        if False:#|$T"
 "k2 neto tanpa pembulatan sen (derau float jadi baris)|$FT|s#n = neto\\[k\\].quantize(Decimal(\"0.01\"))#n = neto[k]#|$T"
 "k3 ringkasan hanya jurnal tagihan (pengakuan/uang muka hilang)|$FT|s#ringkas, nol_netto = ringkas_per_akun(journals + deposit_journals)#ringkas, nol_netto = ringkas_per_akun(journals[:1])#|$T"
 "k4 rincian journals ikut diringkas (Law 1: yang ditulis = yang tampil)|$FT|s#\"journals\": journals, \"deposit_applications\": deposit_apps#\"journals\": [], \"deposit_applications\": deposit_apps#|$T"
 "k5 nomor jurnal biaya kembali berawalan ganda|$A/routers/expenses.py|s#journal_number = await _NJ.nomor_jurnal_dokumen(conn, tenant_id, expense_number, \"JV-EXP\")#journal_number = \"JV-EXP-\" + expense_number#|$T"
)
