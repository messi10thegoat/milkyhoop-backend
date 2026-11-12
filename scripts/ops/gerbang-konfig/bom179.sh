# Konfig gerbang unit BUG-179 (tarif Work Center di-snapshot ke operasi BOM; recalculate BOM draf menyegarkan + pratinjau lama->baru; saring tenant baca work_centers). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-bom179; CABANG=feat/bom-wc-tarif; NAMA_JENDELA=bom179
POLA_UNIT='bom|work_center|izin_rute|inventaris_rute|pagar_rute|permission'
HARNESS=scripts/ops/uji-salinan-bom179.sh
PENANDA_BERKAS=routers/bom.py; PENANDA='async def segarkan_tarif_operasi'
A=backend/api_gateway/app; T=tests/unit/test_bom_wc_tarif.py; BM=$A/routers/bom.py
SAB=(
 "n1 tarif WC tanpa saring tenant|$BM|s#FROM work_centers WHERE id = \$1 AND tenant_id = \$2\"#FROM work_centers WHERE id = \$1\"#|$T"
 "n2 buat BOM tak snapshot tarif WC|$BM|0,/op_labor, op_overhead = tarif_efektif(op_labor, op_overhead, await tarif_work_center(conn, ctx\[\"tenant_id\"\], op.work_center_id))/s#op_labor, op_overhead = tarif_efektif(op_labor, op_overhead, await tarif_work_center(conn, ctx\[\"tenant_id\"\], op.work_center_id))#pass#|$T"
 "n3 tarif eksplisit tertimpa WC|$BM|s#(labor if labor and labor > 0 else tarif_wc\[0\]), (overhead if overhead and overhead > 0 else tarif_wc\[1\])#tarif_wc[0], tarif_wc[1]#|$T"
 "n4 recalculate menyegarkan BOM non-draf juga|$BM|s#if status == \"draft\" else \[\]\$#if True else []#|$T"
 "n5 pratinjau recalculate tak di-rollback|$BM|s#                raise _Pratinjau()#                pass#|$T"
)
