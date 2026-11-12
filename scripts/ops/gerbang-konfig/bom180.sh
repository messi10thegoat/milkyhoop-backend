# Konfig gerbang unit BUG-180 (migrasi V495: operasi BOM per pcs ber-Work Center menanggung overhead menit/60 x tarif WC; per jam tetap; per pcs tanpa WC = 0). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-bom180; CABANG=feat/bom-per-pcs-wc; NAMA_JENDELA=bom180
POLA_UNIT='bom|work_center|izin_rute|inventaris_rute|pagar_rute|permission|migrasi'
HARNESS=scripts/ops/uji-salinan-bom180.sh
PENANDA_BERKAS=routers/bom.py; PENANDA='async def segarkan_tarif_operasi'
A=backend/api_gateway/app; T=tests/unit/test_bom_overhead_per_pcs.py; BM=$A/routers/bom.py; MG=backend/migrations/V495__bom_overhead_per_pcs_wc.sql
SAB=(
 "r1 per pcs mengabaikan menit|$MG|s#AND work_center_id IS NOT NULL THEN (COALESCE(setup_time_minutes,0) + COALESCE(run_time_minutes,0)) \* overhead_rate_per_hour / 60#AND work_center_id IS NOT NULL THEN overhead_rate_per_hour / 60#|$T"
 "r2 overhead per jam dobel (cabang time_based diulang)|$MG|s#WHEN labor_mode = 'piece_rate' AND work_center_id IS NOT NULL THEN#WHEN labor_mode = 'time_based' AND work_center_id IS NOT NULL THEN#|$T"
 "r3 per pcs tanpa WC ikut dihitung|$MG|s# AND work_center_id IS NOT NULL THEN# THEN#|$T"
 "r4 upah per pcs ditambah tarif labor jam|$MG|s#WHEN 'piece_rate' THEN COALESCE(cost_per_piece, 0)#WHEN 'piece_rate' THEN COALESCE(cost_per_piece, 0) + (COALESCE(setup_time_minutes,0) + COALESCE(run_time_minutes,0)) * labor_rate_per_hour / 60#|$T"
 "r5 tarif WC tanpa saring tenant|$BM|s#FROM work_centers WHERE id = \$1 AND tenant_id = \$2\"#FROM work_centers WHERE id = \$1\"#|$T"
)
