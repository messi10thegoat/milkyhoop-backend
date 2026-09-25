"""Status faktur = SATU turunan untuk semua penulis (25 Sep 2026, dogfood grapgrap #1).

Latar terukur (prod read-only 25 Sep): menerapkan DP SEBAGIAN (customer_deposits apply) membiarkan status
'posted' (aturan lama: 'paid' bila lunas, selain itu status lama), sedangkan penerimaan pembayaran menulis
'partial'. grapgrap: 7 faktur 'posted' padahal DP sudah diterapkan (amount_paid == jurnal) → tampil "belum
dibayar" dan lolos dari ?status=partial. Kini keenam penulis cache (DP terapkan/lepas, penerimaan
posting/lepas/void, bayar dari faktur) memanggil services/pihak_helpers.segarkan_cache_piutang_faktur
(compute_ar_outstanding) — tak ada lagi aritmetika status sendiri.
"""
import ast
from decimal import Decimal as D
from pathlib import Path

import pytest

from app.services import pihak_helpers as PH

APP = Path(__file__).resolve().parents[2] / "app"
PENULIS = {
    "routers/customer_deposits.py": ["apply_deposit_core", "reverse_deposit_application_core"],
    "routers/receive_payments.py": ["_post_payment", "unapply_receive_payment_allocation", "void_receive_payment"],
    "routers/sales_invoices.py": ["record_payment"],
}


def _fungsi(berkas, nama):
    src = (APP / berkas).read_text()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nama:
            return n, src
    raise AssertionError(f"{berkas}::{nama} tidak ditemukan")      # gagal-keras bila nama berubah


def _panggilan(node, nama):
    return [c for c in ast.walk(node) if isinstance(c, ast.Call)
            and getattr(c.func, "id", getattr(c.func, "attr", None)) == nama]


def _sql(node):
    return [c.value for c in ast.walk(node) if isinstance(c, ast.Constant) and isinstance(c.value, str)]


@pytest.mark.parametrize("berkas,nama", [(b, n) for b, ns in PENULIS.items() for n in ns])
def test_penulis_memanggil_satu_turunan(berkas, nama):
    node, _ = _fungsi(berkas, nama)
    assert _panggilan(node, "segarkan_cache_piutang_faktur"), f"{nama} tak memanggil helper"


@pytest.mark.parametrize("berkas,nama", [(b, n) for b, ns in PENULIS.items() for n in ns])
def test_penulis_tak_menulis_status_sendiri(berkas, nama):
    node, _ = _fungsi(berkas, nama)
    for s in _sql(node):
        s1 = " ".join(s.split()).lower()
        if "update sales_invoices" in s1:
            assert "amount_paid" not in s1 and "status =" not in s1.split("where")[0].replace("fulfillment_status", ""), \
                f"{nama} masih menulis amount_paid/status faktur sendiri: {s1[:120]}"
        if "update accounts_receivable" in s1:
            raise AssertionError(f"{nama} masih menulis accounts_receivable sendiri")


# ---------- aturan helper ----------

class Conn:
    def __init__(self, faktur, sisa):
        self.faktur, self.sisa, self.exec = faktur, sisa, []

    async def fetchrow(self, sql, *a):
        return self.faktur

    async def fetchval(self, sql, *a):
        assert "compute_ar_outstanding" in sql
        return self.sisa

    async def execute(self, sql, *a):
        self.exec.append((" ".join(sql.split()), a))


async def _status(total, sisa, status="posted"):
    c = Conn({"total_amount": D(str(total)), "status": status}, None if sisa is None else D(str(sisa)))
    await PH.segarkan_cache_piutang_faktur(c, "grapgrap-manado", "inv-1")
    return c


@pytest.mark.asyncio
async def test_dp_sebagian_jadi_partial():
    c = await _status(3390000, 1390000)                       # INV-2609-0006: DP 2jt dari 3,39jt
    sql, a = c.exec[0]
    assert sql.startswith("UPDATE sales_invoices") and a[:2] == (D("2000000"), "partial")
    assert "tenant_id = $4" in sql and a[3] == "grapgrap-manado"


@pytest.mark.asyncio
async def test_lunas_jadi_paid_dan_nol_jadi_posted():
    assert (await _status(1000, 0)).exec[0][1][1] == "paid"
    assert (await _status(1000, 1000, "partial")).exec[0][1][1] == "posted"   # lepas DP terakhir -> posted
    assert (await _status(1000, 400, "paid")).exec[0][1][1] == "partial"      # lepas sebagian -> partial


@pytest.mark.asyncio
async def test_draf_dan_void_tak_disentuh():
    for st in ("draft", "void"):
        assert (await _status(1000, 400, st)).exec == []
