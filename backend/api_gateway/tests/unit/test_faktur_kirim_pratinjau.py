"""BUG-430/436: POST /sales-invoices/{id}/fulfill/preview + batch/serial di SO-fulfill (extra=forbid)."""
import asyncio
import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.middleware import permission_middleware as PM
from app.routers import sales_invoices as SI
from app.services import faktur_kirim_pratinjau as FP
from app.services import so_pengiriman as SP

INV, ITEM, PROD, WH = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


class _Tx:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _Conn:
    def __init__(self, qty="10", terkirim="0"):
        self.qty, self.terkirim = qty, terkirim
        self.stok = Decimal("20")

    def transaction(self): return _Tx()

    async def fetchrow(self, sql, *a):
        if "FROM sales_invoices" in sql:
            return {"id": INV, "invoice_number": "INV-1", "customer_name": "PT X", "total_amount": 1, "invoice_date": date(2026, 11, 28),
                    "status": "posted", "fulfillment_status": "unfulfilled", "warehouse_id": None}
        return None

    async def fetchval(self, sql, *a):
        if "warehouse_stock" in sql: return self.stok
        if "FROM warehouses" in sql: return "Gudang Utama"
        if "SELECT fulfillment_status FROM sales_invoices" in sql: return "partial"
        return None

    async def fetch(self, sql, *a):
        if "FROM sales_invoice_items" in sql:
            return [{"id": ITEM, "item_id": PROD, "description": "Kaos Polos", "unit": "Dus", "quantity": Decimal(self.qty),
                     "fulfilled_qty": Decimal(self.terkirim)}]
        return []


async def _ok(conn, *a, **k):
    conn.stok -= Decimal("6")
    return {"fulfillment_id": str(uuid.uuid4()), "total_cogs": "840000.00", "total_revenue": "1000000.00"}


async def _tolak(conn, *a, **k):
    raise HTTPException(409, "Sisa Kaos Polos hanya 4, diminta 6.")


async def _noop(*a, **k): return None


def _jalan(execute, conn=None, body=None):
    conn = conn or _Conn()
    b = body or {"warehouse_id": str(WH), "fulfillment_date": "2026-11-28",
                 "items": [{"invoice_item_id": str(ITEM), "quantity": "6"}]}
    return asyncio.run(FP.pratinjau_kirim_faktur(conn, {"tenant_id": "t1", "user_id": None}, INV, b, execute, lambda i: None, _noop))


def test_sukses_baris_sisa_teks_dan_totals():
    d = _jalan(_ok)
    assert d["can_fulfill"] and not d["blocks"] and d["totals"]["cogs_text"] == "Rp 840.000"
    ln = d["lines"][0]
    assert ln["remaining_before"] == "10" and ln["remaining_after"] == "4" and ln["shipped_before"] == "0"
    assert ln["summary_text"] == ln["detail_text"] == "Kaos Polos — 6 Dus dari 10 · sisa 4"
    assert ln["stock_before_text"] == "20" and ln["stock_after_text"] == "14"
    assert d["warnings"][0]["code"] == "MASIH_SISA"


def test_lunas_terkirim_teks():
    d = _jalan(_ok, _Conn(qty="6"))
    assert d["lines"][0]["summary_text"].endswith("· lunas terkirim") and not d["warnings"]


def test_galat_inti_jadi_blok_200_tanpa_totals():
    d = _jalan(_tolak)
    assert not d["can_fulfill"] and d["blocks"][0]["code"] == "SISA_KURANG" and d["totals"] is None and d["journal_text"] is None
    assert d["lines"][0]["quantity"] == "6"


def test_items_kosong_dan_baris_asing_dan_faktur_tak_ada():
    d = _jalan(_ok, body={"warehouse_id": str(WH), "items": []})
    assert d["blocks"][0]["code"] == "ITEMS_KOSONG"
    d = _jalan(_ok, body={"items": [{"invoice_item_id": str(uuid.uuid4()), "quantity": "1"}]})
    assert any(b["code"] == "BARIS_BUKAN_MILIK_FAKTUR" for b in d["blocks"])

    class _Kosong(_Conn):
        async def fetchrow(self, sql, *a): return None
    with pytest.raises(HTTPException) as e:
        _jalan(_ok, _Kosong())
    assert e.value.status_code == 404


@pytest.mark.parametrize("status,pesan,kode", [
    (400, "Gudang wajib dipilih.", "GUDANG_WAJIB"), (409, "Stok Kaos tidak cukup", "STOK_KURANG"),
    (400, "Hanya faktur terbit (belum lunas) yang bisa dikirim.", "FAKTUR_TAK_BISA_DIKIRIM"), (409, "lain-lain", "KIRIM_DITOLAK")])
def test_kode_blok(status, pesan, kode):
    assert FP.kode_blok(HTTPException(status, pesan)) == kode


def test_kode_blok_dari_detail_dict():
    assert FP.kode_blok(HTTPException(409, {"code": "FULFILL_LINE_FULLY_CREDITED", "message": "x"})) == "FULFILL_LINE_FULLY_CREDITED"


def test_so_fulfill_extra_forbid_dan_batch_serial():
    L = SP.SOFulfillLine(sales_order_item_id="a", quantity=1, batch_id="b", serials=["S1"])
    assert L.batch_id == "b" and L.serials == ["S1"]
    with pytest.raises(ValidationError):
        SP.SOFulfillLine(sales_order_item_id="a", quantity=1, sembarang=1)
    with pytest.raises(ValidationError):
        SP.SOFulfillRequest(sembarang=1)
    assert SP.SOFulfillRequest().items is None


def test_rute_dan_izin_preview_faktur():
    f = PM.PermissionMiddleware(lambda *a: None, False)._find_permission
    assert f("/api/sales-invoices/X/fulfill/preview", "POST") == f("/api/sales-invoices/X/fulfill", "POST") == ("sales_invoice", "P")
    assert any(getattr(r, "path", "") == "/{invoice_id}/fulfill/preview" for r in SI.router.routes)


def test_bug443_diblok_kosongkan_sesudah_tapi_medan_mentah_tetap():
    d = _jalan(_tolak)
    ln = d["lines"][0]
    for f in ("remaining_after", "remaining_after_text", "summary_text", "detail_text", "stock_after_text", "batches", "serials", "cogs_text"):
        assert ln[f] is None, f
    assert (ln["item_name"], ln["quantity"], ln["unit"], ln["ordered"], ln["shipped_before"], ln["remaining_before"]) == ("Kaos Polos", "6", "Dus", "10", "0", "10")
    assert ln["stock_before_text"] == "20"
    assert d["ok"] is False and d["can_fulfill"] is False and d["totals"] is None and d["journal_text"] is None
    iv = d["invoice"]
    assert iv["fulfillment_status_after"] is None and iv["fulfillment_status_after_text"] is None and iv["fulfillment_status_before_text"] == "Belum dikirim"


def test_bug443_sukses_bentuk_lama_plus_status_teks():
    d = _jalan(_ok)
    assert d["ok"] is True and d["lines"][0]["remaining_after"] == "4" and isinstance(d["lines"][0]["batches"], list)
    iv = d["invoice"]
    assert iv["fulfillment_status_after"] == "partial" and iv["fulfillment_status_after_text"] == "Sebagian"
