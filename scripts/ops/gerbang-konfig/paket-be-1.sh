# Konfig gerbang GABUNGAN paket-be-1 (17 Nov 2026, putusan MASTER): merge tanpa force/rebase dari fix/bug249-customers-nama, fix/bug260-items-teks-stok (+V502), feat/bug259-lembar-hitung,
# feat/bug257-histori-harga, feat/bug272-nilai-persediaan, feat/k8w-peringatan-terima, fix/bug268c-wac-pembalik-lain (memuat 268b). SAB = gabungan SEMUA konfig per-unit (dibaca dari berkasnya,
# tiap konfig di subshell supaya variabel A/T miliknya sendiri terurai); HARNESS = pembungkus semua harness per-unit. URUTAN RILIS: V502 DULU, baru kode.
W=/root/mh-pbe1; CABANG=paket-be-1; NAMA_JENDELA=pbe1
POLA_UNIT='bug249|customers_nama|cheque|expense|biaya|bug260|merek|barang_daftar|barang_standar_so|barang_p3|barang_teks|impor_barang|bug101_impor|bug259|lembar_hitung|bug257|histori_harga|bug272|nilai_persediaan|teks_persediaan|k8w|pemindahan|stock_transfer|bug246|bug268|sales_receipt|production|kosakata_keluar_jual|t10b3b|void_sj_faktur|grn|izin_rute|sql_placeholder'
HARNESS=scripts/ops/uji-salinan-paket-be-1.sh
PENANDA_BERKAS=services/barang_daftar.py; PENANDA='bisa_dijual_text'
UNIT_KONFIG=(b249 b260 b259 b257 b272 k8w b268b)
SAB=()
for _u in "${UNIT_KONFIG[@]}"; do
  while IFS= read -r -d '' _e; do SAB+=("$_e"); done < <(bash -c ". \"$W/scripts/ops/gerbang-konfig/$_u.sh\"; printf '%s\\0' \"\${SAB[@]}\"")
done
