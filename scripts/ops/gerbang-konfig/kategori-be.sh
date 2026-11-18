# Konfig gerbang KATEGORI-BE (18 Nov 2026): BUG-282 akun bawaan per kategori (V505, disalin ke barang BARU) + aksi_tersedia kategori gabung/pindahkan_barang/atur_akun.
# Migrasi V505 aditif: MASTER menerapkan (apply_mig) SEBELUM kode hidup. Pemisah sabotase = "|", sed memakai "@".
W=/root/mh-kat; CABANG=feat/kategori-be-r2; NAMA_JENDELA=kategori
POLA_UNIT='kategori|bug282|izin_rute|impor_barang|aksi_tersedia|k1_|k2_|k3_|k4_|k5_|item_field|cari_varian|saldo_awal|item_nama'
HARNESS=scripts/ops/uji-salinan-kategori.sh
PENANDA_BERKAS=services/kategori_akun.py; PENANDA='def rencana_akun'
A=backend/api_gateway/app
T=tests/unit/test_bug282_kategori_akun.py
SAB=(
 "k1 salin akun tak dipanggil saat buat barang|$A/routers/items.py|s@    await KAK.salin_ke_barang(conn, tenant_id, body)@    pass@|$T"
 "k2 isian eksplisit tak menang|$A/services/kategori_akun.py|s@ and not getattr(body, KOLOM\[j\], None) and @ and @|$T"
 "k3 persediaan/HPP disalin ke jasa|$A/services/kategori_akun.py|s@(stok or j not in (\"persediaan\", \"hpp\"))@True@|$T"
 "k4 akun kategori nonaktif ikut disalin|$A/services/kategori_akun.py|s@if a and a\[\"is_active\"\] and not a.get@if a and not a.get@|$T"
 "k5 tipe akun tak diperiksa|$A/services/kategori_akun.py|s@        elif not tipe_sah(j, a):@        elif False:@|$T"
 "k6 akun header lolos|$A/services/kategori_akun.py|s@        elif a.get(\"is_header\"):@        elif False:@|$T"
 "k7 gabung: alasan sub kategori hilang|$A/services/kategori_daftar.py|s@    if jumlah_anak:@    if False:@|$T"
 "k8 pindahkan_barang selalu tersedia|$A/services/kategori_daftar.py|s@    return None if jumlah_barang else ALASAN_TAK_ADA_BARANG@    return None@|$T"
 "k9 tanpa-perubahan lolos|$A/services/kategori_akun.py|s@    if not ubah:@    if False:@|$T"
 "k10 izin rute accounts hilang|$A/middleware/permission_middleware.py|s@parent.accounts)@parent.zzz)@|$T"
 "k11 akun tenant lain tak disaring|$A/services/kategori_akun.py|s@FROM chart_of_accounts WHERE tenant_id = \$1 AND id = ANY@FROM chart_of_accounts WHERE \$1::text IS NOT NULL AND id = ANY@|$T"
 "k12 baris daftar tanpa akun_text|$A/services/kategori_daftar.py|s@\"akun_text\": KAK.akun_text(akun)@\"akun_text\": None@|$T"
 "k13 salin tanpa saring tenant|$A/services/kategori_akun.py|s@FROM item_categories WHERE tenant_id = \$1 AND lower(name)@FROM item_categories WHERE \$1::text IS NOT NULL AND lower(name)@|$T"
)
