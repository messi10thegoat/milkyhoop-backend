# Konfig gerbang unit BUG-189 (BOM: banyak aktif, satu UTAMA; jadikan-utama; promosi saat usang; label/status_text). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-b189; CABANG=feat/bom-banyak-aktif; NAMA_JENDELA=bug189
POLA_UNIT='bom|work_center|izin_rute|inventaris_rute|pagar_rute|permission|produksi|production'
HARNESS=scripts/ops/uji-salinan-bug189.sh
PENANDA_BERKAS=services/bom_status.py; PENANDA='def label'
A=backend/api_gateway/app; T=tests/unit/test_bug189_bom_banyak_aktif.py; BM=$A/routers/bom.py; BSS=$A/services/bom_status.py; PM=$A/middleware/permission_middleware.py; PRD=$A/routers/production.py
SAB=(
 "i1 aktivasi mengusangkan/menurunkan BOM lain|$BM|s#                jadi_utama = not sudah_ada_utama#                jadi_utama = True; await conn.execute(\"UPDATE bill_of_materials SET is_current = false WHERE tenant_id = \$1 AND product_id = \$2 AND id != \$3\", ctx[\"tenant_id\"], bom[\"product_id\"], bom_id)#|$T"
 "i2 jadikan-utama tak menurunkan utama lama|$BM|s#\"UPDATE bill_of_materials SET is_current = false WHERE tenant_id = \$1 AND product_id = \$2 AND id != \$3 AND is_current\",#\"UPDATE bill_of_materials SET is_current = true WHERE tenant_id = \$1 AND product_id = \$2 AND id != \$3 AND is_current\",#|$T"
 "i3 WO mensyaratkan is_current|$PRD|s#WHERE bom.tenant_id = \$1 AND bom.id = \$2 AND bom.status = 'active'#WHERE bom.tenant_id = \$1 AND bom.id = \$2 AND bom.status = 'active' AND bom.is_current#|$T"
 "i4 label tanpa penanda utama|$BSS|s#        bagian.append(\"utama\")#        pass#|$T"
 "i5 usangkan utama tanpa promosi penerus|$BM|s#                if bom\[\"is_current\"\]:#                if False:#|$T"
 "i6 izin jadikan-utama jadi C (pola luas)|$PM|s#jadikan-utama\$\", \[\"POST\"\], \"bom\", \"U\"#jadikan-utama\$\", [\"POST\"], \"bom\", \"C\"#|$T"
 "i7 draf bertanda utama (label)|$BSS|s#    return status == \"active\" and bool(is_current)#    return bool(is_current)#|$T"
)
