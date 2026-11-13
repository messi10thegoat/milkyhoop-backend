# Konfig gerbang unit BUG-199 (izin Penyesuaian Stok per aksi P/V/U/D di STOCK_ADJUST; aksi_tersedia memuat izin pengguna). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-b199; CABANG=feat/izin-penyesuaian-pvud; NAMA_JENDELA=izin199
POLA_UNIT='stock_adj|penyesuaian|izin_rute|inventaris_rute|pagar_rute|permission|aksi_tersedia'
HARNESS=scripts/ops/uji-salinan-izin199.sh
PENANDA_BERKAS=services/stok_sesuai.py; PENANDA='def izin_aksi'
A=backend/api_gateway/app; T=tests/unit/test_izin_penyesuaian_199.py; PMF=$A/middleware/permission_middleware.py; SSF=$A/services/stok_sesuai.py; SAR=$A/routers/stock_adjustments.py
SAB=(
 "i1 posting kembali huruf C|$PMF|s#\"stock_adjust\", \"P\"),#\"stock_adjust\", \"C\"),#|$T"
 "i2 void kembali huruf C|$PMF|s#\"stock_adjust\", \"V\"),#\"stock_adjust\", \"C\"),#|$T"
 "i3 ubah kembali modul PRODUCT|$PMF|s#\[\"PATCH\"\], \"stock_adjust\", \"U\"#[\"PATCH\"], \"item\", \"U\"#|$T"
 "i4 hapus kembali modul PRODUCT|$PMF|s#\[\"DELETE\"\], \"stock_adjust\", \"D\"#[\"DELETE\"], \"item\", \"D\"#|$T"
 "i5 aksi_tersedia mengabaikan izin|$SSF|s#if kunci in hasil and not boleh:#if False and kunci in hasil and not boleh:#|$T"
 "i6 detail tak memberi izin ke aksi_tersedia|$SAR|s#await SS.izin_aksi(request.state.user, sa\[\"id\"\])#None#|$T"
 "i7 kalimat izin Inggris|$SSF|s#Anda tidak punya izin untuk memposting penyesuaian stok.#You cannot post.#|$T"
 "i8 izin posting dinilai di jalur void|$SSF|s#\"posting\": (\"POST\", \"/api/stock-adjustments/{id}/post\")#\"posting\": (\"POST\", \"/api/stock-adjustments/{id}/void\")#|$T"
)
