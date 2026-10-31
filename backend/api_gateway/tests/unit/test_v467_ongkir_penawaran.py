"""V467 -- ongkos kirim di Penawaran (paket Milkybook v2, 31 Okt 2026), pola V290 SO/faktur.

Kode pajak ongkir EKSPLISIT; None = ikut kode pajak barang (BUKAN tanpa pajak); tanpa PPN = kode NONE tenant.
Contoh acuan paket (golden Q04): sub 7.550.000, diskon 5%, PPN 12% DPP 11/12, ongkir 50.000 tanpa PPN -> 8.011.475.
Konversi ke SO/faktur membawa ongkir + pilihan kode APA ADANYA (NONE tetap NONE). Ongkir 0 = hasil identik pra-V467.
DB palsu #34/#39 (menjawab SQL sesuai semantik)."""
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.routers import quotes as Q
from app.schemas.quotes import CreateQuoteRequest, UpdateQuoteRequest

from .test_t34_pajak_so import TENANT, CUST, PPN12, _req
from .test_t39_tarif_penawaran import DBQ, QUOTE_ID, pasang  # noqa: F401 (fixture)

NONE = "aaaaaaaa-0000-0000-0000-000000000000"  # kode NONE tenant (tarif 0)


class DBO(DBQ):
    def __init__(self, tersimpan_kepala=None, **kw):
        super().__init__(**kw)
        self.kode[NONE] = (TENANT, Decimal("0"), 1, 1, True)
        self.kepala = tersimpan_kepala or {}

    async def fetchrow(self, sql, *a):
        s = " ".join(sql.split())
        if s.startswith("SELECT * FROM quotes WHERE id"):
            return {"id": a[0], "status": "draft", "total_amount": 0, **self.kepala}
        return await super().fetchrow(sql, *a)

    def ongkir_ditulis(self, tabel="quotes"):
        # DB palsu #34 menyimpan 40 karakter pertama SQL ("UPDATE sales_invoices SET shipping_amoun")
        t = f"UPDATE {tabel} SET shipping_amount"
        return [a for (s, a) in self.tulis if s.startswith(t) or (len(s) >= 30 and t.startswith(s))]


def _baris(qty, harga):
    return {"description": "x", "quantity": qty, "unit_price": harga, "tax_id": PPN12, "tax_rate": 0}


Q04 = [_baris(48, 80000), _baris(6, 175000), _baris(4, 185000), _baris(24, 80000)]  # sub 7.550.000


def _buat(**kw):
    return CreateQuoteRequest(quote_date=date(2026, 10, 31), customer_id=CUST, customer_name="Pelanggan",
                              discount_type="percentage", discount_value=5, items=Q04, **kw)


@pytest.mark.asyncio
@pytest.mark.parametrize("ongkir,kode,total,pajak,pajak_ongkir", [
    (50000, NONE, Decimal("8011475.00"), Decimal("788975.00"), Decimal("0")),       # Q04: ongkir tanpa PPN
    (50000, None, Decimal("8016975.00"), Decimal("794475.00"), Decimal("5500.00")),  # None = ikut barang (11/12 x 12%)
    (0, None, Decimal("7961475.00"), Decimal("788975.00"), Decimal("0")),           # tanpa ongkir = pra-V467
])
async def test_hitung_q04(ongkir, kode, total, pajak, pajak_ongkir):
    doc = await Q._quote_calc(DBO(), TENANT, [dict(b) for b in Q04], "percentage", 5,
                              shipping_amount=ongkir, shipping_tax_code_id=kode)
    assert (doc["total_amount"], doc["tax_amount"], doc["shipping_tax_amount"]) == (total, pajak, pajak_ongkir)
    assert doc["doc_discount"] == Decimal("377500.00")  # ongkir tak menerima alokasi diskon dokumen


@pytest.mark.asyncio
async def test_tanpa_argumen_ongkir_identik_dengan_ongkir_nol():
    a = await Q._quote_calc(DBO(), TENANT, [dict(b) for b in Q04], "percentage", 5)
    b = await Q._quote_calc(DBO(), TENANT, [dict(b) for b in Q04], "percentage", 5, shipping_amount=0)
    assert a["total_amount"] == b["total_amount"] == Decimal("7961475.00")


@pytest.mark.asyncio
async def test_create_q04_menulis_ongkir_dan_kode_none(pasang):
    db = pasang(DBO())
    await Q.create_quote(_req(), _buat(shipping_amount=50000, shipping_tax_code_id=NONE))
    total = [a[15] for (s, a) in db.tulis if s.startswith("INSERT INTO quotes")]
    assert total == [Decimal("8011475.00")]
    (o,) = db.ongkir_ditulis()
    assert (o[1], str(o[2]), o[4]) == (Decimal("50000.00"), NONE, Decimal("0"))


@pytest.mark.asyncio
async def test_create_tanpa_ongkir_tak_menyentuh_kolom_baru(pasang):
    db = pasang(DBO())
    await Q.create_quote(_req(), _buat())
    assert db.ongkir_ditulis() == []


@pytest.mark.asyncio
async def test_calculate_mengembalikan_ongkir_eksplisit_dan_efektif(pasang):
    pasang(DBO())
    r = await Q.calculate_quote(_req(), _buat(shipping_amount=50000, shipping_tax_code_id=NONE))
    d = r["data"]
    assert (d["total_amount"], d["shipping_amount"], d["shipping_tax_amount"]) == (8011475.0, 50000.0, 0.0)
    assert d["shipping_tax_code_id"] == NONE and d["shipping_tax_code_id_effective"] == NONE
    r2 = await Q.calculate_quote(_req(), _buat(shipping_amount=50000))
    assert r2["data"]["shipping_tax_code_id"] is None and r2["data"]["shipping_tax_code_id_effective"] == PPN12


