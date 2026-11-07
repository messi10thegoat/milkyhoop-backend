"""BUG-046 penjualan tunai satu langkah: POST /sales-invoices + auto_post + bayar_tunai.

Faktur terbit (_internal_post_invoice) lalu lunas lewat inti penerimaan yang SAMA (receive_payments.buat_penerimaan)
di transaksi create yang SAMA; jumlah = sisa compute_ar_outstanding; rekening = baris bank_accounts tenant & aktif.
"""
import inspect
import os
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.routers import sales_invoices as SI  # noqa: E402
from app.routers import receive_payments as RP  # noqa: E402
from app.schemas.sales_invoices import CreateInvoiceRequest  # noqa: E402

T = "kaos-biru-konveksi"
INV = UUID("80000000-0000-0000-0000-000000000046")
CUST = "40000000-0000-0000-0000-0000000000c1"
BA = UUID("70000000-0000-0000-0000-0000000000b1")
COA = UUID("70000000-0000-0000-0000-0000000000c0")
RPID = UUID("90000000-0000-0000-0000-000000000046")
JID = UUID("91000000-0000-0000-0000-000000000046")


class _C:
    def __init__(self, rekening=True):
        self.rekening = rekening
        self.kunci = []

    async def execute(self, q, *a):
        if "pg_advisory_xact_lock" in q:
            self.kunci.append(a[0])

    async def fetchrow(self, q, *a):
        if "FROM bank_accounts" in q:
            assert "tenant_id = $2" in q and "is_active" in q
            assert a == (BA, T)
            return {"id": BA, "coa_id": COA, "account_name": "QRIS BCA"} if self.rekening else None
        if "FROM receive_payments" in q:
            return {"journal_id": JID, "journal_number": "JV-RCV-1", "payment_number": "RCV-2026-0046"}
        raise AssertionError(q[:60])


def _badan(**ubah):
    d = dict(customer_id=CUST, customer_name="Pelanggan Umum (Eceran)", invoice_date=date(2026, 11, 7),
             items=[{"item_id": "11111111-1111-1111-1111-111111111111", "description": "Sania", "quantity": 3,
                     "unit_price": 31900}],
             auto_post=True, bayar_tunai={"bank_account_id": str(BA), "reference": "QRIS-8812"})
    d.update(ubah)
    return CreateInvoiceRequest(**d)


@pytest.fixture
def inti(monkeypatch):
    panggil = []

    async def _sisa(conn, tid, iid):
        assert (tid, iid) == (T, INV)
        return Decimal("155900.00")

    async def _buat(conn, ctx, body):
        panggil.append(body)
        return {"data": {"id": str(RPID)}}

    monkeypatch.setattr(RP, "get_invoice_remaining_from_journal", _sisa)
    monkeypatch.setattr(RP, "buat_penerimaan", _buat)
    return panggil


@pytest.mark.asyncio
async def test_lunas_penuh_lewat_inti_penerimaan(inti):
    c = _C()
    hasil = await SI._bayar_tunai_faktur(c, {"tenant_id": T}, INV, _badan())
    assert c.kunci == [f"INVOICE_PAYMENT:{INV}"]
    (b,) = inti
    assert b.total_amount == Decimal("155900.00")
    assert [(a.invoice_id, a.amount_applied) for a in b.allocations] == [(str(INV), Decimal("155900.00"))]
    assert b.bank_account_id == str(BA) and b.customer_id == CUST and b.save_as_draft is False
    assert b.reference_number == "QRIS-8812" and b.payment_date == date(2026, 11, 7)
    assert hasil == {"id": str(RPID), "payment_number": "RCV-2026-0046", "journal_id": str(JID),
                     "journal_number": "JV-RCV-1", "amount": 155900.0, "bank_account_id": str(BA),
                     "bank_account_name": "QRIS BCA"}


@pytest.mark.asyncio
async def test_rekening_bukan_milik_tenant_ditolak_tanpa_tulis(inti):
    with pytest.raises(HTTPException) as e:
        await SI._bayar_tunai_faktur(_C(rekening=False), {"tenant_id": T}, INV, _badan())
    assert e.value.status_code == 400 and e.value.detail == "Rekening penerimaan tidak ditemukan."
    assert inti == []


@pytest.mark.asyncio
async def test_sisa_nol_ditolak(monkeypatch, inti):
    async def _nol(conn, tid, iid):
        return Decimal("0")
    monkeypatch.setattr(RP, "get_invoice_remaining_from_journal", _nol)
    with pytest.raises(HTTPException):
        await SI._bayar_tunai_faktur(_C(), {"tenant_id": T}, INV, _badan())
    assert inti == []


def test_syarat_satu_tempat():
    bt = {"bank_account_id": str(BA)}
    assert SI._tolak_bayar_tunai(None, False, None, "delivery") is None
    assert SI._tolak_bayar_tunai(bt, True, CUST, None) is None
    assert SI._tolak_bayar_tunai(bt, False, CUST, None) == "Bayar tunai butuh faktur yang langsung diterbitkan."
    assert SI._tolak_bayar_tunai(bt, True, None, None) == "Pilih pelanggan dulu (untuk pembeli eceran: Pelanggan Umum)."
    assert SI._tolak_bayar_tunai(bt, True, CUST, "delivery") == "Penjualan tunai diakui saat faktur, tidak bisa saat dikirim."


def test_sidik_idempotensi_memuat_bayar_tunai():
    assert _badan().model_dump(mode="json")["bayar_tunai"] == {"bank_account_id": str(BA), "reference": "QRIS-8812"}
    assert _badan().model_dump(mode="json") != _badan(bayar_tunai=None).model_dump(mode="json")


def test_create_satu_transaksi_urutan_terbit_lalu_bayar():
    src = inspect.getsource(SI.create_invoice)
    tolak, acq = src.index("_tolak_bayar_tunai("), src.index("pool.acquire()")
    terbit, bayar, simpan = (src.index("await _internal_post_invoice("), src.index("await _bayar_tunai_faktur("),
                             src.index('"pembayaran": pembayaran'))
    assert tolak < acq < terbit < bayar < simpan  # syarat sebelum tulis; bayar sesudah terbit, sebelum amplop idem
    assert src.count("async with conn.transaction()") == 1  # bayar di transaksi create yang SAMA
    assert 'body.recognize_at = "invoice"' in src


def test_pratinjau_baris_payment_dari_rekening_yang_sama():
    src = inspect.getsource(SI.preview_journal)
    assert "_tolak_bayar_tunai(" in src and "_rekening_bayar_tunai(" in src
    assert src.count('"event": "PAYMENT"') == 2 and "AccountRole.AR_TRADE" in src
