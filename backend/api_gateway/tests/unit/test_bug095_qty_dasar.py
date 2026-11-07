"""BUG-095: qty dalam SATUAN DASAR per baris (+ total per satuan dasar) di pratinjau to-invoice SO dan detail SO -- server
mengonversi, FE menulis "= 17 botol" tanpa menghitung. Riwayat terima PO bertanggal Indonesia ("7 Nov 2026")."""
import ast
import datetime as dt
import pathlib
from decimal import Decimal

import pytest

from app.routers import sales_orders as RSO
from app.schemas import sales_orders as S
from app.services import satuan_dasar as SD

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
KTX = {"alias": {}, "barang": {"p1": {"nama": "Minyak", "dasar": "botol", "konversi": {"karton": Decimal("12")}},
                               "p2": {"nama": "Gula", "dasar": "kg", "konversi": {}}}}


def test_qty_dasar_baris_konversi_dan_tak_terkonversi():
    assert SD.qty_dasar_baris(KTX, "p1", "karton", Decimal("1")) == {"qty_dasar": 12.0, "satuan_dasar": "botol"}
    assert SD.qty_dasar_baris(KTX, "p1", "botol", Decimal("5")) == {"qty_dasar": 5.0, "satuan_dasar": "botol"}
    assert SD.qty_dasar_baris(KTX, "p1", "lusin", Decimal("1")) == {"qty_dasar": None, "satuan_dasar": None}
    assert SD.qty_dasar_baris(KTX, None, "pcs", Decimal("1")) == {"qty_dasar": None, "satuan_dasar": None}


def test_total_dasar_per_satuan_tidak_dicampur():
    b = [SD.qty_dasar_baris(KTX, "p1", "karton", 1), SD.qty_dasar_baris(KTX, "p1", "botol", 5),
         SD.qty_dasar_baris(KTX, "p2", "kg", Decimal("2.5")), SD.qty_dasar_baris(KTX, "p1", "lusin", 1)]
    assert SD.total_dasar(b) == [{"qty": 17.0, "satuan": "botol"}, {"qty": 2.5, "satuan": "kg"}]


class _Conn:
    def __init__(self):
        self.inv = {"subtotal": 1, "discount_amount": 0, "shipping_amount": 0, "tax_amount": 0, "total_amount": 1,
                    "due_date": dt.date(2026, 11, 7), "invoice_date": dt.date(2026, 11, 7), "warehouse_id": None, "sales_order_id": None}

    async def fetchrow(self, sql, *a):
        return self.inv

    async def fetch(self, sql, *a):
        return [{"sales_order_item_id": None, "item_id": "p1", "unit": "karton", "description": "Minyak", "quantity": Decimal("1"),
                 "unit_price": 1, "subtotal": 1, "total": 1},
                {"sales_order_item_id": None, "item_id": "p1", "unit": "botol", "description": "Minyak", "quantity": Decimal("5"),
                 "unit_price": 1, "subtotal": 1, "total": 1}]


@pytest.mark.asyncio
async def test_pratinjau_to_invoice_kirim_qty_dasar_dan_total(monkeypatch):
    async def muat(conn, tid, ids):
        return KTX

    async def isyarat(*a, **k):
        return []

    async def tgl(*a):
        return dt.date(2026, 11, 7)
    monkeypatch.setattr(SD, "muat", muat)
    monkeypatch.setattr(RSO.SB, "isyarat", isyarat)
    monkeypatch.setattr(RSO, "tanggal_dokumen", tgl)
    body = RSO.ConvertToInvoiceRequest(apply_deposits=False)
    d = await RSO._baca_pratinjau(_Conn(), {"tenant_id": "t"}, {"invoice_id": "i", "posted": False, "due_date_source": "x"}, body)
    assert [(x["qty_dasar"], x["satuan_dasar"]) for x in d["lines"]] == [(12.0, "botol"), (5.0, "botol")]
    assert d["total_dasar"] == [{"qty": 17.0, "satuan": "botol"}]


def test_skema_detail_so_menyampaikan_qty_dasar():
    it = S.SalesOrderItemResponse.model_validate({"id": "1", "description": "x", "quantity": 1, "unit_price": 1, "line_total": 1,
                                                  "qty_dasar": 12.0, "satuan_dasar": "botol"}).model_dump()
    assert (it["qty_dasar"], it["satuan_dasar"]) == (12.0, "botol")
    assert "total_dasar" in S.SalesOrderDetail.model_fields


def test_detail_so_menyambung_qty_dasar():
    """Penyambungan di rute detail: SalesOrderItemResponse(**_dasar_det[...]) + SalesOrderDetail(total_dasar=...)."""
    f = next(n for n in ast.parse((APP / "routers" / "sales_orders.py").read_text()).body
             if isinstance(n, ast.AsyncFunctionDef) and n.name == "get_sales_order_detail")
    panggil = {n.func.id: n for n in ast.walk(f) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert any(k.arg is None and "_dasar_det" in ast.unparse(k.value) for k in panggil["SalesOrderItemResponse"].keywords)
    assert any(k.arg == "total_dasar" for k in panggil["SalesOrderDetail"].keywords)


def test_riwayat_terima_po_tanggal_indonesia():
    from app.services.tanggal_uang_muka import tgl
    assert tgl(dt.date(2026, 11, 7)) == "7 Nov 2026"
    src = (APP / "routers" / "purchase_orders.py").read_text()
    assert "receive_date.isoformat()})" not in src and "(tanggal terima {_tgl_id(body.receive_date)})" in src
