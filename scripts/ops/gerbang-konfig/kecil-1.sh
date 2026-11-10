# Konfig gerbang unit (dibaca scripts/ops/gerbang-unit.sh). Sabotase = bukti unit ini; tiap baris WAJIB membuat tes merah.
W=/root/mh-kecil-r3; CABANG=fix/laporan-urutan-umur-sen-full-r3; NAMA_JENDELA=kecil-1
POLA_UNIT='reports|ledger|report_engine|neraca_saldo|trial|aging|umur|tanggal_bisnis|rute'
HARNESS=scripts/ops/uji-salinan-neraca-saldo.sh
PENANDA_BERKAS=routers/reports.py; PENANDA='ORDER BY a.due_date ASC, a.bill_number, a.bill_id'
A=backend/api_gateway/app; T=tests/unit/test_laporan_tiket_kecil.py
SAB=(
 "k1 urutan umur tanpa tie-breaker|$A/routers/reports.py|s#ORDER BY a.due_date ASC, a.invoice_number, a.invoice_id#ORDER BY a.due_date ASC#|$T"
 "k2 int() /full kembali|$A/routers/reports.py|s#closing_d = Decimal(str(row\[\"closing_debit\"\] or 0))#closing_d = int(row[\"closing_debit\"] or 0)#|$T"
)
