"""DAFTAR-HARGA (4 Nov 2026): SATU penentu harga jual saran (services/harga_jual) + rute resolve.

Spek (putusan MASTER): daftar pelanggan (null/nonaktif/di luar masa berlaku -> daftar BAWAAN tenant) x barang x SATUAN (alias kamus) x
tingkat qty tertinggi <= qty -> harga per satuan konversi (unit_conversions.sales_price) -> harga dasar (products.sales_price) x faktor.
Tanpa harga dasar -> None "kosong"; satuan tanpa konversi -> blok. Uang Decimal 2dp HALF_UP. Tiruan DB mencocokkan POLA (tabel + kolom
kunci), bukan teks SQL utuh (pelajaran MASTER 3 Nov)."""
import ast
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.services import harga_jual as HJ

T = "kaos-biru-konveksi"
P, P2 = uuid.uuid4(), uuid.uuid4()
WARUNG, ECERAN, LAMA = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
C_WARUNG, C_POLOS, C_LAMA = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
HARI = date(2026, 11, 4)
APP = Path(HJ.__file__).resolve().parents[1]


def _daftar(i, kode, bawaan=False, aktif=True, mulai=None, akhir=None):
    return {"id": i, "code": kode, "name": kode.title(), "is_active": aktif, "is_default": bawaan, "start_date": mulai, "end_date": akhir}


class Kon:
    def __init__(self, daftar=None, baris=None, harga_dasar=None, konversi=None, harga_beli=None):
        self.daftar = daftar if daftar is not None else [_daftar(WARUNG, "WARUNG"), _daftar(ECERAN, "ECERAN", bawaan=True),
                                                          _daftar(LAMA, "LAMA", aktif=False)]
        self.pelanggan = {C_WARUNG: WARUNG, C_POLOS: None, C_LAMA: LAMA}
        self.kategori = {}  # BUG-338A: customer_id -> price_list_id DH kategori AKTIF (kategori nonaktif/tanpa kategori = tak ada)
        self.baris = baris if baris is not None else [
            {"pl": WARUNG, "item_id": P, "unit": "Dus", "price": Decimal("100000"), "min_quantity": Decimal("1")},
            {"pl": WARUNG, "item_id": P, "unit": "Dus", "price": Decimal("95000"), "min_quantity": Decimal("10")},
            {"pl": WARUNG, "item_id": P, "unit": "Dus", "price": Decimal("90000.455"), "min_quantity": Decimal("50")},
            {"pl": ECERAN, "item_id": P, "unit": None, "price": Decimal("110000"), "min_quantity": Decimal("1")},
            {"pl": WARUNG, "item_id": P2, "unit": "kg", "price": Decimal("15000"), "min_quantity": Decimal("1")}]
        self.harga_dasar = harga_dasar if harga_dasar is not None else {P: Decimal("105000"), P2: None}
        self.konversi = konversi if konversi is not None else [
            {"product_id": P, "base_unit": "Dus", "conversion_unit": "Karton", "conversion_factor": Decimal("12"), "sales_price": None},
            {"product_id": P, "base_unit": "Dus", "conversion_unit": "Pak", "conversion_factor": Decimal("6"), "sales_price": Decimal("600000")}]
        self.harga_beli = harga_beli if harga_beli is not None else {P: Decimal("80000"), P2: Decimal("9000.5")}
        self.tanya = []

    async def fetchval(self, sql, *a):
        q = " ".join(sql.split())
        if "FROM customers" in q and "price_list_id" in q:
            return self.pelanggan.get(a[0])
        raise AssertionError(q[:90])

    async def fetchrow(self, sql, *a):
        q = " ".join(sql.split())
        if "customer_categories" in q:
            return {"price_list_id": self.kategori[a[0]], "kategori_name": "Warung Besar"} if a[0] in self.kategori else None
        if "FROM price_lists" in q:
            if "is_default" in q:
                return next((d for d in self.daftar if d["is_default"] and d["is_active"]), None)
            return next((d for d in self.daftar if d["id"] == a[1]), None)
        raise AssertionError(q[:90])

    async def fetch(self, sql, *a):
        q = " ".join(sql.split())
        if "FROM product_units" in q:
            return [{"name": "Dus", "abbreviation": "dus"}, {"name": "Kilogram", "abbreviation": "kg"}, {"name": "Karton", "abbreviation": "krt"}]
        if "FROM products" in q and "AS dasar" in q:
            return [{"id": P, "nama_produk": "Minyak 1L", "dasar": "Dus"}, {"id": P2, "nama_produk": "Gula", "dasar": "Kilogram"}]
        self.tanya.append(q)
        if "FROM products" in q and ("sales_price AS harga" in q or "purchase_price AS harga" in q):  # BUG-032-BE: kolom per arah
            sumber = self.harga_dasar if "sales_price" in q else self.harga_beli
            return [{"id": k, "harga": v} for k, v in sumber.items()]
        if "FROM unit_conversions" in q and "conversion_factor" in q:
            return [k for k in self.konversi]
        if "FROM unit_conversions" in q and ("sales_price AS harga" in q or "purchase_price AS harga" in q):
            kol = "sales_price" if "sales_price AS harga" in q else "purchase_price"
            return [{**k, "harga": k.get(kol)} for k in self.konversi if k.get(kol) is not None]
        if "FROM price_list_items" in q:
            return [{**b, "start_date": None, "end_date": None, "min_price": b.get("min_price"), "discount_percent": b.get("discount_percent")} for b in self.baris if b["pl"] == a[0]]
        raise AssertionError(q[:90])


