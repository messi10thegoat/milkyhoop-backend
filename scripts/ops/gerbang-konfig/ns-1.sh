# Konfig gerbang unit (dibaca scripts/ops/gerbang-unit.sh). Sabotase = bukti unit ini; tiap baris WAJIB membuat tes merah.
W=/root/mh-ns; CABANG=feat/laporan-neraca-saldo-teks; NAMA_JENDELA=ns-1
POLA_UNIT='reports|ledger|report_engine|neraca_saldo|trial|tanggal_bisnis|rute'
HARNESS=scripts/ops/uji-salinan-neraca-saldo.sh
PENANDA_BERKAS=services/neraca_saldo.py; PENANDA='def kolom_saldo(debit, kredit) -> tuple:'
A=backend/api_gateway/app; T=tests/unit/test_neraca_saldo_teks.py
SAB=(
 "s1 status POSTED dibuang|$A/services/neraca_saldo.py|s#AND je.status = .POSTED. ##|$T"
 "s2 alias as_of_date diabaikan|$A/routers/reports.py|s#as_of or as_of_date or await#as_of or await#|$T"
 "s3 label Kewajiban|$A/services/report_engine/baris.py|s#\"LIABILITY\": \"Liabilitas\", \"PAYABLE\"#\"LIABILITY\": \"Kewajiban\", \"PAYABLE\"#|$T"
 "s4 selalu Seimbang|$A/services/neraca_saldo.py|s#\"Seimbang\" if selisih.quantize(B.SEN) == 0 else#\"Seimbang\" if True else#|$T"
 "s5 kolom saldo tertukar|$A/services/neraca_saldo.py|s#return (neto, NOL) if neto > 0 else (NOL, -neto)#return (NOL, -neto) if neto > 0 else (neto, NOL)#|$T"
 "s6 saldo normal konstan|$A/services/report_engine/baris.py|s#return SALDO_NORMAL.get(str(normal or \"\").upper(), \"\")#return \"Debit\"#|$T"
 "s7 akun nonaktif dibuang|$A/services/neraca_saldo.py|s#    WHERE coa.tenant_id = \\\$1\$#    WHERE coa.tenant_id = \$1 AND coa.is_active#|$T"
 "s8 str(e) /full kembali|$A/routers/reports.py|s#detail=\"Failed to generate trial balance report\"\$#detail=f\"Failed to generate trial balance report: {str(e)}\"#|$T"
)
