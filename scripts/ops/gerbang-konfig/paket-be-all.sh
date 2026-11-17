# Konfig gerbang GABUNGAN paket-be-all (17 Nov 2026, putusan MASTER): paket-be-1-r2 + paket-be-2-r2 (kategori K-1..K-5, K-2b, K-3b; V498) + paket-be-3-r2 (255+276+277), dasar master 547e5649a.
# Konflik terselesaikan: items.py (blok impor, simpan keduanya) dan customers.py (price-history BUG-257 + bulk price-list BUG-255, simpan keduanya). SAB = gabungan SEMUA konfig per-unit (subshell tiap konfig).
# URUTAN RILIS: V502 -> V498 -> kode. HARNESS = pembungkus semua harness per-unit.
W=/root/mh-pall; CABANG=paket-be-all-r3; NAMA_JENDELA=pall
POLA_UNIT='bug249|customers_nama|cheque|expense|biaya|bug260|merek|barang_daftar|barang_standar_so|barang_p3|barang_teks|impor_barang|bug101_impor|bug259|lembar_hitung|bug257|histori_harga|bug272|nilai_persediaan|teks_persediaan|k8w|pemindahan|stock_transfer|bug246|bug268|sales_receipt|production|kosakata_keluar_jual|t10b3b|void_sj_faktur|grn|izin_rute|sql_placeholder|k3b_kategori|k2b_kategori|k1_kategori|k2_kategori|k3_kategori|k4_kategori|k5_impor|kategori_daftar|kategori_aksi|kategori|bug255|dh_pelanggan|customer|pelanggan|bug276|bug021|bug092|bug277|ekspor_barang'
HARNESS=scripts/ops/uji-salinan-paket-be-all.sh
PENANDA_BERKAS=services/barang_daftar.py; PENANDA='bisa_dijual_text'
UNIT_KONFIG=(b249 b260 b259 b257 b272 k8w b268b k1 b255 b276 b277)
SAB=()
for _u in "${UNIT_KONFIG[@]}"; do
  # gerbang-unit.sh menjalankan tes dari api_gateway: kolom 4 (tes) WAJIB relatif ke api_gateway; konfig per-unit memakai awalan backend/api_gateway/ (kolom 2 = berkas, relatif W, tak disentuh)
  while IFS= read -r -d '' _e; do _t="${_e##*|}"; SAB+=("${_e%|*}|${_t//backend\/api_gateway\//}"); done < <(bash -c ". \"$W/scripts/ops/gerbang-konfig/$_u.sh\"; printf '%s\\0' \"\${SAB[@]}\"")
done
