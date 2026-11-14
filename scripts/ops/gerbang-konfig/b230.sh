# Konfig gerbang unit BUG-230 (GET /warehouses/{id}/stock bawaan hanya barang ber-stok; ?hanya_berstok=false ikut baris 0). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-b230; CABANG=feat/gudang-stok-tanpa-nol-r2; NAMA_JENDELA=b230
POLA_UNIT='bug230|gudang_stok|warehouse|izin_rute|sql_placeholder'
HARNESS=scripts/ops/uji-salinan-b230.sh
PENANDA_BERKAS=routers/warehouses.py; PENANDA='hanya_berstok'
A=backend/api_gateway/app; T=tests/unit/test_bug230_stok_gudang_berstok.py; WF=$A/routers/warehouses.py
SAB=(
 "n1 bawaan jadi ikut stok 0|$WF|s#hanya_berstok: bool = Query(True,#hanya_berstok: bool = Query(False,#|$T"
 "n2 saringan stok 0 hilang|$WF|s# AND ws.quantity <> 0\" if hanya_berstok#\" if hanya_berstok#|$T"
 "n3 saringan terbalik (hanya yang nol)|$WF|s# AND ws.quantity <> 0\" if hanya_berstok# AND ws.quantity = 0\" if hanya_berstok#|$T"
 "n4 cari menimpa saringan stok (= bukan +=)|$WF|s#where_extra += \" AND (i.item_code#where_extra = \" AND (i.item_code#|$T"
)
