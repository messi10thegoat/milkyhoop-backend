"""Cache tagihan = SATU turunan untuk semua penulis (26 Sep 2026, antrean 4b; kembaran status faktur 25 Sep).

Dulu 6 penulis cache tagihan beraritmetika sendiri (amount_paid + $1, CASE status), sebagian SEBELUM jurnal
pembayaran dibuat; nota kredit vendor malah menulis status_v2. Layar tagihan menurunkan status dari jurnal,
tapi cache dibaca guard (void ditolak bila 'paid') + laporan. Kini semuanya memanggil
services/pihak_helpers.segarkan_cache_hutang_tagihan (compute_ap_outstanding), sesudah jurnal POSTED.
"""
import ast
from decimal import Decimal as D
from pathlib import Path

import pytest

from app.services import pihak_helpers as PH

APP = Path(__file__).resolve().parents[2] / "app"
PENULIS = {
    "routers/bill_payments.py": ["create_bill_payment", "post_bill_payment", "void_bill_payment"],
    "routers/vendor_deposits.py": ["apply_vendor_deposit"],
    "routers/vendor_credits.py": ["apply_vendor_credit"],
    "services/bills_service.py": ["record_payment"],
}
KASUS = [(b, n) for b, ns in PENULIS.items() for n in ns]


def _fungsi(berkas, nama):
    src = (APP / berkas).read_text()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nama:
            return n
    raise AssertionError(f"{berkas}::{nama} tidak ditemukan")


@pytest.mark.parametrize("berkas,nama", KASUS)
def test_penulis_memanggil_satu_turunan(berkas, nama):
    n = _fungsi(berkas, nama)
    assert any(isinstance(c, ast.Call) and getattr(c.func, "id", getattr(c.func, "attr", None)) == "segarkan_cache_hutang_tagihan"
               for c in ast.walk(n)), nama


@pytest.mark.parametrize("berkas,nama", KASUS)
def test_penulis_tak_menulis_cache_sendiri(berkas, nama):
    n = _fungsi(berkas, nama)
    for c in ast.walk(n):
        if isinstance(c, ast.Constant) and isinstance(c.value, str):
            s = " ".join(c.value.split()).lower()
            if "update bills" in s:
                assert "amount_paid" not in s and "status_v2 =" not in s, f"{nama}: {s[:100]}"
                assert not s.split(" where ")[0].count(" status ="), f"{nama}: {s[:100]}"
            assert "update accounts_payable" not in s, f"{nama} masih menulis accounts_payable sendiri"


def test_pembayaran_baru_cache_sesudah_journal_id():
    """compute_ap_outstanding membaca pembayaran lewat bill_payments_v2.journal_id -> helper WAJIB sesudahnya."""
    src = (APP / "routers/bill_payments.py").read_text()
    for nama, penanda in (("create_bill_payment", "SET journal_id = $1, journal_number = $2"),
                          ("post_bill_payment", "journal_id = $2, journal_number = $3")):
        seg = ast.get_source_segment(src, _fungsi("routers/bill_payments.py", nama))
        assert seg.index(penanda) < seg.index("segarkan_cache_hutang_tagihan("), nama


class Conn:
    def __init__(self, bill, sisa):
        self.bill, self.sisa, self.exec = bill, sisa, []

    async def fetchrow(self, sql, *a):
        return self.bill

    async def fetchval(self, sql, *a):
        assert "compute_ap_outstanding" in sql
        return self.sisa

    async def execute(self, sql, *a):
        self.exec.append((" ".join(sql.split()), a))


async def _jalan(amount, sisa, status="posted", v2="posted"):
    c = Conn({"amount": D(str(amount)), "status": status, "status_v2": v2}, D(str(sisa)))
    await PH.segarkan_cache_hutang_tagihan(c, "grapgrap-manado", "bill-1")
    return c


@pytest.mark.asyncio
async def test_aturan_status():
    c = await _jalan(1000, 400)
    sql, a = c.exec[0]
    assert sql.startswith("UPDATE bills SET amount_paid = $1, status = $2") and a[:2] == (D("600"), "partial")
    assert "status_v2" not in sql                                         # siklus hidup tak disentuh
    assert "tenant_id = $4" in sql and a[3] == "grapgrap-manado"
    assert "accounts_payable" in c.exec[1][0] and "tenant_id = $4" in c.exec[1][0]
    assert (await _jalan(1000, 0)).exec[0][1][1] == "paid"
    assert (await _jalan(1000, 1000, "paid")).exec[0][1][1] == "posted"   # void pembayaran terakhir


@pytest.mark.asyncio
async def test_draf_void_tak_disentuh():
    for st, v2 in (("draft", "draft"), ("void", "void"), ("posted", "void"), ("posted", "draft")):
        assert (await _jalan(1000, 400, st, v2)).exec == []
