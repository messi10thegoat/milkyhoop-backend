# Konfig gerbang unit penyesuaian-3 (quick adjust -> penulis dokumen SA). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-pen3; CABANG=feat/penyesuaian-3; NAMA_JENDELA=pen-3
POLA_UNIT='penyesuaian|persediaan|stock_adj|stok_sesuai|items|kosakata|t10b2|akun_lawan|izin_rute|inventaris_rute|pagar_rute'
HARNESS=scripts/ops/uji-salinan-persediaan.sh
PENANDA_BERKAS=routers/items.py; PENANDA='SAR._buat_draf('
A=backend/api_gateway/app; T=tests/unit/test_penyesuaian3.py
SAB=(
 "r1 set tak dihitung per gudang (selisih = jumlah)|$A/routers/items.py|s#ubah = (jumlah - sebelum) if tipe == \"set\"#ubah = (jumlah) if tipe == \"set\"#|$T"
 "r2 pagar minus dibuang|$A/routers/items.py|s#if sebelum + ubah < 0:#if False and sebelum + ubah < 0:#|$T"
 "r3 alias adjustment_account_id hilang|$A/routers/items.py|s#body.get(\"counter_account_id\") or body.get(\"adjustment_account_id\")#body.get(\"counter_account_id\")#|$T"
 "r4 quick membuat jurnal kosong bila nilai 0|$A/routers/stock_adjustments.py|s#    if r\\[\"baris\"\\] or not tanpa_jurnal_bila_nol:#    if True:#|$T"
 "r5 barang non-inventory lolos|$A/routers/items.py|s#if not (tipe_brg and tipe_brg\\[\"item_type\"\\] == \"goods\"#if False and not (tipe_brg and tipe_brg[\"item_type\"] == \"goods\"#|$T"
 "r6 kunci per barang+gudang hilang|$A/routers/items.py|s#f\"STOCK:{item_id}:{g\\['id'\\]}\"#f\"STOK:{item_id}:{g['id']}\"#|$T"
)
