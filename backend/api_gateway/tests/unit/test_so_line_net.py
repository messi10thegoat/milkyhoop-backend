"""GET /sales-orders/{id} items[].line_net = line_total − tax_amount (30 Sep 2026, MASTER/FE): line_total SO = BRUTO
(neto + PPN baris); neto dihitung SERVER supaya FE tak menurunkannya sendiri. Uji nyata baca-saja di prod: 
line_net + tax_amount = line_total di semua baris SO kaos & grapgrap (termasuk baris ber-PPN)."""
import ast
import os
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

from app.schemas.sales_orders import SalesOrderItemResponse  # noqa: E402
from app.routers import sales_orders as SO  # noqa: E402


def test_skema_membawa_line_net():
    assert "line_net" in SalesOrderItemResponse.model_fields


def test_detail_mengisi_line_net_dari_line_total_dikurangi_pajak():
    src = Path(SO.__file__).read_text(encoding="utf-8")
    kw = [k for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "SalesOrderItemResponse"
          for k in n.keywords if k.arg == "line_net"]
    assert len(kw) == 1
    assert " ".join(ast.unparse(kw[0].value).split()) == "item['line_total'] - (item['tax_amount'] or 0)"
