# Konfig gerbang terfokus Daftar Harga BERJANGKA (BUG-245, 15 Nov 2026): V500 (indeks unik + COALESCE(start_date)), HARGA_GANDA per tanggal mulai, resolve mulai terbaru, D3 masa depan = baris berjangka.
# MIGRASI V500: dipasang MASTER lewat apply_mig (--require-marker); gerbang menguji maju+ROLLBACK di salinan (harness H0/H5).
# Pemisah sabotase = "|", ekspresi sed memakai "@".
W=/root/mh-dh-berjangka-r5; CABANG=feat/daftarharga-berjangka-r5; NAMA_JENDELA=daftar-harga-berjangka
POLA_UNIT='harga_massal|harga_jual|price_lists|bulk_price_lists|harga_daftar|daftar_harga|dh_berjangka|satuan_dasar'
HARNESS=scripts/ops/uji-salinan-daftar-harga-berjangka.sh
PENANDA_BERKAS=services/harga_massal.py; PENANDA='async def terapkan('
A=backend/api_gateway/app
T=tests/unit/test_dh_berjangka.py
SAB=(
 "b1 duplikat tanpa tanggal mulai|$A/routers/price_lists.py|s@start_date IS NOT DISTINCT FROM@start_date IS DISTINCT FROM@|$T"
 "b2 kunci badan tanpa tanggal mulai|$A/routers/price_lists.py|s@item.min_quantity, item.start_date)@item.min_quantity)@|$T"
 "b3 pesan ganda tanpa tanggal|$A/routers/price_lists.py|s@jumlah minimum, dan tanggal mulai itu@jumlah minimum itu@|$T"
 "b4 pilih baris tanpa tie-break tanggal|$A/services/harga_jual.py|s@, x\[\"start_date\"\] or date.min))@, date.min))@|$T"
 "b5 pilih baris qty terendah|$A/services/harga_jual.py|s@return max(cocok, key=lambda x: (Decimal@return min(cocok, key=lambda x: (Decimal@|$T"
 "b6 masa depan menimpa|$A/services/harga_massal.py|s@if mulai and mulai > hari_ini:@if False:@|$T"
 "b7 baris baru mewarisi akhir lama|$A/services/harga_massal.py|s@o.discount_percent, \$5, NULL,@o.discount_percent, \$5, o.end_date,@|$T"
 "b8 tanggal sama selalu menambah|$A/services/harga_massal.py|s@if sama == \"UPDATE 0\":@if True:@|$T"
 "b9 peringatan nanti kembali ke jatuh tingkat|$A/services/harga_massal.py|s@harga lama tetap berlaku sampai tanggal itu@harga lama dipakai tingkat jumlah yang lebih rendah@|$T"
)
