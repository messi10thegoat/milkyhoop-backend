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
    def __init__(self, daftar=None, baris=None, harga_dasar=None, konversi=None):
        self.daftar = daftar if daftar is not None else [_daftar(WARUNG, "WARUNG"), _daftar(ECERAN, "ECERAN", bawaan=True),
                                                          _daftar(LAMA, "LAMA", aktif=False)]
        self.pelanggan = {C_WARUNG: WARUNG, C_POLOS: None, C_LAMA: LAMA}
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

    async def fetchval(self, sql, *a):
        q = " ".join(sql.split())
        if "FROM customers" in q and "price_list_id" in q:
            return self.pelanggan.get(a[0])
        raise AssertionError(q[:90])

    async def fetchrow(self, sql, *a):
        q = " ".join(sql.split())
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
        if "FROM products" in q and "sales_price" in q:
            return [{"id": k, "sales_price": v} for k, v in self.harga_dasar.items()]
        if "FROM unit_conversions" in q and "conversion_factor" in q:
            return [k for k in self.konversi]
        if "FROM unit_conversions" in q and "sales_price" in q:
            return [k for k in self.konversi if k["sales_price"] is not None]
        if "FROM price_list_items" in q:
            return [{**b, "start_date": None, "end_date": None} for b in self.baris if b["pl"] == a[0]]
        raise AssertionError(q[:90])


async def _satu(kon, pelanggan, item, unit, qty, tgl=HARI):
    r = await HJ.harga_saran(kon, T, pelanggan, tgl, [{"key": "k1", "item_id": item, "unit": unit, "quantity": qty}])
    return r["price_list"], r["lines"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("qty,harga,tingkat", [(1, 100000.0, 1.0), (9, 100000.0, 1.0), (10, 95000.0, 10.0), (49, 95000.0, 10.0),
                                               (50, 90000.46, 50.0)])
async def test_daftar_pelanggan_tingkat_qty_tertinggi_dan_dibulatkan_2dp(qty, harga, tingkat):
    pl, b = await _satu(Kon(), C_WARUNG, P, "Dus", qty)
    assert pl == {"id": str(WARUNG), "code": "WARUNG", "name": "Warung", "asal": "pelanggan"}
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
async def test_satuan_lain_jatuh_ke_harga_satuan_lalu_harga_dasar_kali_faktor():
    _, b = await _satu(Kon(), C_WARUNG, P, "Pak", 1)  # daftar hanya Dus -> konversi Pak punya harga sendiri
    assert (b["harga"], b["sumber"], b["faktor"]) == (600000.0, "harga_satuan", 6.0)
    _, b2 = await _satu(Kon(), C_WARUNG, P, "Karton", 1)  # Karton tanpa harga konversi -> 105.000 x 12
    assert (b2["harga"], b2["sumber"], b2["faktor"]) == (1260000.0, "harga_dasar", 12.0)


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
    assert [(x["key"], x["harga"], x["sumber"]) for x in r["lines"]] == [("a", 95000.0, "daftar"), ("b", 1260000.0, "harga_dasar")]


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
    assert "SD.qty_dasar" in t and "UniqueViolationError" in t and "FROM products WHERE id = $1 AND tenant_id = $2" in t


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
    assert "Literal['sales_order', 'quote', 'sales_invoice']" in ast.unparse(a.args)


def test_jalur_harga_lama_dihapus_bukan_dibiarkan():
    # /get-price (nol pemanggil FE/BE/chat) + tabel customer_price_lists (0 baris; digantikan customers.price_list_id) dihapus
    src = {rel: (APP / rel).read_text() for rel in ("routers/price_lists.py", "routers/customers.py", "schemas/price_lists.py")}
    assert all("/get-price" not in t and "ItemPrice" not in t and "customer_price_lists" not in t for t in src.values())


def test_detail_pelanggan_memuat_daftar_harganya():
    g = ast.unparse(_fungsi("routers/customers.py", "get_customer"))
    assert "price_list_id" in g and "price_list_name" in g
    from app.schemas.customers import CustomerDetail
    assert {"price_list_id", "price_list_name"} <= set(CustomerDetail.model_fields)
