# Konfig gerbang GABUNGAN paket-be-3-r2 (17 Nov 2026, putusan MASTER): merge tanpa force/rebase dari feat/bug255-dh-pelanggan-massal, fix/bug276-impor-ketik-satuan-tunggal,
# feat/bug277-ekspor-barang (ekspor di GET /items/impor/ekspor; /items/export lama utuh). SAB = gabungan SEMUA konfig per-unit (dibaca dari berkasnya, tiap konfig di subshell);
# HARNESS = pembungkus harness per-unit. Tanpa migrasi. Dasar = deploy/master (BUKAN paket-be-1/2: tak bergantung V502/V498).
W=/root/mh-pbe3r2; CABANG=paket-be-3-r2; NAMA_JENDELA=pbe3r2
POLA_UNIT='bug255|dh_pelanggan|customer|pelanggan|bug276|bug021|impor_barang|bug101_impor|bug092|bug277|ekspor_barang|izin_rute|sql_placeholder'
HARNESS=scripts/ops/uji-salinan-paket-be-3-r2.sh
PENANDA_BERKAS=services/dh_pelanggan_massal.py; PENANDA='SQL_UBAH'
UNIT_KONFIG=(b255 b276 b277)
SAB=()
for _u in "${UNIT_KONFIG[@]}"; do
  while IFS= read -r -d '' _e; do SAB+=("$_e"); done < <(bash -c ". \"$W/scripts/ops/gerbang-konfig/$_u.sh\"; printf '%s\\0' \"\${SAB[@]}\"")
done
