# Konfig gerbang PAKET 2 Daftar Harga (17 Nov 2026): cabang puncak feat/daftarharga-paket2 = rantai LIVE (547e5649a) + BUG-278 (D3 sumber baris berlaku) + BUG-256 (salin daftar), satu paket.
# SEMUA sabotase dari 8 konfig (rantai 6 + salin + d3berlaku) wajib MERAH di sha teratas; HARNESS = 7 harness klon berurutan. Konfig ini TIDAK mendefinisi sabotase baru.
W=/root/mh-dh-paket2; CABANG=feat/daftarharga-paket2; NAMA_JENDELA=daftar-harga-paket2
POLA_UNIT='impor_harga|impor_barang|price_lists|harga_daftar|daftar_harga|dh_impor|dh_berjangka|dh_diskon|dh_urut|dh_riwayat|dh_rencana|harga_riwayat|dh_harga_minimum|harga_minimum|harga_jual|harga_massal|bulk_price|bulk_f1|izin_rute|bug101|satuan_dasar|peringatan_207|konfirmasi_pratinjau|harga_salin|dh_salin|dh_d3_berlaku'
HARNESS=scripts/ops/uji-salinan-daftar-harga-paket2.sh
PENANDA_BERKAS=services/harga_salin.py; PENANDA='async def tulis('
KD="$W/scripts/ops/gerbang-konfig"
SAB=()
for k in daftar-harga-impor daftar-harga-min daftar-harga-diskon daftar-harga-urut daftar-harga-riwayat daftar-harga-rencana daftar-harga-salin daftar-harga-d3berlaku; do
  while IFS= read -r l; do SAB+=("$l"); done < <(bash -c ". \"$KD/$k.sh\"; printf '%s\n' \"\${SAB[@]}\"")
done
