"""Gerbang Unit A: PATCH beban draf diperluas. Draf -> ubah amount/akun/baris (DB berubah,
status tetap draf); non-draf -> 400; akun tak ada -> 400; nominal 0 -> 400."""
import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import expenses as exp_mod
from app.routers.expenses import update_expense
from app.schemas.expenses import UpdateExpenseRequest

TENANT = "gate-tenant"
USER = str(uuid.uuid4())
EXP = uuid.uuid4()
BANK = uuid.uuid4()
ACCT1 = uuid.uuid4()
ACCT2 = uuid.uuid4()


def _req():
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": TENANT, "user_id": USER}))


def _row(status="draft"):
    return {
        "id": str(EXP), "tenant_id": TENANT, "status": status,
        "expense_date": date(2026, 9, 16), "is_itemized": False,
        "paid_through_id": str(BANK), "tax_rate": 0, "pph_rate": 0,
        "subtotal": 100000, "account_id": str(ACCT1), "account_name": "Old",
        "vendor_id": None, "vendor_name": None, "tax_id": None, "tax_name": None,
        "pph_type": None, "currency": "IDR", "is_billable": False,
        "billed_to_customer_id": None, "reference": None, "notes": None,
        "has_receipt": False,
    }


class _Txn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self, status="draft", acct_exists=True):
        self.status = status
        self.acct_exists = acct_exists
        self.updated = None
        self.inserts = []
        self.deletes = 0

    def transaction(self):
        return _Txn()

    async def execute(self, sql, *args):
        s = " ".join(str(sql).split())
        if s.startswith("UPDATE expenses SET"):
            self.updated = args
        elif s.startswith("DELETE FROM expense_items"):
            self.deletes += 1
        elif s.startswith("INSERT INTO expense_items"):
            self.inserts.append(args)
        return None

    async def fetch(self, sql, *a):
        return []

    async def fetchrow(self, sql, *args):
        s = " ".join(str(sql).split())
        if "FROM expenses WHERE id = $1 AND tenant_id = $2 FOR UPDATE" in s:
            return _row(self.status)
        if "FROM bank_accounts" in s:
            return {"id": str(BANK), "account_name": "BCA", "coa_id": str(uuid.uuid4())}
        if "FROM chart_of_accounts" in s:
            return {"id": args[0], "name": "Acct"} if self.acct_exists else None
        if "SELECT * FROM expenses WHERE id = $1" in s:
            u = self.updated
            return {"id": str(EXP), "subtotal": u[15], "total_amount": u[16],
                    "is_itemized": u[17], "account_id": u[4], "status": "draft"}
        return None

    async def fetchval(self, *a, **k):
        return None


class _Acq:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acq(self.conn)


def _patch(monkeypatch, conn):
    async def fp():
        return _Pool(conn)

    async def noop_period(*a, **k):
        return None

    monkeypatch.setattr(exp_mod, "get_pool", fp)
    monkeypatch.setattr(exp_mod, "check_period_is_open", noop_period)


@pytest.mark.asyncio
async def test_a_green_edit_itemized_lines(monkeypatch):
    conn = _Conn(status="draft")
    _patch(monkeypatch, conn)
    body = UpdateExpenseRequest(
        is_itemized=True,
        line_items=[
            {"account_id": str(ACCT1), "amount": 300000},
            {"account_id": str(ACCT2), "amount": 200000},
        ],
    )
    res = await update_expense(_req(), EXP, body)
    assert conn.updated is not None
    assert int(conn.updated[15]) == 500000  # subtotal dari baris
    assert int(conn.updated[16]) == 500000  # total (pajak 0)
    assert conn.updated[17] is True  # is_itemized
    assert conn.deletes == 1 and len(conn.inserts) == 2  # baris diganti
    assert res["data"]["status"] == "draft"  # status tetap draf
    assert int(res["data"]["subtotal"]) == 500000


@pytest.mark.asyncio
async def test_a_green_edit_amount_account(monkeypatch):
    conn = _Conn(status="draft")
    _patch(monkeypatch, conn)
    await update_expense(_req(), EXP, UpdateExpenseRequest(amount=750000, account_id=str(ACCT2)))
    assert int(conn.updated[15]) == 750000  # subtotal dari amount
    assert conn.updated[4] == str(ACCT2)  # account_id berubah


@pytest.mark.asyncio
async def test_a_red_non_draft(monkeypatch):
    conn = _Conn(status="posted")
    _patch(monkeypatch, conn)
    with pytest.raises(HTTPException) as ei:
        await update_expense(_req(), EXP, UpdateExpenseRequest(amount=1))
    assert ei.value.status_code == 400 and "draft" in ei.value.detail


@pytest.mark.asyncio
async def test_a_red_bad_account(monkeypatch):
    conn = _Conn(status="draft", acct_exists=False)
    _patch(monkeypatch, conn)
    with pytest.raises(HTTPException) as ei:
        await update_expense(_req(), EXP, UpdateExpenseRequest(amount=1000, account_id=str(ACCT2)))
    assert ei.value.status_code == 400 and "expense account" in ei.value.detail.lower()


@pytest.mark.asyncio
async def test_a_red_zero_amount(monkeypatch):
    conn = _Conn(status="draft")
    _patch(monkeypatch, conn)
    with pytest.raises(HTTPException) as ei:
        await update_expense(_req(), EXP, UpdateExpenseRequest(amount=0))
    assert ei.value.status_code == 400 and "greater than 0" in ei.value.detail
