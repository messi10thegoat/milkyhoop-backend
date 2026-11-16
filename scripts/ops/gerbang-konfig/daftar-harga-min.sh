# Konfig gerbang terfokus Daftar Harga HARGA MINIMUM (BUG-040, 16 Nov 2026): V501 min_price (+ROLLBACK), tulis/ubah/detail/resolve, impor 251 kolom, D3 peringatan,
# penentu harga_minimum.py + pemasangan di /calculate + pratinjau tersimpan Penawaran/SO/Faktur (JUAL_DI_BAWAH_MINIMUM butuh_konfirmasi; konversi SO->Faktur tak mengulang).
# Cabang turunan 251 (feat/daftarharga-impor-251). MIGRASI V501: dipasang MASTER lewat apply_mig (--require-marker) SEBELUM kode; gerbang menguji maju+ROLLBACK di salinan (harness M0).
# Pemisah sabotase = "|", ekspresi sed memakai "@". Tiap sabotase terbukti MERAH lokal pada tes unit (12/12).
W=/root/mh-dh-min-040; CABANG=feat/daftarharga-min-040; NAMA_JENDELA=daftar-harga-min
POLA_UNIT='harga_minimum|harga_jual|harga_massal|price_lists|impor_harga|daftar_harga|dh_impor|dh_harga_minimum|dh_berjangka|peringatan_207|konfirmasi_pratinjau|bulk_f1|izin_rute|satuan_dasar|sales_order|quote|invoice|so_'
HARNESS=scripts/ops/uji-salinan-daftar-harga-min.sh
PENANDA_BERKAS=services/harga_minimum.py; PENANDA='async def hitung('
A=backend/api_gateway/app
T40=tests/unit/test_dh_harga_minimum_040.py
T51=tests/unit/test_dh_impor_251.py
SAB=(
 "m1 blok bukan peringatan|$A/services/harga_minimum.py|s@MODE_PENEGAKAN = \"peringatan\"@MODE_PENEGAKAN = \"blok\"@|$T40"
 "m2 konversi mengulang cek|$A/services/harga_minimum.py|s@Decimal(str(harga_asal\[i\])) == Decimal(str(x\[\"unit_price\"\])):@False:@|$T40"
 "m3 satuan beda ditebak|$A/services/harga_minimum.py|s@SD.kanon(r\[\"unit\"\] or brg\[\"dasar\"\], alias) == kanon and @@|$T40"
 "m4 diskon tak dihitung|$A/services/harga_minimum.py|s@harga = Decimal(str(x\[\"nilai\"\])) / q@harga = Decimal(str(x.get(\"unit_price\") or x[\"nilai\"]))@|$T40"
 "m5 pesan tanpa selisih|$A/services/harga_minimum.py|s@ (selisih Rp {_rp(m - h)})@@|$T40"
 "m6 tingkat qty diabaikan|$A/services/harga_minimum.py|s@ and Decimal(str(r\[\"min_quantity\"\])) <= q@@|$T40"
 "m7 minimum di atas harga lolos|$A/routers/price_lists.py|s@item.min_price > item.price@False@|$T51"
 "m8 resolve tanpa lantai|$A/services/harga_jual.py|s@harga_minimum=float(_r2(r\[\"min_price\"\])) if r\[\"min_price\"\] is not None else None@harga_minimum=None@|$T51"
 "m9 salinan berjangka tanpa lantai|$A/services/harga_massal.py|s@o.is_default_unit, o.min_price@o.is_default_unit, NULL@|$T51"
 "m10 SO tak terpasang|$A/routers/sales_orders.py|s@HMN.peringatan_jual(conn, ctx\[\"tenant_id\"\], body.customer_id, body.order_date@HMN.peringatan_jual(conn, ctx[\"tenant_id\"], None, body.order_date@|$T40"
 "m11 faktur lupa harga asal SO|$A/routers/sales_invoices.py|s@HMN.harga_asal_so(_conn, ctx\[\"tenant_id\"\], getattr(body, \"sales_order_id\", None))@HMN.harga_asal_so(_conn, ctx[\"tenant_id\"], None)@|$T40"
 "m12 D3 tanpa peringatan minimum|$A/services/harga_massal.py|s@\"DH_DI_BAWAH_MINIMUM\"@\"DH_X\"@|$T51"
)
