"""Q-016 (a) (25 Sep 2026): penanda faktur DRAF per SO di daftar & detail — status SO tetap.

Latar: SO berstatus 'invoiced' padahal fakturnya DRAF (grapgrap SO-2609-0006) — label menyesatkan, tapi
penahan draf (quantity_invoiced menghitung draf -> cegah faktur ganda) BENAR (putusan MASTER). Penanda
dihitung dari SUMBER YANG SAMA dengan quantity_invoiced: tautan per baris sales_invoice_items.
sales_order_item_id, faktur non-void; dipecah draf vs terbit.
"""
import ast
import uuid
from pathlib import Path

import pytest

from app.schemas.sales_orders import SalesOrderDetail, SalesOrderListItem
from app.services import so_faktur_draf as SD

APP = Path(__file__).resolve().parents[2] / "app"
T = "grapgrap-manado"


def test_sql_sumber_sama_dengan_quantity_invoiced():
    s = " ".join(SD.SQL_PENANDA.split())
    assert "JOIN sales_order_items soi ON soi.id = sii.sales_order_item_id" in s   # tautan PER BARIS
    assert "si.status <> 'void'" in s                                             # non-void, draf ikut
    assert "si.tenant_id = $1" in s and "soi.sales_order_id = ANY($2::uuid[])" in s
    assert "COUNT(DISTINCT si.id) FILTER (WHERE si.status = 'draft')" in s
    assert "SUM(sii.quantity) FILTER (WHERE si.status NOT IN ('draft', 'void'))" in s


class Conn:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    async def fetch(self, sql, *a):
        self.calls.append(a)
        return self.rows


@pytest.mark.asyncio
async def test_pemetaan_dan_satu_kueri():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    conn = Conn([{"so_id": a, "draft_invoice_count": 1, "posted_invoiced_qty": 0},
                 {"so_id": b, "draft_invoice_count": 0, "posted_invoiced_qty": 12}])
    h = await SD.penanda_faktur_so(conn, T, [a, b, c])
    assert len(conn.calls) == 1 and conn.calls[0] == (T, [str(a), str(b), str(c)])
    assert h[str(a)] == {"has_draft_invoice": True, "draft_invoice_count": 1, "posted_invoiced_qty": 0.0}
    assert h[str(b)] == {"has_draft_invoice": False, "draft_invoice_count": 0, "posted_invoiced_qty": 12.0}
    assert h[str(c)] == SD.KOSONG


@pytest.mark.asyncio
async def test_kosong_tanpa_kueri():
    conn = Conn([])
    assert await SD.penanda_faktur_so(conn, T, []) == {}
    assert conn.calls == []


def _fungsi(nama):
    src = (APP / "routers/sales_orders.py").read_text()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nama:
            return n
    raise AssertionError(nama)


@pytest.mark.parametrize("nama", ["list_sales_orders", "get_sales_order_detail"])
def test_daftar_dan_detail_menyambung(nama):
    n = _fungsi(nama)
    assert any(isinstance(c, ast.Call) and getattr(c.func, "id", None) == "penanda_faktur_so" for c in ast.walk(n))
    # penanda disebar ke model respons (kalau tidak, response_model membuang medan)
    assert any(isinstance(k, ast.keyword) and k.arg is None and getattr(k.value, "id", None) == "penanda"
               or (isinstance(k, ast.keyword) and k.arg is None and isinstance(k.value, ast.Subscript))
               for k in ast.walk(n))


@pytest.mark.parametrize("model", [SalesOrderListItem, SalesOrderDetail])
def test_skema_membawa_medan(model):
    for k in ("has_draft_invoice", "draft_invoice_count", "posted_invoiced_qty"):
        assert k in model.model_fields, (model.__name__, k)
