"""Daftar faktur/tagihan terbuka tak memotong sen (temuan WORKSPACE 4 Nov 2026, Law 9).

GET /customers/{id}/open-invoices dan GET /vendors/{id}/open-bills dulu int() -> sisa 274,50 tampil 274 (form terapkan NK/uang
muka/terima bayar + alat chat membacanya). Kini Decimal jurnal -> JSON bulat int (sama dengan dulu), bersen float."""
import asyncio
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.routers import customers as CU
from app.routers import vendors as VE

D = Decimal


class Conn:
    def __init__(self, rows, kredit=0):
        self.rows, self.kredit = rows, kredit

    async def execute(self, *a):
        return None

    async def fetch(self, sql, *a):
        return self.rows

    async def fetchval(self, sql, *a):
        return self.kredit


def _pool(conn):
    class A:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *e):
            return False

    class P:
        def acquire(self):
            return A()

    async def get_pool():
        return P()
    return get_pool


def _jalan(mod, fn, conn, monkeypatch, *arg):
    monkeypatch.setattr(mod, "get_pool", _pool(conn))
    monkeypatch.setattr(mod, "get_user_context", lambda r: {"tenant_id": "t", "user_id": None})

    async def hari(conn, tid):
        return date(2026, 11, 4)
    monkeypatch.setattr(mod, "tanggal_dokumen", hari)
    return asyncio.run(fn(SimpleNamespace(state=SimpleNamespace(user={})), *arg))


def test_open_invoices_sen_terjaga_bulat_tetap_int(monkeypatch):
    rows = [{"id": uuid4(), "invoice_number": "INV-1", "invoice_date": date(2026, 10, 1), "due_date": date(2026, 10, 15),
             "total_amount": D("1000.00"), "remaining_amount": D("274.50"), "is_overdue": True, "overdue_days": 20},
            {"id": uuid4(), "invoice_number": "INV-2", "invoice_date": date(2026, 10, 2), "due_date": date(2026, 11, 30),
             "total_amount": D("50000.00"), "remaining_amount": D("50000.00"), "is_overdue": False, "overdue_days": 0}]
    r = _jalan(CU, CU.get_customer_open_invoices, Conn(rows), monkeypatch, str(uuid4()))
    a, b = r["invoices"]
    assert (a["remaining_amount"], a["paid_amount"], a["total_amount"]) == (274.5, 725.5, 1000)
    assert (b["remaining_amount"], type(b["remaining_amount"])) == (50000, int)
    assert r["summary"]["total_outstanding"] == 50274.5 and r["summary"]["total_overdue"] == 274.5


def test_open_bills_vendor_sen_terjaga(monkeypatch):
    rows = [{"bill_id": uuid4(), "bill_number": "B-1", "bill_date": date(2026, 10, 1), "due_date": date(2026, 10, 15),
             "total_amount": D("999.90"), "paid_amount": D("0.40"), "remaining_amount": D("999.50"), "is_overdue": True}]
    r = _jalan(VE, VE.get_vendor_open_bills, Conn(rows), monkeypatch, str(uuid4()))
    assert (r["bills"][0]["remaining_amount"], r["bills"][0]["paid_amount"], r["total_outstanding"]) == (999.5, 0.4, 999.5)


def test_tak_ada_int_pemotong_sen():
    import inspect
    for fn in (CU.get_customer_open_invoices, VE.get_vendor_open_bills):
        s = inspect.getsource(fn)
        assert 'int(row["remaining_amount"])' not in s and 'int(row["paid_amount"])' not in s, fn.__name__