def _rows():
    return [{"item_id": None, "description": "x", "quantity": Decimal(b["quantity"]), "unit": None,
             "unit_price": b["unit_price"], "discount_percent": 0, "tax_id": uuid.UUID(PPN12),
             "tax_rate": Decimal("12"), "group_name": None, "sort_order": i} for i, b in enumerate(Q04)]


@pytest.mark.asyncio
async def test_konversi_membawa_ongkir_dan_kode_none_apa_adanya():
    db = DBO()
    quote = {"id": QUOTE_ID, "discount_type": "percentage", "discount_value": 5,
             "shipping_amount": Decimal("50000"), "shipping_tax_code_id": uuid.UUID(NONE)}
    doc = await Q._quote_converted_doc(db, TENANT, quote, _rows())
    assert (doc["total_amount"], doc["shipping_tax_amount"]) == (Decimal("8011475.00"), Decimal("0"))
    so_id = uuid.uuid4()
    await Q._salin_ongkir_konversi(db, "sales_orders", so_id, doc, quote)
    (o,) = db.ongkir_ditulis("sales_orders")
    assert (o[0], o[1], str(o[2]), o[4]) == (so_id, Decimal("50000.00"), NONE, Decimal("0"))  # NONE, bukan null


@pytest.mark.asyncio
async def test_konversi_kode_null_tetap_null_ikut_barang():
    db = DBO()
    quote = {"id": QUOTE_ID, "discount_type": "percentage", "discount_value": 5,
             "shipping_amount": Decimal("50000"), "shipping_tax_code_id": None}
    doc = await Q._quote_converted_doc(db, TENANT, quote, _rows())
    await Q._salin_ongkir_konversi(db, "sales_invoices", uuid.uuid4(), doc, quote)
    (o,) = db.ongkir_ditulis("sales_invoices")
    assert o[2] is None and o[4] == Decimal("5500.00")


@pytest.mark.asyncio
async def test_konversi_tanpa_ongkir_tak_menulis_apa_pun():
    db = DBO()
    quote = {"id": QUOTE_ID, "discount_type": "fixed", "discount_value": 0}  # baris pra-V467: kolom tak ada
    doc = await Q._quote_converted_doc(db, TENANT, quote, _rows())
    await Q._salin_ongkir_konversi(db, "sales_orders", uuid.uuid4(), doc, quote)
    assert db.ongkir_ditulis("sales_orders") == []
    with pytest.raises(KeyError):
        await Q._salin_ongkir_konversi(db, "quotes; DROP", uuid.uuid4(), doc,
                                       {"shipping_amount": 1, "shipping_tax_code_id": None})


@pytest.mark.asyncio
async def test_patch_ongkir_saja_menghitung_ulang_dan_menulis(pasang):
    db = pasang(DBO(baris_tersimpan=_rows()))
    await Q.update_quote(_req(), QUOTE_ID, UpdateQuoteRequest(shipping_amount=50000, shipping_tax_code_id=NONE))
    (o,) = db.ongkir_ditulis()
    assert (o[1], str(o[2])) == (Decimal("50000.00"), NONE)


@pytest.mark.asyncio
async def test_patch_diskon_mempertahankan_ongkir_tersimpan(pasang):
    db = pasang(DBO(baris_tersimpan=_rows(),
                    tersimpan_kepala={"shipping_amount": Decimal("50000"), "shipping_tax_code_id": uuid.UUID(NONE)}))
    await Q.update_quote(_req(), QUOTE_ID, UpdateQuoteRequest(discount_type="percentage", discount_value=5))
    (o,) = db.ongkir_ditulis()
    assert (o[1], str(o[2]), o[4]) == (Decimal("50000.00"), NONE, Decimal("0"))


@pytest.mark.asyncio
async def test_patch_hapus_ongkir_menulis_nol(pasang):
    db = pasang(DBO(baris_tersimpan=_rows(),
                    tersimpan_kepala={"shipping_amount": Decimal("50000"), "shipping_tax_code_id": uuid.UUID(NONE)}))
    await Q.update_quote(_req(), QUOTE_ID, UpdateQuoteRequest(shipping_amount=0, shipping_tax_code_id=None))
    (o,) = db.ongkir_ditulis()
    assert (o[1], o[2]) == (Decimal("0.00"), None)


def test_ongkir_tersimpan_aman_sebelum_kolom_ada():
    assert Q._ongkir_tersimpan({"id": 1}) == (Decimal("0"), None)
    assert Q._ongkir_tersimpan({"shipping_amount": Decimal("5"), "shipping_tax_code_id": uuid.UUID(NONE)}) == \
        (Decimal("5"), NONE)


def test_migrasi_v467_aditif_dan_pembalik_gagal_keras():
    import os
    m = os.path.join(os.path.dirname(__file__), "..", "..", "..", "migrations")
    up = open(os.path.join(m, "V467__ongkir_penawaran.sql")).read()
    down = open(os.path.join(m, "V467__ongkir_penawaran_ROLLBACK.sql")).read()
    assert up.count("ADD COLUMN IF NOT EXISTS") == 5 and "DEFAULT 0" in up and "UPDATE" not in up
    assert "RAISE EXCEPTION" in down
