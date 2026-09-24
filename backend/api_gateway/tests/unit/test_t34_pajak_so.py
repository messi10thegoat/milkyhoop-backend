"""#34 -- UNIT PAJAK SO (putusan pemilik 25 Sep 2026).

(1) Tenant non-PKP: SO ber-PPN DITOLAK 422 (pesan sama dengan faktur, pkp_guard) di
    create / PATCH-baris / PATCH-diskon-ongkir / calculate / konversi penawaran->SO.
    Tanpa pajak -> jalan. Tenant PKP -> jalan.
(2) Tarif baris DITURUNKAN SERVER dari kode pajak (services/tax_factor.turunkan_tarif_baris):
    PPN-11 + tax_rate 0 dari klien -> 11%; kode asing -> 400; tarif tanpa kode yang tak
    dimiliki kode aktif tenant -> 400.

Handler dipanggil LANGSUNG di atas DB palsu yang menjawab SQL sesuai semantiknya (bukan
helper saja: helper benar + handler tak memanggilnya = tetap hijau).
"""
import ast
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import quotes as Q
from app.routers import sales_orders as SO
from app.schemas.sales_orders import CreateSalesOrderRequest, UpdateSalesOrderRequest
from app.services import tax_factor as TF
from app.services.pkp_guard import PESAN_NON_PKP

TENANT = "tenant-uji"
USER = "22222222-2222-2222-2222-222222222222"
CUST = "33333333-3333-3333-3333-333333333333"
PPN11 = "aaaaaaaa-0000-0000-0000-000000000011"
PPN12 = "aaaaaaaa-0000-0000-0000-000000000012"  # 12% DPP nilai lain 11/12
KODE_ASING = "bbbbbbbb-0000-0000-0000-000000000011"  # milik tenant lain
SO_ID = "44444444-4444-4444-4444-444444444444"


class DB:
    def __init__(self, is_pkp, so_baris=None, so_header=None):
        self.is_pkp = is_pkp
        self.kode = {  # id -> (tenant, rate, num, den, aktif)
            PPN11: (TENANT, Decimal("11"), 1, 1, True),
            PPN12: (TENANT, Decimal("12"), 11, 12, True),
            KODE_ASING: ("tenant-lain", Decimal("11"), 1, 1, True),
        }
        self.tulis = []
        self.so_baris = so_baris or []
        self.so_header = so_header or {"discount_amount": 0, "shipping_amount": 0, "shipping_tax_code_id": None}

    def _kode(self, tcid, tenant):
        k = self.kode.get(str(tcid))
        return k if k and k[0] == tenant else None

    async def fetchval(self, sql, *a):
        s = " ".join(sql.split())
        if s.startswith('SELECT is_pkp FROM "Tenant"'):
            return self.is_pkp
        if s.startswith("SELECT rate FROM tax_codes WHERE id"):
            k = self._kode(a[0], a[1])
            return k[1] if k else None
        if s.startswith("SELECT count(*) FROM tax_codes"):
            return sum(1 for (t, r, *_x, akt) in self.kode.values() if t == a[0] and r == a[2] and akt)
        if "generate_sales_order_number" in s:
            return "SO-UJI-0001"
        if s.startswith("SELECT 1 FROM sales_orders"):
            return None
        raise AssertionError(f"fetchval tak dikenal: {s}")

    async def fetchrow(self, sql, *a):
        s = " ".join(sql.split())
        if s.startswith("SELECT dpp_factor_num, dpp_factor_den FROM tax_codes WHERE id"):
            k = self._kode(a[0], a[1])
            return {"dpp_factor_num": k[2], "dpp_factor_den": k[3]} if k else None
        if s.startswith("SELECT id, rate, dpp_factor_num, dpp_factor_den FROM tax_codes WHERE id"):
            k = self._kode(a[0], a[1])
            return {"id": a[0], "rate": k[1], "dpp_factor_num": k[2], "dpp_factor_den": k[3]} if k else None
        if s.startswith("SELECT nama FROM customers"):
            return {"nama": "Pelanggan"}
        if s.startswith("SELECT id, status FROM sales_orders"):
            return {"id": a[0], "status": "draft"}
        if s.startswith("SELECT discount_amount, shipping_amount, shipping_tax_code_id FROM sales_orders"):
            return self.so_header
        raise AssertionError(f"fetchrow tak dikenal: {s}")

    async def fetch(self, sql, *a):
        s = " ".join(sql.split())
        if s.startswith("SELECT DISTINCT dpp_factor_num, dpp_factor_den FROM tax_codes"):
            fs = {(k[2], k[3]) for k in self.kode.values() if k[0] == a[0] and k[1] == a[2] and k[4]}
            return [{"dpp_factor_num": n, "dpp_factor_den": d} for n, d in fs]
        if s.startswith("SELECT id, quantity, unit_price, discount_percent, tax_rate, tax_id FROM sales_order_items"):
            return self.so_baris
        raise AssertionError(f"fetch tak dikenal: {s}")

    async def execute(self, sql, *a):
        self.tulis.append((" ".join(sql.split())[:40], a))
        return "OK"

    def transaction(self):
        db = self

        class T:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *e):
                return False
        return T()


