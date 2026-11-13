# Konfig gerbang terfokus Daftar Harga D3 (13 Nov 2026): ubah harga massal -- dasar harga lama | HPP (WAC x faktor), pembulatan kelipatan x arah, harga nol = peringatan konfirmasi, tanggal berlaku, pratinjau per barang, idempotensi + audit. Tanpa migrasi.
# Pemisah sabotase = "|", ekspresi sed memakai "@".
W=/root/mh-dh-d3; CABANG=feat/daftarharga-d3; NAMA_JENDELA=daftar-harga-d3
POLA_UNIT='harga_massal|bulk_price_lists|harga_daftar|harga_aksi|price_lists|bulk|permission_middleware|daftar_harga|satuan_dasar'
HARNESS=scripts/ops/uji-salinan-daftar-harga-d3.sh
PENANDA_BERKAS=services/harga_massal.py; PENANDA='async def terapkan('
A=backend/api_gateway/app
T=tests/unit/test_daftar_harga_d3.py
SAB=(
 "s1 pembulatan atas jadi terdekat|$A/services/harga_massal.py|s@\"atas\": ROUND_CEILING@\"atas\": ROUND_HALF_UP@|$T"
 "s2 pembulatan bawah jadi atas|$A/services/harga_massal.py|s@\"bawah\": ROUND_FLOOR@\"bawah\": ROUND_CEILING@|$T"
 "s3 harga negatif lolos|$A/services/harga_massal.py|s@return None if baru < 0 else baru@return baru@|$T"
 "s4 HPP tak ada tak diblok|$A/services/harga_massal.py|s@if dasar == \"hpp\" and any(b.get(\"hpp\") is None for b in baris):@if False:@|$T"
 "s5 dasar HPP memakai harga lama|$A/services/harga_massal.py|s@dasar_nilai = hpp if dasar == \"hpp\" else Decimal(str(b\[\"price\"\]))@dasar_nilai = Decimal(str(b[\"price\"]))@|$T"
 "s6 faktor konversi diabaikan|$A/services/harga_massal.py|s@Decimal(str(wac)) \* h\[\"faktor\"\]@Decimal(str(wac))@|$T"
 "s7 WAC nol diterima|$A/services/harga_massal.py|s@or Decimal(str(wac)) <= 0@or False@|$T"
 "s8 peringatan harga nol dicabut|$A/services/harga_massal.py|s@nol = any(h\[\"ke\"\] == 0 for h in hasil)@nol = False@|$T"
 "s9 konfirmasi harga nol tak dituntut|$A/routers/bulk_price_lists.py|s@if r\[\"rencana\"\]\[\"ada_harga_nol\"\] and not body.konfirmasi_harga_nol:@if False:@|$T"
 "s10 kunci idempotensi tak wajib|$A/routers/bulk_price_lists.py|s@    if not kunci:@    if False:@|$T"
 "s11 pratinjau tak baca-saja|$A/routers/bulk_price_lists.py|s@baca_saja=True@baca_saja=False@|$T"
 "s12 izin ubah massal dicabut|$A/middleware/permission_middleware.py|s@price-lists/\[^/\]+/bulk/price@price-lists/\[^/\]+/bulk/priceX@|$T"
 "s13 penulis beda penentu dari pratinjau|$A/routers/bulk_price_lists.py|s@r = await HM.ukur_rencana(conn, ctx@r = await HM.ukur_rencanaX(conn, ctx@|$T"
 "s14 audit/per-barang dicabut|$A/routers/bulk_price_lists.py|s@bulk.jalankan_per_item(@bulk.jalan_item(@|$T"
 "s15 tanggal berlaku tak ditulis|$A/services/harga_massal.py|s@SET price = \$4, start_date = \$5 WHERE@SET price = \$4 WHERE@|$T"
 "s16 dasar tak diteruskan ke penentu|$A/routers/bulk_price_lists.py|s@dasar=body.dasar, @@|$T"
)
