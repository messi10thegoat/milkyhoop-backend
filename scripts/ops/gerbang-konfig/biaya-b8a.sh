# Konfig gerbang unit Biaya B8a (bukti Biaya = lampiran nyata document_attachments, bukan tabel mati expense_attachments). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-b8a; CABANG=feat/biaya-b8a; NAMA_JENDELA=biaya-b8a
POLA_UNIT='biaya|expense|izin_rute|inventaris_rute|pagar_rute|permission'
HARNESS=scripts/ops/uji-salinan-biaya-b8a.sh
PENANDA_BERKAS=services/biaya_daftar.py; PENANDA='def sql_jumlah_bukti'
A=backend/api_gateway/app; T=tests/unit/test_biaya_b8a.py; BDF=$A/services/biaya_daftar.py; EXR=$A/routers/expenses.py; BRW=$A/services/biaya_riwayat.py
SAB=(
 "k1 pembaca lama pill (expense_attachments)|$BDF|s#\"EXISTS (SELECT 1 FROM \" + _BUKTI_DARI.format(a=a) + \")\"#f\"EXISTS (SELECT 1 FROM expense_attachments ea WHERE ea.expense_id = {a}.id AND ea.tenant_id = {a}.tenant_id)\"#|$T"
 "k2 jumlah bukti tanpa saring dihapus|$BDF|s# AND d.deleted_at IS NULL\"#\"#|$T"
 "k3 detail kembali ke tabel lama|$EXR|s#attachments = await conn.fetch(_EXP_ATT_SQL_DAFTAR, str(expense_id), ctx\\[\"tenant_id\"\\])\$#attachments = await conn.fetch(\"SELECT id FROM expense_attachments WHERE expense_id = \$1\", str(expense_id))#|$T"
 "k4 riwayat bukti tabel lama|$BRW|s#FROM document_attachments da JOIN documents d#FROM expense_attachments da JOIN documents d#|$T"
 "k5 kueri daftar lampiran tanpa saring tenant|$EXR|s#      AND da.tenant_id = \$2 AND d.tenant_id = \$2#      AND d.tenant_id = \$2#|$T"
)