class Pool:
    def __init__(self, db):
        self.db = db

    def acquire(self):
        db = self.db

        class A:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *e):
                return False
        return A()


def _req():
    return SimpleNamespace(
        state=SimpleNamespace(user={"tenant_id": TENANT, "user_id": USER}),
        headers={},
    )


@pytest.fixture
def pasang(monkeypatch):
    def _p(db):
        async def _pool():
            return Pool(db)
        monkeypatch.setattr(SO, "get_pool", _pool)
        return db
    return _p


def _baris(**kw):
    b = {"description": "Kaos", "quantity": 1, "unit_price": 100000}
    b.update(kw)
    return b


def _buat(*baris, **kw):
    return CreateSalesOrderRequest(
        order_date=date(2026, 9, 25), customer_id=CUST, customer_name="Pelanggan",
        items=list(baris), **kw,
    )


def _baris_tersimpan(db):
    return [a for (sql, a) in db.tulis if sql.startswith("INSERT INTO sales_order_items")]


def _header_tersimpan(db):
    return [a for (sql, a) in db.tulis if sql.startswith("INSERT INTO sales_orders")]


async def _gagal(coro, kode):
    with pytest.raises(HTTPException) as e:
        await coro
    assert e.value.status_code == kode, e.value.detail
    return e.value.detail


# ------------------------------------------------ (1) non-PKP ditolak, di semua pintu
@pytest.mark.asyncio
async def test_nonpkp_create_ber_ppn_422_tanpa_tulis(pasang):
    db = pasang(DB(is_pkp=False))
    d = await _gagal(SO.create_sales_order(_req(), _buat(_baris(tax_id=PPN11, tax_rate=11))), 422)
    assert d == PESAN_NON_PKP
    assert db.tulis == []


@pytest.mark.asyncio
async def test_nonpkp_calculate_ber_ppn_422(pasang):
    pasang(DB(is_pkp=False))
    d = await _gagal(SO.calculate_sales_order(_req(), _buat(_baris(tax_id=PPN11, tax_rate=11))), 422)
    assert d == PESAN_NON_PKP


@pytest.mark.asyncio
async def test_nonpkp_ppn_ongkir_saja_422(pasang):
    # baris tanpa pajak, ONGKIR ber-kode PPN eksplisit -> tetap memungut PPN
    pasang(DB(is_pkp=False))
    await _gagal(SO.calculate_sales_order(
        _req(), _buat(_baris(), shipping_amount=20000, shipping_tax_code_id=PPN11)), 422)


@pytest.mark.asyncio
async def test_nonpkp_tanpa_pajak_jalan(pasang):
    db = pasang(DB(is_pkp=False))
    r = await SO.create_sales_order(_req(), _buat(_baris(), shipping_amount=10000))
    assert r.success
    (h,) = _header_tersimpan(db)
    assert h[13] == Decimal("0")  # tax_amount ($14)
    c = await SO.calculate_sales_order(_req(), _buat(_baris()))
    assert c["data"]["tax_amount"] == 0


@pytest.mark.asyncio
async def test_nonpkp_patch_baris_ber_ppn_422_tanpa_tulis(pasang):
    db = pasang(DB(is_pkp=False))
    body = UpdateSalesOrderRequest(items=[_baris(tax_id=PPN11, tax_rate=11)])
    await _gagal(SO.update_sales_order(_req(), SO_ID, body), 422)
    assert db.tulis == []


@pytest.mark.asyncio
async def test_nonpkp_patch_diskon_atas_baris_tersimpan_ber_ppn_422(pasang):
    # draf lama ber-PPN (sebelum penjaga): suntingan diskon menghitung ulang -> ditolak
    db = pasang(DB(is_pkp=False, so_baris=[{
        "id": uuid.uuid4(), "quantity": 1, "unit_price": 100000, "discount_percent": 0,
        "tax_rate": Decimal("11"), "tax_id": uuid.UUID(PPN11)}]))
    await _gagal(SO.update_sales_order(_req(), SO_ID, UpdateSalesOrderRequest(discount_amount=1000)), 422)
    assert db.tulis == []


