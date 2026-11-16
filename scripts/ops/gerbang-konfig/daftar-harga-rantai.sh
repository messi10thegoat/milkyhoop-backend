# Konfig gerbang PAKET RANTAI Daftar Harga (16 Nov 2026, putusan MASTER): cabang puncak feat/daftarharga-riwayat-252 memuat LINEAR 245-r5 -> 251 -> 040 -> 253 -> urut-start -> 252 -> 275 (Tinjau rencana; commit di cabang yang sama).
# Satu paket pada sha teratas: unit gabungan, SEMUA harness (impor 251, min 040, diskon 253, berjangka 245, riwayat 252, rencana 275), SEMUA sabotase dari 6 konfig (tiap sabotase wajib MERAH
# di sha teratas), satu kontrol merah, satu gerbang penuh. Vonis berlaku untuk seluruh rantai. Konfig ini TIDAK mendefinisi sabotase baru: ia memuat SAB dari konfig asal.
W=/root/mh-dh-riwayat-252; CABANG=feat/daftarharga-riwayat-252; NAMA_JENDELA=daftar-harga-rantai
POLA_UNIT='impor_harga|impor_barang|price_lists|harga_daftar|daftar_harga|dh_impor|dh_berjangka|dh_diskon|dh_urut|dh_riwayat|dh_rencana|harga_riwayat|dh_harga_minimum|harga_minimum|harga_jual|harga_massal|bulk_price|bulk_f1|izin_rute|bug101|satuan_dasar|peringatan_207|konfirmasi_pratinjau'
HARNESS=scripts/ops/uji-salinan-daftar-harga-rantai.sh
PENANDA_BERKAS=services/harga_riwayat.py; PENANDA='async def catat('
KD="$W/scripts/ops/gerbang-konfig"
SAB=()
for k in daftar-harga-impor daftar-harga-min daftar-harga-diskon daftar-harga-urut daftar-harga-riwayat daftar-harga-rencana; do
  while IFS= read -r l; do SAB+=("$l"); done < <(bash -c ". \"$KD/$k.sh\"; printf '%s\n' \"\${SAB[@]}\"")
done
