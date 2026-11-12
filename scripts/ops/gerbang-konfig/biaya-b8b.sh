# Konfig gerbang unit Biaya B8b (jenis `expense` di hub pembaca lampiran GET /api/lampiran/expense/{id}). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-b8a; CABANG=feat/biaya-b8a; NAMA_JENDELA=biaya-b8b
POLA_UNIT='lampiran|biaya|expense|izin_rute|inventaris_rute|pagar_rute|permission'
HARNESS=scripts/ops/uji-salinan-biaya-b8b.sh
PENANDA_BERKAS=routers/lampiran_induk.py; PENANDA='SPEC_BIAYA = SpecLampiran'
A=backend/api_gateway/app; L=$A/routers/lampiran_dokumen.py; T=tests/unit/test_lampiran_baca_biaya.py
SAB=(
 "m1 jenis expense tak diterima|$L|s#    \"expense\": (LI.SPEC_BIAYA, \"expense_date\"),.*##|$T"
 "m2 saring tenant kueri baris hilang|$L|/^def sql_tunggal/,/nomor_tunggal/{s#AND da.tenant_id = \$2#AND \$2::text IS NOT NULL#;s#AND d.tenant_id = \$2 AND d.deleted_at#AND \$2::text IS NOT NULL AND d.deleted_at#}|$T"
 "m3 izin rute hub biaya = kas_bank|$A/middleware/permission_middleware.py|s#lampiran/expense/\[^/\]+\$\", \[\"GET\"\], \"expense\"#lampiran/expense/[^/]+$\", [\"GET\"], \"kas_bank\"#|$T"
 "m4 url grup biaya salah modul|$A/routers/sales_order_lampiran.py|s#\"expense\": (\"expenses\", None),#\"expense\": (\"bills\", None),#|$T"
 "m5 lampiran terhapus ikut tampil|$L|/^def sql_tunggal/,/nomor_tunggal/{s# AND d.deleted_at IS NULL##}|$T"
)