@pytest.mark.asyncio
async def test_pkp_ber_ppn_jalan(pasang):
    db = pasang(DB(is_pkp=True))
    r = await SO.create_sales_order(_req(), _buat(_baris(tax_id=PPN11, tax_rate=11)))
    assert r.success
    (h,) = _header_tersimpan(db)
    assert h[13] == Decimal("11000.00")


def test_konversi_penawaran_ke_so_menjaga_sebelum_insert():
    tree = ast.parse(open(Q.__file__, encoding="utf-8").read())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "convert_to_sales_order")
    jaga = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
            and getattr(n.func, "id", None) == "tolak_ppn_bila_non_pkp"]
    ins = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Constant)
           and isinstance(n.value, str) and "INSERT INTO sales_orders" in n.value]
    assert len(jaga) == 1 and ins and jaga[0] < min(ins)


# ------------------------------------------------ (2) tarif dari kode, bukan dari klien
@pytest.mark.asyncio
async def test_kode_ppn11_tarif_klien_nol_jadi_11(pasang):
    db = pasang(DB(is_pkp=True))
    c = await SO.calculate_sales_order(_req(), _buat(_baris(tax_id=PPN11, tax_rate=0)))
    assert c["data"]["items"][0]["tax_rate"] == 11.0
    assert c["data"]["tax_amount"] == 11000.0
    await SO.create_sales_order(_req(), _buat(_baris(tax_id=PPN11, tax_rate=0)))
    (b,) = _baris_tersimpan(db)
    assert b[9] == Decimal("11") and b[10] == Decimal("11000.00")  # tax_rate, tax_amount


@pytest.mark.asyncio
async def test_kode_ppn11_tarif_klien_12_diabaikan(pasang):
    pasang(DB(is_pkp=True))
    c = await SO.calculate_sales_order(_req(), _buat(_baris(tax_id=PPN11, tax_rate=12)))
    assert c["data"]["tax_amount"] == 11000.0


@pytest.mark.asyncio
async def test_kode_ppn12_faktor_11_12_tetap(pasang):
    pasang(DB(is_pkp=True))
    c = await SO.calculate_sales_order(_req(), _buat(_baris(tax_id=PPN12, tax_rate=0)))
    assert c["data"]["tax_amount"] == 11000.0  # 100.000 x 11/12 x 12%


@pytest.mark.asyncio
async def test_patch_baris_tarif_dari_kode(pasang):
    db = pasang(DB(is_pkp=True))
    body = UpdateSalesOrderRequest(items=[_baris(tax_id=PPN11, tax_rate=0)])
    await SO.update_sales_order(_req(), SO_ID, body)
    (b,) = _baris_tersimpan(db)
    assert b[9] == Decimal("11") and b[10] == Decimal("11000.00")


@pytest.mark.asyncio
@pytest.mark.parametrize("kode", [KODE_ASING, "bukan-uuid"])
async def test_kode_asing_atau_rusak_400(pasang, kode):
    db = pasang(DB(is_pkp=True))
    await _gagal(SO.create_sales_order(_req(), _buat(_baris(tax_id=kode, tax_rate=11))), 400)
    assert db.tulis == []


@pytest.mark.asyncio
async def test_tarif_karangan_tanpa_kode_400(pasang):
    pasang(DB(is_pkp=True))
    d = await _gagal(SO.calculate_sales_order(_req(), _buat(_baris(tax_rate=7))), 400)
    assert "7%" in d


@pytest.mark.asyncio
async def test_tarif_tanpa_kode_yang_dimiliki_kode_aktif_jalan(pasang):
    pasang(DB(is_pkp=True))
    c = await SO.calculate_sales_order(_req(), _buat(_baris(tax_rate=11)))
    assert c["data"]["tax_amount"] == 11000.0


@pytest.mark.asyncio
async def test_helper_murni_tarif_nol_tanpa_kode_tanpa_kueri():
    class Mati:
        async def fetchval(self, *a):
            raise AssertionError("tak boleh kueri")
    it = [{"tax_id": None, "tax_rate": 0}]
    await TF.turunkan_tarif_baris(Mati(), TENANT, it, "tax_id")
    assert it[0]["tax_rate"] == Decimal("0")
