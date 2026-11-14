# Konfig gerbang terfokus Teks Barang (BUG-235/236, 14 Nov 2026): history jenis_text, activity nama tampil + "Barang ...", position_text_ket. Tanpa migrasi, aditif.
# Pemisah sabotase = "|", ekspresi sed memakai "@".
W=/root/mh-barang-teks-r2; CABANG=feat/barang-teks-235-r2; NAMA_JENDELA=barang-teks
POLA_UNIT='barang_teks|barang_daftar|barang_standar|routers.items|get_item_activity|get_item_history|position_text|jenis_text|kosakata_ledger'
HARNESS=scripts/ops/uji-salinan-barang-teks.sh
PENANDA_BERKAS=services/barang_teks.py; PENANDA='def jenis_text('
A=backend/api_gateway/app
T=tests/unit/test_barang_teks.py
SAB=(
 "s1 jenis tak dikenal tampil kode mentah|$A/services/barang_teks.py|s@        return GENERIK@        return mv or GENERIK@|$T"
 "s2 kamus per sumber dicabut|$A/services/barang_teks.py|s@t = PER_SUMBER.get((mv, src)) or PER_JENIS.get(mv)@t = PER_JENIS.get(mv)@|$T"
 "s3 Item tak jadi Barang|$A/services/barang_teks.py|s@return \"Barang \" + description\[len(\"Item \"):\]@return description@|$T"
 "s4 email menang atas nama tampil|$A/services/barang_teks.py|s@for x in (profil, nama_pengguna, cadangan):@for x in (cadangan, profil, nama_pengguna):@|$T"
 "s5 keterangan posisi hilang|$A/services/barang_daftar.py|s@return _posisi(b, stok, bisa_dijual, satuan)\[1\]@return None@|$T"
 "s6 history tanpa jenis_text|$A/routers/items.py|s@\"jenis_text\": BT.jenis_text(@\"jenis_teks\": BT.jenis_text(@|$T"
 "s7 activity tanpa nama tampil|$A/routers/items.py|s@actor_name=BT.nama_tampil(@actor_name=BT.nama_x(@|$T"
 "s8 activity tanpa join pengguna|$A/routers/items.py|s@LEFT JOIN \"User\" u ON@LEFT JOIN \"Usr\" u ON@|$T"
 "s9 ket hilang di detail|$A/routers/items.py|s@\"position_text_ket\": BD.position_text_ket(@\"position_text_kt\": BD.position_text_ket(@|$T"
 "s10 position_text beda sumber dari ket|$A/services/barang_daftar.py|s@    return utama + (f\" · {ket}\" if ket else \"\")@    return utama@|$T"
)
