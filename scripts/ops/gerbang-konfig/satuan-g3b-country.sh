# Konfig gerbang GABUNGAN (17 Nov 2026, putusan MASTER hemat slot): Satuan G3-b (gabung + ganti nama menyebar + V504 alias) + BUG-289 Gudang country_text. SATU rantai, SATU jendela
# (V504 apply_mig dulu, lalu window_item dengan penanda satuan_sebar; MASTER cek penanda negara_text manual). Sabotase = b1-b19 (satuan) + c1-c6 (country). Pemisah "|", sed "@".
W=/root/mh-satuan-g3b-country; CABANG=feat/satuan-g3b-country; NAMA_JENDELA=satuan-g3b
POLA_UNIT='satuan_g3|satuan_sebar|satuan_pakai|satuan_master|satuan_tersimpan|satuan_baris|satuan_dasar|qty_dasar|routers.units|izin_rute|izin_rute_pratinjau|pagar_rute|gudang_country|gudang_g3|gudang_k|warehouse'
HARNESS=scripts/ops/uji-salinan-satuan-g3b-country.sh
PENANDA_BERKAS=services/satuan_sebar.py; PENANDA='async def siapkan('
A=backend/api_gateway/app
T=tests/unit/test_satuan_sebar.py
G=tests/unit/test_satuan_g3.py
TC=tests/unit/test_gudang_country_text.py
SAB=(
 "b1 duplikat identik: yang asli tak dipertahankan|$A/services/satuan_sebar.py|s@next((x for x in g if x\[\"nb\"\] == x\[\"base_unit\"\] and x\[\"nc\"\] == x\[\"conversion_unit\"\]), g\[0\])@g[0]@|$T"
 "b2 faktor beda tak diblok|$A/services/satuan_sebar.py|s@if len({_faktor(x\[\"conversion_factor\"\]) for x in g}) > 1:@if len({_faktor(x[\"conversion_factor\"]) for x in g}) > 99:@|$T"
 "b3 konversi jadi dasar sendiri selalu dibuang|$A/services/satuan_sebar.py|s@                if _faktor(x\[\"conversion_factor\"\]) == 1:@                if True:@|$T"
 "b4 harga beda tak diblok|$A/services/satuan_sebar.py|s@if len({Decimal(str(x\[\"price\"\])) for x in g}) > 1:@if len({Decimal(str(x[\"price\"])) for x in g}) > 99:@|$T"
 "b5 penulis teks tanpa saring tenant|$A/services/satuan_sebar.py|s@WHERE {pemilik} AND {cocok}\"@WHERE {cocok}\"@|$T"
 "b6 penulis tak menyaring no-op|$A/services/satuan_sebar.py|s@IS DISTINCT FROM (CASE@IS NOT NULL OR (CASE@|$T"
 "b7 nama menang atas singkatan|$A/services/satuan_sebar.py|s@return teks_singk if n in bentuk_singk else teks_nama if n in bentuk_nama else t@return teks_nama if n in bentuk_nama else teks_singk if n in bentuk_singk else t@|$T"
 "b8 dokumen historis ikut ditulis ulang|$A/services/satuan_sebar.py|s@(\"bom\", \"recipes\", \"yield_unit\", None, \"t.tenant_id = \$1\"),@(\"bom\", \"recipes\", \"yield_unit\", None, \"t.tenant_id = \$1\"), (\"dok\", \"sales_order_items\", \"unit\", None, \"t.tenant_id = \$1\"),@|$T"
 "b9 kamus tak membaca tabel alias|$A/services/satuan_dasar.py|s@SELECT u.name, a.alias FROM product_unit_aliases a@SELECT u.name, a.alias FROM product_units a@|$T"
 "b10 sumber gabung tak dinonaktifkan|$A/routers/units.py|s@UPDATE product_units SET is_active = false@UPDATE product_units SET is_active = true@|$T"
 "b11 nama lama tak dicatat alias|$A/routers/units.py|s@            await SS.catat_alias(conn, tid, tujuan, lama_teks)@            pass@|$T"
 "b12 sumber bawaan boleh digabung|$A/services/satuan_rencana.py|s@    if _bawaan(s):@    if False:@|$T"
 "b13 gabung ke diri sendiri diterima|$A/services/satuan_rencana.py|s@    if s\[\"id\"\] == t\[\"id\"\]:@    if False:@|$T"
 "b14 tujuan nonaktif diterima|$A/services/satuan_rencana.py|s@    elif t\[\"jenis\"\] == \"master\" and not t.get(\"is_active\", True):@    elif False:@|$T"
 "b15 ganti nama tak menyebar|$A/routers/units.py|s@            if r.get(\"sebar\") is not None:@            if False:@|$T"
 "b16 tujuan bawaan tak dibuatkan baris|$A/routers/units.py|s@            if t\[\"jenis\"\] == \"bawaan\":@            if False:@|$T"
 "b17 izin merge hilang|$A/middleware/permission_middleware.py|s@/merge(/preview)?\$\", \[@/mergex(/preview)?\$\", \[@|$T"
 "b18 gabung bawaan dikatakan tersedia|$A/services/satuan_pakai.py|s@a\[\"gabung\"\] = tak(\"Satuan bawaan tidak bisa digabung.\")@a[\"gabung\"] = ada()@|$G"
 "b19 nama bawaan-teks dianggap belum terdaftar|$A/services/satuan_rencana.py|s@        if jenis == \"terpakai\" and u.get(\"bawaan\"):@        if False:@|$T"
 "c1 kode ID tak jadi Indonesia|$A/services/gudang_daftar.py|s@NEGARA = {\"ID\": \"Indonesia\"@NEGARA = {\"ID\": \"ID\"@|$TC"
 "c2 kode peka huruf|$A/services/gudang_daftar.py|s@NEGARA.get(t.upper(), t)@NEGARA.get(t, t)@|$TC"
 "c3 kosong bukan None|$A/services/gudang_daftar.py|s@    if not t:@    if False:@|$TC"
 "c4 baca tak mengisi country_text|$A/routers/warehouses.py|s@country_text=GD.negara_text(row\[\"country\"\])@country_text=None@|$TC"
 "c5 skema tanpa medan country_text|$A/schemas/warehouses.py|s@    country_text: Optional\[str\] = None@    country_x: Optional[str] = None@|$TC"
 "c6 kode dua huruf bukan kapital di peta|$A/services/gudang_daftar.py|s@\"MY\": \"Malaysia\"@\"my\": \"Malaysia\"@|$TC"
)
