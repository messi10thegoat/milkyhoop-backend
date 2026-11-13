# Konfig gerbang unit BUG-213 (Selesai tak menguras sisa WIP; tutup bulan yang menguras) + BUG-214 (lapor hasil saat Dirilis ditolak WO_BELUM_DIMULAI). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-b213; CABANG=feat/wo-sisa-wip-tutup-bulan-lapor-dirilis; NAMA_JENDELA=wo213
POLA_UNIT='production|produksi|wo_|wip|izin_rute|inventaris_rute|pagar_rute|permission|t10b3b'
HARNESS=scripts/ops/uji-salinan-wo213.sh
PENANDA_BERKAS=routers/production.py; PENANDA='WO_BELUM_DIMULAI'
A=backend/api_gateway/app; T=tests/unit/test_wo_213_214.py; PRF=$A/routers/production.py
SAB=(
 "o1 Selesai menulis jurnal lagi|$PRF|s#                \# Selesai kini hanya mengubah status + variance_amount (angka), tanpa jurnal.#                await conn.execute(\"INSERT INTO journal_entries (id) VALUES (\$1)\", order_id)#|$T"
 "o2 penanda putusan 213 hilang|$PRF|s#\# BUG-213 (putusan pemilik 13 Nov 22:40, opsi B): Selesai TIDAK lagi menguras#\# x (putusan pemilik 13 Nov 22:40, opsi B): Selesai TIDAK lagi menguras#|$T"
 "o3 lapor hasil saat Dirilis lolos|$PRF|s#if order\[\"status\"\] == \"released\":  \# BUG-214#if False:  \# BUG-214#|$T"
 "o4 kode galat status lain salah|$PRF|s#\"WO_TIDAK_BISA_LAPOR\", f\"Hasil#\"WO_LAIN\", f\"Hasil#|$T"
 "o5 kode WO_BELUM_DIMULAI salah|$PRF|s#\"WO_BELUM_DIMULAI\", \"Mulai proses dulu#\"WO_BELUM_MULAI\", \"Mulai proses dulu#|$T"
 "o6 teks tak menyuruh mulai proses|$PRF|s#\"Mulai proses dulu: hasil produksi#\"Hasil produksi#|$T"
 "o7 tutup bulan tak menguras (pratinjau/tulis)|$PRF|s#wip_sisa = await PW.sisa_wip_selesai(conn, tenant_id, _wip_id, p_end)#wip_sisa = []#|$T"
 "o8 penguras tutup bulan mengabaikan JV-VAR lama (kuras ganda)|$A/services/produksi_wip_sisa.py|s#AND je.journal_date <= \$3 AND is_effective_journal(je.id)#AND je.journal_date <= \$3 AND je.source_type <> 'PRODUCTION_VARIANCE' AND is_effective_journal(je.id)#|$T"
 "o9 penguras tutup bulan tak menghitung baris kuras (tak idempoten)|$A/services/produksi_wip_sisa.py|s#saldo\[m.group(1)\] += Decimal(str(r\[\"debit\"\] or 0)) - Decimal(str(r\[\"credit\"\] or 0))#saldo[m.group(1)] += 0#|$T"
)
