# Konfig gerbang unit BUG-188 (GET BOM tak menulis; biaya tersimpan dipakai apa adanya). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-b188; CABANG=feat/bom-get-tak-menulis; NAMA_JENDELA=bug188
POLA_UNIT='bom|work_center|izin_rute|inventaris_rute|pagar_rute|permission'
HARNESS=scripts/ops/uji-salinan-bug188.sh
PENANDA_BERKAS=routers/bom.py; PENANDA='BUG-188: GET TIDAK menulis'
A=backend/api_gateway/app; T=tests/unit/test_bug188_get_bom_tak_menulis.py; BM=$A/routers/bom.py
SAB=(
 "g1 get_bom kembali menulis|$BM|s#            \# BUG-188: GET TIDAK menulis. Dulu di sini#            await conn.fetchval(\"SELECT calculate_bom_cost(\$1)\", bom_id)  \# BUG-188: GET TIDAK menulis. Dulu di sini#|$T"
 "g2 rincian biaya kembali menulis|$BM|s#            \# BUG-188: GET TIDAK menulis (lihat get_bom) -- rincian biaya dari angka TERSIMPAN.#            await conn.fetchval(\"SELECT calculate_bom_cost(\$1)\", bom_id)#|$T"
 "g3 recalculate tak menyegarkan|$BM|s#            new_cost = await conn.fetchval(\"SELECT calculate_bom_cost(\$1)\", bom_id)#            new_cost = 0#|$T"
)
