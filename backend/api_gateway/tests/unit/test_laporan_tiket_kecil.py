"""Tiket kecil laporan (10 Nov 2026, sesudah L2/L3): (1) umur piutang/hutang + rincian per pihak: urutan DETERMINISTIK (dulu hanya
ORDER BY due_date -> dokumen bertanggal jatuh tempo sama keluar dalam urutan bebas; kaos AP 13 baris/5 tanggal); (2) /trial-balance/full:
uang Decimal (dulu int() memotong sen di semua kolom)."""
import asyncio
import inspect
import uuid
from datetime import date
from decimal import Decimal

import pytest
from starlette.requests import Request

from app.routers import reports as RP


@pytest.mark.parametrize("fn,d", [(RP.get_aging_receivable, "invoice"), (RP.get_customer_aging_invoices, "invoice"),
                                  (RP.get_aging_payable, "bill"), (RP.get_vendor_aging_bills, "bill")])
def test_umur_urutan_deterministik(fn, d):
    assert f"ORDER BY a.due_date ASC, a.{d}_number, a.{d}_id" in inspect.getsource(fn)


class Kon:
    async def fetchrow(self, sql, *a):
        return {"id": uuid.uuid4(), "name": "November 2026", "start_date": date(2026, 11, 1), "end_date": date(2026, 11, 30)}

    async def fetch(self, sql, *a):
        b = dict(id=uuid.uuid4(), account_type="ASSET", category=None, level=2, is_header=False, parent_id=None, normal_balance="DEBIT")
        return [{**b, "code": "1-10100", "account_name": "Kas", "opening_debit": Decimal("1000.50"), "opening_credit": Decimal("0"),
                 "period_debit": Decimal("200.25"), "period_credit": Decimal("0"), "closing_debit": Decimal("1200.75"), "closing_credit": Decimal("0")},
                {**b, "code": "3-10000", "account_name": "Modal", "account_type": "EQUITY", "normal_balance": "CREDIT",
                 "opening_debit": Decimal("0"), "opening_credit": Decimal("1000.50"), "period_debit": Decimal("0"),
                 "period_credit": Decimal("200.25"), "closing_debit": Decimal("0"), "closing_credit": Decimal("1200.75")}]


class Kolam:
    def acquire(self):
        k = Kon()

        class A:
            async def __aenter__(self):
                return k

            async def __aexit__(self, *e):
                return False
        return A()


def test_full_sen_utuh(monkeypatch):
    async def kolam():
        return Kolam()
    monkeypatch.setattr(RP, "get_pool", kolam)
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})
    r.state.user = {"tenant_id": "t", "user_id": "u"}
    h = asyncio.run(RP.get_trial_balance_full(r, period_id=str(uuid.uuid4()), show_zero_balance=False, group_by_type=True))
    kas = h["data"]["accounts"][0]
    assert (kas["opening_debit"], kas["period_debit"], kas["closing_debit"]) == (Decimal("1000.50"), Decimal("200.25"), Decimal("1200.75"))
    s = h["data"]["summary"]
    assert s["total_closing_debit"] == Decimal("1200.75") == s["total_closing_credit"] and s["is_fully_balanced"] is True
    assert 'int(row["' not in inspect.getsource(RP.get_trial_balance_full)
