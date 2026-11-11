# Konfig gerbang unit laporan-fix-2 (basis kas per akun + footing bulat per baris + label arus kas/dasbor + periode_rentang_text). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-lapfix2-r2; CABANG=feat/laporan-fix-2-r2; NAMA_JENDELA=lapfix-2
POLA_UNIT='basis_kas|footing|laporan_psak|laporan_fix2|dashboard|laba_presisi|report|ledger|cash_flow|arus_kas|teks_galat|rupiah|izin_rute|inventaris_rute|pagar_rute'
HARNESS=scripts/ops/uji-salinan-laporan-psak.sh
PENANDA_BERKAS=services/report_engine/basis_kas.py; PENANDA='def ringkas'
A=backend/api_gateway/app; T=tests/unit/test_laporan_fix2_footing.py; TB=tests/unit/test_basis_kas.py; TD=tests/unit/test_dashboard_v2.py
SAB=(
 "s1 total dari jumlah mentah (bukan Σ tampil)|$A/services/report_engine/footing.py|s#if b\\[\"jenis\"\\] != \"akun\" and k in rumus:#if False and k in rumus:#|$T"
 "s2 HPP faktur tak ikut basis kas|$A/services/report_engine/basis_kas.py|s#(SQL_HPP_FAKTUR, 1), ##|$TB"
 "s3 tanda pendapatan basis kas terbalik|$A/services/report_engine/basis_kas.py|s#(SQL_PENDAPATAN, -1)#(SQL_PENDAPATAN, 1)#|$TB"
 "s4 label LIABILITY kembali umum|$A/services/report_engine/cash_flow.py|s#if account_type in (\"ASSET\", \"LIABILITY\") and account_name:#if account_type == \"ASSET\" and account_name:#|$T"
 "s5 koreksi dilebur ke Lainnya|$A/services/dashboard_v2.py|s#sisa_debit = \\[r for r in sisa if d(r\\[\"balance\"\\]) > NOL\\]#sisa_debit = list(sisa)#|$TD"
 "s6 rentang beda tahun tak dibedakan|$A/services/teks_galat.py|s#    if a.year == b.year:#    if False and a.year == b.year:#|$T"
)
