# Konfig gerbang unit (dibaca scripts/ops/gerbang-unit.sh). Sabotase = bukti unit ini; tiap baris WAJIB membuat tes merah.
W=/root/mh-rek; CABANG=feat/kasbank-kelola-rekening-cw-r2; NAMA_JENDELA=rek-1
POLA_UNIT='bank_accounts|kasbank|idem_buat|auto_create_coa|inventaris_rute|izin_rute|pagar_uang_muka|tanggal_bisnis'
HARNESS=scripts/ops/uji-salinan-kasbank-rekening.sh
PENANDA_BERKAS=services/kasbank_rekening.py; PENANDA='async def rencana_hapus_rekening(conn, tid: str, ba_id, pratinjau: bool) -> dict:'
A=backend/api_gateway/app; K=$A/services/kasbank_rekening.py; T=tests/unit/test_kasbank_kelola_rekening.py
SAB=(
 "r1 pratinjau bukan READ ONLY|$K|s#conn.transaction(readonly=True)#conn.transaction()#|$T"
 "r2 saldo awal terbukukan diabaikan|$A/services/kasbank_daftar.py|s#if jumlah_mutasi == 0 and terbukukan:#if False:#|$T"
 "r3 kode CoA bergeser|$K|s#int(last\[-2:\]) + 1#int(last[-2:]) + 2#|$T"
 "r4 peringatan utama hilang|$K|s#        if ba\[\"is_default\"\]:#        if False:#|$T"
 "r5 kartu kredit tak dibalik|$K|s#if kartu else\$#if False else#|$T"
 "r6 idem buat hilang|$A/routers/bank_accounts.py|s#\"BANK_CREATE\", body,#\"BANK_CREATE_X\", body,#|$T"
 "r7 pagar uang muka hilang|$K|s#await bl.coba(\"COA_UANG_MUKA\", tolak_rekening_bank(conn, tid, coa\[\"id\"\]))#pass#|tests/unit/test_pagar_uang_muka.py"
 "r8 tanggal klien tertimpa|$K|s#body.opening_date or await tanggal_dokumen#await tanggal_dokumen#|tests/unit/test_t10b_tanggal_bisnis_arap.py"
 "r9 str(e) di 400 buat|$A/routers/bank_accounts.py|s#detail=\"Gagal membuat akun kas/bank.\"#detail=f\"Gagal: {str(e)}\"#|$T"
)