async def _satu(kon, pelanggan, item, unit, qty, tgl=HARI):
    r = await HJ.harga_saran(kon, T, pelanggan, tgl, [{"key": "k1", "item_id": item, "unit": unit, "quantity": qty}])
    return r["price_list"], r["lines"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("qty,harga,tingkat", [(1, 100000.0, 1.0), (9, 100000.0, 1.0), (10, 95000.0, 10.0), (49, 95000.0, 10.0),
                                               (50, 90000.46, 50.0)])
async def test_daftar_pelanggan_tingkat_qty_tertinggi_dan_dibulatkan_2dp(qty, harga, tingkat):
    pl, b = await _satu(Kon(), C_WARUNG, P, "Dus", qty)
    assert pl == {"id": str(WARUNG), "code": "WARUNG", "name": "Warung", "asal": "pelanggan", "kategori_name": None}
    assert (b["harga"], b["sumber"], b["tingkat_min_qty"], b["daftar"]) == (harga, "daftar", tingkat, {"id": str(WARUNG), "code": "WARUNG"})


@pytest.mark.asyncio
async def test_pelanggan_tanpa_daftar_atau_daftarnya_nonaktif_memakai_bawaan():
    for c in (C_POLOS, C_LAMA, None):
        pl, b = await _satu(Kon(), c, P, None, 1)
        assert pl["code"] == "ECERAN" and pl["asal"] == "bawaan" and (b["harga"], b["sumber"]) == (110000.0, "daftar"), c


@pytest.mark.asyncio
async def test_baris_daftar_tanpa_satuan_berarti_satuan_dasar_dan_alias_kamus():
    _, b = await _satu(Kon(), C_POLOS, P, "dus", 1)  # alias -> Dus = dasar -> baris unit NULL ECERAN
    assert (b["harga"], b["sumber"]) == (110000.0, "daftar")
    _, b2 = await _satu(Kon(), C_WARUNG, P2, "Kilogram", 2)  # baris "kg" == "Kilogram"
    assert (b2["harga"], b2["sumber"]) == (15000.0, "daftar")


@pytest.mark.asyncio
async def test_satuan_lain_tanpa_baris_dh_pakai_dh_dasar_kali_faktor_dh_menang():
    # BUG-320 (pola SAP): daftar WARUNG hanya punya baris Dus (dasar) -> Pak/Karton = DH dasar x faktor, tingkat qty di satuan dasar
    _, b = await _satu(Kon(), C_WARUNG, P, "Pak", 1)  # 1 Pak = 6 Dus -> tingkat 1 (100.000) x 6; DH menang atas sales_price konversi 600.000
    assert (b["harga"], b["sumber"], b["faktor"], b["tingkat_min_qty"], b["sumber_text"]) == (600000.0, "daftar_konversi", 6.0, 1.0, "Daftar harga (dari satuan dasar)")
    _, b2 = await _satu(Kon(), C_WARUNG, P, "Karton", 1)  # 1 Karton = 12 Dus -> tingkat 10 (95.000) x 12
    assert (b2["harga"], b2["sumber"], b2["tingkat_min_qty"], b2["daftar"]) == (1140000.0, "daftar_konversi", 10.0, {"id": str(WARUNG), "code": "WARUNG"})


@pytest.mark.asyncio
async def test_satuan_lain_tanpa_dh_sama_sekali_tetap_harga_satuan_lalu_harga_dasar():
    kon = Kon(daftar=[], baris=[])
    _, b = await _satu(kon, C_POLOS, P, "Pak", 1)
    assert (b["harga"], b["sumber"], b["sumber_text"]) == (600000.0, "harga_satuan", "Harga satuan")
    _, b2 = await _satu(kon, C_POLOS, P, "Karton", 1)
    assert (b2["harga"], b2["sumber"], b2["sumber_text"]) == (1260000.0, "harga_dasar", "Harga dasar")


@pytest.mark.asyncio
async def test_dh_punya_baris_satuan_sendiri_tetap_menang_dan_dasar_tak_berubah():
    baris = [{"pl": WARUNG, "item_id": P, "unit": "Dus", "price": Decimal("100000"), "min_quantity": Decimal("1")},
             {"pl": WARUNG, "item_id": P, "unit": "Karton", "price": Decimal("1100000"), "min_quantity": Decimal("1")}]
    _, b = await _satu(Kon(baris=baris), C_WARUNG, P, "Karton", 2)
    assert (b["harga"], b["sumber"], b["sumber_text"]) == (1100000.0, "daftar", "Daftar harga")
    _, b2 = await _satu(Kon(baris=baris), C_WARUNG, P, "Dus", 2)
    assert (b2["harga"], b2["sumber"]) == (100000.0, "daftar")


@pytest.mark.asyncio
async def test_uji_d2_dh_pcs_dus_12_pcs_dasar_15000():
    # UJI-D2: DH Pcs>=1 14.000, Dus = 12 Pcs, harga dasar 15.000 -> 2 Dus = 336.000 (kontrol merah: 15.000x12 = 180.000/Dus)
    kon = Kon(baris=[{"pl": WARUNG, "item_id": P2, "unit": "kg", "price": Decimal("14000"), "min_quantity": Decimal("1")},
                     {"pl": WARUNG, "item_id": P2, "unit": "kg", "price": Decimal("13000"), "min_quantity": Decimal("60")}],
              harga_dasar={P: Decimal("105000"), P2: Decimal("15000")},
              konversi=[{"product_id": P2, "base_unit": "Kilogram", "conversion_unit": "Dus", "conversion_factor": Decimal("12"), "sales_price": None}])
    _, b = await _satu(kon, C_WARUNG, P2, "Dus", 2)
    assert (b["harga"], b["sumber"]) == (168000.0, "daftar_konversi")  # harga per Dus 14.000 x 12; 2 Dus = 336.000
    assert b["harga"] * 2 == 336000.0 and b["harga"] != 180000.0
    _, b5 = await _satu(kon, C_WARUNG, P2, "Dus", 5)  # 5 Dus = 60 dasar -> tingkat >=60 (13.000 x 12)
    assert (b5["harga"], b5["tingkat_min_qty"]) == (156000.0, 60.0)


@pytest.mark.asyncio
async def test_tanpa_daftar_sama_sekali_harga_dasar():
    pl, b = await _satu(Kon(daftar=[], baris=[]), C_POLOS, P, None, 3)
    assert pl is None and (b["harga"], b["sumber"]) == (105000.0, "harga_dasar")


@pytest.mark.asyncio
async def test_tanpa_harga_dasar_kosong_bukan_blok_dan_tanpa_konversi_blok():
    _, b = await _satu(Kon(daftar=[], baris=[]), None, P2, None, 1)
    assert (b["harga"], b["sumber"], b["blok"]) == (None, "kosong", None)
    _, b2 = await _satu(Kon(daftar=[], baris=[]), None, P, "Lusin", 1)
    assert b2["harga"] is None and b2["blok"]["code"] == "SATUAN_TAK_TERKONVERSI"


@pytest.mark.asyncio
async def test_daftar_di_luar_masa_berlaku_tak_dipakai():
    d = [_daftar(WARUNG, "WARUNG", akhir=date(2026, 10, 31)), _daftar(ECERAN, "ECERAN", bawaan=True)]
    pl, b = await _satu(Kon(daftar=d), C_WARUNG, P, None, 1)
    assert pl["code"] == "ECERAN" and b["harga"] == 110000.0


@pytest.mark.asyncio
async def test_banyak_baris_satu_panggilan_dan_kunci_dikembalikan():
    r = await HJ.harga_saran(Kon(), T, C_WARUNG, HARI, [{"key": "a", "item_id": P, "unit": "Dus", "quantity": 10},
                                                        {"key": "b", "item_id": P, "unit": "Karton", "quantity": 1}])
    assert [(x["key"], x["harga"], x["sumber"]) for x in r["lines"]] == [("a", 95000.0, "daftar"), ("b", 1140000.0, "daftar_konversi")]  # BUG-320: 1 Karton = 12 Dus -> tingkat 10 (95.000) x 12


def _fungsi(rel, nama):
    src = (APP / rel).read_text()
    return next(n for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nama)


def test_rute_resolve_read_only_memakai_penentu_satu():
    r = ast.unparse(_fungsi("routers/price_lists.py", "resolve_harga"))
    assert "readonly=True" in r and "HJ.harga_saran" in r


def test_baris_harga_ditulis_satu_penulis_tervalidasi():
    for nama in ("create_price_list", "add_item_to_price_list"):
        assert "tulis_baris_harga(" in ast.unparse(_fungsi("routers/price_lists.py", nama)), nama
    t = ast.unparse(_fungsi("routers/price_lists.py", "tulis_baris_harga"))
    assert "siapkan_baris_harga(" in t and "UniqueViolationError" in t  # 4 Nov: validasi = penentu bersama tulis + pratinjau
    v = ast.unparse(_fungsi("routers/price_lists.py", "siapkan_baris_harga"))
    assert "SD.qty_dasar" in v and "FROM products WHERE id = $1 AND tenant_id = $2" in v and "HARGA_GANDA" in v


# --- izin resolve (catatan MASTER 4 Nov): staf yang hanya boleh BACA SO / Penawaran tetap dapat harga saran ---
JALUR_RESOLVE = {m: f"/api/price-lists/resolve/{m}" for m in ("sales_order", "quote", "sales_invoice")}


def test_resolve_per_modul_pemanggil_butuh_baca_modul_itu_bukan_izin_daftar_harga():
    from app.middleware.permission_middleware import izin_untuk_jalur
    for m, j in JALUR_RESOLVE.items():
        assert izin_untuk_jalur(j, "POST") == (m, "R"), j
    assert izin_untuk_jalur("/api/price-lists/resolve/payroll", "POST") == ("price_list", "C")  # modul lain = jatuh ke pola umum
    assert izin_untuk_jalur("/api/price-lists", "POST") == ("price_list", "C")


class _Ctx:
    membership_active, business_role_id, business_role_code = True, uuid.uuid4(), "STAFF"


class _PE:
    def __init__(self, boleh):
        self.boleh = boleh

    async def get_user_context(self, **_):
        return _Ctx()

    async def can(self, ctx, aksi, modul):
        return (modul, aksi) in self.boleh


@pytest.mark.asyncio
@pytest.mark.parametrize("boleh,harap", [({("sales_order", "R")}, {"sales_order"}), ({("quote", "R")}, {"quote"}),
                                         ({("sales_invoice", "R")}, {"sales_invoice"}), (set(), set())])
async def test_peran_staf_hanya_baca_satu_modul_hanya_resolve_modul_itu(monkeypatch, boleh, harap):
    from app.middleware import permission_middleware as PM
    monkeypatch.setattr(PM, "get_policy_engine", lambda: _PE(boleh))
    jalur = list(JALUR_RESOLVE.values()) + ["/api/price-lists"]
    r = await PM.izin_rute_pengguna({"user_id": "u", "tenant_id": T}, jalur, "POST")
    assert {m for m, j in JALUR_RESOLVE.items() if r[j]} == harap and r["/api/price-lists"] is False


def test_rute_resolve_dibatasi_tiga_modul_penjualan():
    a = _fungsi("routers/price_lists.py", "resolve_harga")
    assert "@router.post('/resolve/{modul}')" in ast.unparse(a).split("\n")[0]
    assert "Literal['sales_order', 'quote', 'sales_invoice', 'purchase_order', 'bill']" in ast.unparse(a.args)


def test_jalur_harga_lama_dihapus_bukan_dibiarkan():
    # /get-price (nol pemanggil FE/BE/chat) + tabel customer_price_lists (0 baris; digantikan customers.price_list_id) dihapus
    src = {rel: (APP / rel).read_text() for rel in ("routers/price_lists.py", "routers/customers.py", "schemas/price_lists.py")}
    assert all("/get-price" not in t and "ItemPrice" not in t and "customer_price_lists" not in t for t in src.values())


def test_detail_pelanggan_memuat_daftar_harganya():
    g = ast.unparse(_fungsi("routers/customers.py", "get_customer"))
    assert "price_list_id" in g and "price_list_name" in g
    from app.schemas.customers import CustomerDetail
    assert {"price_list_id", "price_list_name"} <= set(CustomerDetail.model_fields)


# ---------------------------------------------------------------- BUG-032-BE harga BELI saran (putusan pemilik LANGSUNG 5 Nov 2026)
async def _beli(kon, item, unit, qty):
    r = await HJ.harga_saran(kon, T, C_WARUNG, HARI, [{"key": "k1", "item_id": item, "unit": unit, "quantity": qty}], "beli")
    return r["price_list"], r["lines"][0]


@pytest.mark.asyncio
async def test_beli_konversi_tanpa_harga_beli_jatuh_ke_dasar_kali_faktor():
    """Kasus tester BUG-032: konversi karton purchase_price NULL -> 80.000 x 12 (dulu FE mendapat 0)."""
    kon = Kon()
    daftar, b = await _beli(kon, P, "Karton", 5)
    assert daftar is None and b["harga"] == 960000.0 and b["sumber"] == "harga_dasar" and b["faktor"] == 12.0
    assert not any("price_list" in q or "FROM customers" in q for q in kon.tanya)  # beli tak memakai daftar harga pelanggan


@pytest.mark.asyncio
async def test_beli_harga_konversi_diisi_menang():
    kon = Kon(konversi=[{"product_id": P, "base_unit": "Dus", "conversion_unit": "Karton", "conversion_factor": Decimal("12"),
                         "sales_price": Decimal("1300000"), "purchase_price": Decimal("950000")}])
    _, b = await _beli(kon, P, "krt", 1)
    assert b["harga"] == 950000.0 and b["sumber"] == "harga_satuan"


@pytest.mark.asyncio
async def test_beli_satuan_dasar_dan_sen_dan_kosong():
    kon = Kon()
    assert (await _beli(kon, P, None, 1))[1]["harga"] == 80000.0
    assert (await _beli(kon, P2, "kg", 1))[1]["harga"] == 9000.5
    _, b = await _beli(Kon(harga_beli={P: None, P2: None}), P, "Karton", 1)
    assert b["harga"] is None and b["sumber"] == "kosong"


@pytest.mark.asyncio
async def test_jual_tetap_memakai_sales_price():
    _, b = await _satu(Kon(daftar=[]), None, P, "Karton", 1)
    assert b["harga"] == 1260000.0 and b["sumber"] == "harga_dasar"  # 105.000 x 12, bukan harga beli


def test_rute_resolve_modul_beli_dan_izin():
    src = (APP / "routers" / "price_lists.py").read_text()
    assert 'modul: Literal["sales_order", "quote", "sales_invoice", "purchase_order", "bill"]' in src
    assert 'arah = "beli" if modul in ("purchase_order", "bill") else "jual"' in src
    mid = (APP / "middleware" / "permission_middleware.py").read_text()
    assert '(r"^/api/price-lists/resolve/purchase_order$", ["POST"], "purchase_order", "R")' in mid
    assert '(r"^/api/price-lists/resolve/bill$", ["POST"], "purchase_invoice", "R")' in mid


# ---- BUG-339 (putusan pemilik LANGSUNG 22 Nov): diskon % baris dasar ikut ke satuan turunan; minimum turunan = minimum dasar x faktor; Dus eksplisit menang; tingkat di qty dasar ----
def _konv_kg(faktor="24"):
    return [{"product_id": P2, "base_unit": "Kilogram", "conversion_unit": "Dus", "conversion_factor": Decimal(faktor), "sales_price": None}]


def _dasar_kg(**ekstra):
    return [{"pl": WARUNG, "item_id": P2, "unit": "kg", "price": Decimal("14000"), "min_quantity": Decimal("1"), **ekstra},
            {"pl": WARUNG, "item_id": P2, "unit": "kg", "price": Decimal("13000"), "min_quantity": Decimal("120"), **ekstra}]


@pytest.mark.asyncio
async def test_bug339_diskon_dan_minimum_dasar_ikut_ke_dus_turunan():
    kon = Kon(baris=_dasar_kg(discount_percent=Decimal("5"), min_price=Decimal("12000")), harga_dasar={P: Decimal("1"), P2: Decimal("15000")}, konversi=_konv_kg())
    _, b = await _satu(kon, C_WARUNG, P2, "Dus", 1)
    assert (b["sumber"], b["harga"], b["diskon_persen"], b["harga_minimum"]) == ("daftar_konversi", 336000.0, 5.0, 288000.0)  # 14.000x24; min 12.000x24


@pytest.mark.asyncio
async def test_bug339_diskon_nol_eksplisit_tetap_nol_dan_null_tetap_null():
    kon = Kon(baris=_dasar_kg(discount_percent=Decimal("0")), harga_dasar={P: Decimal("1"), P2: Decimal("15000")}, konversi=_konv_kg())
    _, b = await _satu(kon, C_WARUNG, P2, "Dus", 1)
    assert (b["diskon_persen"], b["harga_minimum"]) == (0.0, None)
    kon2 = Kon(baris=_dasar_kg(), harga_dasar={P: Decimal("1"), P2: Decimal("15000")}, konversi=_konv_kg())
    _, b2 = await _satu(kon2, C_WARUNG, P2, "Dus", 1)
    assert (b2["diskon_persen"], b2["harga_minimum"]) == (None, None)


@pytest.mark.asyncio
async def test_bug339_tingkat_dievaluasi_pada_qty_dasar_5_dus_kali_24_sama_120():
    kon = Kon(baris=_dasar_kg(), harga_dasar={P: Decimal("1"), P2: Decimal("15000")}, konversi=_konv_kg())
    _, b4 = await _satu(kon, C_WARUNG, P2, "Dus", 4)   # 96 dasar < 120 -> tingkat 1 (14.000)
    assert (b4["harga"], b4["tingkat_min_qty"]) == (336000.0, 1.0)
    _, b5 = await _satu(kon, C_WARUNG, P2, "Dus", 5)   # 120 dasar -> tingkat 120 (13.000 x 24)
    assert (b5["harga"], b5["tingkat_min_qty"]) == (312000.0, 120.0)


@pytest.mark.asyncio
async def test_bug339_baris_dus_eksplisit_menang_dengan_diskon_dan_minimum_miliknya():
    baris = _dasar_kg(discount_percent=Decimal("5"), min_price=Decimal("12000")) + [
        {"pl": WARUNG, "item_id": P2, "unit": "Dus", "price": Decimal("330000"), "min_quantity": Decimal("1"), "discount_percent": Decimal("2"), "min_price": Decimal("300000")}]
    kon = Kon(baris=baris, harga_dasar={P: Decimal("1"), P2: Decimal("15000")}, konversi=_konv_kg())
    _, b = await _satu(kon, C_WARUNG, P2, "Dus", 1)
    assert (b["sumber"], b["harga"], b["diskon_persen"], b["harga_minimum"]) == ("daftar", 330000.0, 2.0, 300000.0)


@pytest.mark.asyncio
async def test_bug339_minimum_faktor_pecahan_dibulatkan_2dp():
    kon = Kon(baris=_dasar_kg(min_price=Decimal("1000.01")), harga_dasar={P: Decimal("1"), P2: Decimal("15000")}, konversi=_konv_kg("0.5"))
    _, b = await _satu(kon, C_WARUNG, P2, "Dus", 2)  # 2 Dus x 0.5 = 1 kg dasar -> tingkat 1 terpenuhi (qty dasar < 1 tak memenuhi tingkat)
    assert b["sumber"] == "daftar_konversi" and b["harga_minimum"] == 500.01  # 1000.01 x 0.5 = 500.005 -> _r2 HALF_UP


# --- BUG-338A: tingkat KATEGORI pelanggan (pelanggan > kategori > bawaan) ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_338a_pelanggan_tanpa_daftar_jatuh_ke_daftar_kategori_asal_kategori():
    k = Kon(); k.kategori = {C_POLOS: WARUNG}
    pl, b = await _satu(k, C_POLOS, P, "Dus", 10)
    assert pl == {"id": str(WARUNG), "code": "WARUNG", "name": "Warung", "asal": "kategori", "kategori_name": "Warung Besar"}
    assert (b["harga"], b["sumber"], b["sumber_text"]) == (95000.0, "daftar", "Kategori pelanggan Warung Besar")


@pytest.mark.asyncio
async def test_338a_daftar_pelanggan_menang_atas_kategori():
    k = Kon(); k.kategori = {C_WARUNG: ECERAN}
    pl, _ = await _satu(k, C_WARUNG, P, "Dus", 1)
    assert pl["asal"] == "pelanggan" and pl["code"] == "WARUNG"


@pytest.mark.asyncio
async def test_338a_daftar_pelanggan_nonaktif_jatuh_ke_kategori_bukan_bawaan():
    k = Kon(); k.kategori = {C_LAMA: WARUNG}   # C_LAMA: DH pelanggan LAMA nonaktif
    pl, _ = await _satu(k, C_LAMA, P, "Dus", 1)
    assert pl["asal"] == "kategori" and pl["code"] == "WARUNG"


@pytest.mark.asyncio
async def test_338a_daftar_kategori_nonaktif_atau_kedaluwarsa_jatuh_ke_bawaan():
    k = Kon(); k.kategori = {C_POLOS: LAMA}   # LAMA nonaktif
    pl, _ = await _satu(k, C_POLOS, P, None, 1)
    assert pl["asal"] == "bawaan" and pl["code"] == "ECERAN"
    kd = Kon(daftar=[_daftar(WARUNG, "WARUNG", akhir=date(2026, 1, 1)), _daftar(ECERAN, "ECERAN", bawaan=True)]); kd.kategori = {C_POLOS: WARUNG}
    pl2, _ = await _satu(kd, C_POLOS, P, None, 1)
    assert pl2["asal"] == "bawaan"


@pytest.mark.asyncio
async def test_338a_tanpa_kategori_atau_tanpa_pelanggan_perilaku_lama():
    for c in (C_POLOS, None):
        pl, _ = await _satu(Kon(), c, P, None, 1)
        assert pl["asal"] == "bawaan"


@pytest.mark.asyncio
async def test_338a_kueri_kategori_bersaring_tenant_dan_hanya_kategori_aktif():
    tanya = []

    class Spy(Kon):
        async def fetchrow(self, sql, *a):
            if "customer_categories" in sql:
                tanya.append((" ".join(sql.split()), a))
            return await super().fetchrow(sql, *a)
    await _satu(Spy(), C_POLOS, P, None, 1)
    (q, a), = tanya
    assert a == (C_POLOS, T) and "k.tenant_id = c.tenant_id" in q and "c.tenant_id = $2" in q and "k.is_active" in q


def test_338a_guard_minimum_memakai_penentu_daftar_yang_sama():
    src = (APP / "services" / "harga_minimum.py").read_text()
    assert "HJ.daftar_untuk(conn, tid, customer_id, tanggal)" in src


@pytest.mark.asyncio
async def test_338a_teks_server_kategori_hanya_untuk_sumber_daftar_pelanggan_dan_bawaan_tak_berubah():
    k = Kon(); k.kategori = {C_POLOS: WARUNG}
    pl, b = await _satu(k, C_POLOS, P, "Karton", 1)   # satuan turunan tanpa baris DH sendiri -> daftar_konversi
    assert b["sumber"] == "daftar_konversi" and b["sumber_text"] == "Kategori pelanggan Warung Besar (dari satuan dasar)"
    pl2, b2 = await _satu(k, C_WARUNG, P, "Dus", 1)   # DH pelanggan sendiri
    assert pl2["asal"] == "pelanggan" and pl2["kategori_name"] is None and b2["sumber_text"] == "Daftar harga"
    pl3, b3 = await _satu(Kon(), C_POLOS, P, None, 1)   # bawaan
    assert pl3["asal"] == "bawaan" and pl3["kategori_name"] is None and b3["sumber_text"] == "Daftar harga"
