"""#39 -- tarif PPN baris PENAWARAN diturunkan SERVER dari kode pajak (lanjutan #34 SO).

Dulu create/PATCH Penawaran menghitung pajak dari `tax_rate` kiriman klien: PPN-11 + tax_rate 0
-> pajak 0; tax_rate 7 tanpa kode -> 7% karangan. Kini `_quote_calc` (satu jalan hitung create,
PATCH, konversi) memanggil tax_factor.turunkan_tarif_baris.

Handler dipanggil LANGSUNG di atas DB palsu #34 (menjawab SQL sesuai semantik) -- helper benar
+ handler tak memanggilnya = merah di sini.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.routers import quotes as Q
from app.schemas.quotes import CreateQuoteRequest, UpdateQuoteRequest

from .test_t34_pajak_so import DB, Pool, TENANT, CUST, PPN11, PPN12, KODE_ASING, _req

QUOTE_ID = "55555555-5555-5555-5555-555555555555"


class DBQ(DB):
    def __init__(self, is_pkp=True, baris_tersimpan=None):
        super().__init__(is_pkp)
        self.baris_tersimpan = baris_tersimpan or []

    async def fetchval(self, sql, *a):
        s = " ".join(sql.split())
        if "generate_quote_number" in s:
            return "QUO-UJI-0001"
        if s.startswith("SELECT 1 FROM quotes"):
            return None
        return await super().fetchval(sql, *a)

    async def fetchrow(self, sql, *a):
        s = " ".join(sql.split())
        if s.startswith("SELECT id, status FROM quotes"):
            return {"id": a[0], "status": "draft"}
        if s.startswith("SELECT discount_type, discount_value FROM quotes"):
            return {"discount_type": "fixed", "discount_value": 0}
        if s.startswith("SELECT total_amount FROM quotes"):
            return {"total_amount": 0}
        return await super().fetchrow(sql, *a)

    async def fetch(self, sql, *a):
        s = " ".join(sql.split())
        if s.startswith("SELECT * FROM quote_items WHERE quote_id"):
            return self.baris_tersimpan
        return await super().fetch(sql, *a)

    def baris_ditulis(self):
        """(tax_rate, tax_amount) tiap INSERT quote_items."""
        return [(a[9], a[10]) for (s, a) in self.tulis if s.startswith("INSERT INTO quote_items")]

    def header_pajak(self):
        return [a[14] for (s, a) in self.tulis if s.startswith("INSERT INTO quotes")]


@pytest.fixture
def pasang(monkeypatch):
    def _p(db):
        async def _pool():
            return Pool(db)
        monkeypatch.setattr(Q, "get_pool", _pool)
        return db
    return _p


def _baris(**kw):
    b = {"description": "Kaos", "quantity": 1, "unit_price": 100000}
    b.update(kw)
    return b


def _buat(*baris):
    return CreateQuoteRequest(
        quote_date=date(2026, 9, 25), customer_id=CUST, customer_name="Pelanggan",
        items=list(baris),
    )


@pytest.mark.asyncio
async def test_create_kode_ppn11_tarif_klien_nol_jadi_11(pasang):
    db = pasang(DBQ())
    await Q.create_quote(_req(), _buat(_baris(tax_id=PPN11, tax_rate=0)))
    assert db.baris_ditulis() == [(Decimal("11"), Decimal("11000.00"))]
    assert db.header_pajak() == [Decimal("11000.00")]


@pytest.mark.asyncio
async def test_create_kode_ppn11_tarif_klien_12_diabaikan(pasang):
    db = pasang(DBQ())
    await Q.create_quote(_req(), _buat(_baris(tax_id=PPN11, tax_rate=12)))
    assert db.baris_ditulis() == [(Decimal("11"), Decimal("11000.00"))]


@pytest.mark.asyncio
async def test_create_kode_ppn12_faktor_11_12_tetap(pasang):
    db = pasang(DBQ())
    await Q.create_quote(_req(), _buat(_baris(tax_id=PPN12, tax_rate=0)))
    # DPP nilai lain 11/12 x 100.000 x 12% = 11.000
    assert db.baris_ditulis() == [(Decimal("12"), Decimal("11000.00"))]


@pytest.mark.asyncio
@pytest.mark.parametrize("kode", [KODE_ASING, "bukan-uuid"])
async def test_create_kode_asing_atau_rusak_400_tanpa_tulis(pasang, kode):
    db = pasang(DBQ())
    with pytest.raises(HTTPException) as e:
        await Q.create_quote(_req(), _buat(_baris(tax_id=kode, tax_rate=11)))
    assert e.value.status_code == 400
    assert db.baris_ditulis() == [] and db.header_pajak() == []


@pytest.mark.asyncio
async def test_create_tarif_karangan_tanpa_kode_400(pasang):
    db = pasang(DBQ())
    with pytest.raises(HTTPException) as e:
        await Q.create_quote(_req(), _buat(_baris(tax_rate=7)))
    assert e.value.status_code == 400
    assert db.header_pajak() == []


@pytest.mark.asyncio
async def test_create_tarif_tanpa_kode_dimiliki_kode_aktif_jalan(pasang):
    db = pasang(DBQ())
    await Q.create_quote(_req(), _buat(_baris(tax_rate=11)))
    assert db.baris_ditulis() == [(Decimal("11"), Decimal("11000.00"))]


@pytest.mark.asyncio
async def test_create_tanpa_pajak_jalan(pasang):
    db = pasang(DBQ())
    await Q.create_quote(_req(), _buat(_baris()))
    assert db.baris_ditulis() == [(Decimal("0"), Decimal("0.00"))]


@pytest.mark.asyncio
async def test_patch_baris_tarif_dari_kode(pasang):
    db = pasang(DBQ())
    await Q.update_quote(_req(), QUOTE_ID, UpdateQuoteRequest(items=[_baris(tax_id=PPN11, tax_rate=0)]))
    assert db.baris_ditulis() == [(Decimal("11"), Decimal("11000.00"))]


@pytest.mark.asyncio
async def test_patch_diskon_hitung_ulang_baris_tersimpan_tarif_dari_kode(pasang):
    tersimpan = [{
        "item_id": None, "description": "Kaos", "quantity": Decimal("1"), "unit": None,
        "unit_price": 100000, "discount_percent": 0, "tax_id": uuid.UUID(PPN11),
        "tax_rate": Decimal("0"), "group_name": None, "sort_order": 0,
    }]
    db = pasang(DBQ(baris_tersimpan=tersimpan))
    await Q.update_quote(_req(), QUOTE_ID, UpdateQuoteRequest(discount_value=0))
    assert db.baris_ditulis() == [(Decimal("11"), Decimal("11000.00"))]


@pytest.mark.asyncio
async def test_konversi_menurunkan_tarif_baris_tersimpan():
    db = DBQ()
    quote = {"id": QUOTE_ID, "discount_type": "fixed", "discount_value": 0}
    rows = [{
        "item_id": None, "description": "Kaos", "quantity": Decimal("2"), "unit": None,
        "unit_price": 50000, "discount_percent": 0, "tax_id": uuid.UUID(PPN11),
        "tax_rate": Decimal("0"), "group_name": None, "sort_order": 0,
    }]
    doc = await Q._quote_converted_doc(db, TENANT, quote, rows)
    assert doc["tax_amount"] == Decimal("11000.00")


# --- non-PKP: Penawaran ber-PPN ditolak 422 (perluasan putusan pemilik #34 SO) -------------

from app.services.pkp_guard import PESAN_NON_PKP  # noqa: E402


@pytest.mark.asyncio
async def test_nonpkp_create_ber_ppn_422_tanpa_tulis(pasang):
    db = pasang(DBQ(is_pkp=False))
    with pytest.raises(HTTPException) as e:
        await Q.create_quote(_req(), _buat(_baris(tax_id=PPN11)))
    assert (e.value.status_code, e.value.detail) == (422, PESAN_NON_PKP)
    assert db.header_pajak() == [] and db.baris_ditulis() == []


@pytest.mark.asyncio
async def test_nonpkp_create_tanpa_pajak_jalan(pasang):
    db = pasang(DBQ(is_pkp=False))
    await Q.create_quote(_req(), _buat(_baris()))
    assert db.header_pajak() == [Decimal("0.00")]


@pytest.mark.asyncio
async def test_nonpkp_patch_baris_ber_ppn_422(pasang):
    db = pasang(DBQ(is_pkp=False))
    with pytest.raises(HTTPException) as e:
        await Q.update_quote(_req(), QUOTE_ID, UpdateQuoteRequest(items=[_baris(tax_id=PPN11)]))
    assert (e.value.status_code, e.value.detail) == (422, PESAN_NON_PKP)
    assert db.baris_ditulis() == []


@pytest.mark.asyncio
async def test_pkp_create_ber_ppn_jalan(pasang):
    db = pasang(DBQ(is_pkp=True))
    await Q.create_quote(_req(), _buat(_baris(tax_id=PPN11)))
    assert db.header_pajak() == [Decimal("11000.00")]
