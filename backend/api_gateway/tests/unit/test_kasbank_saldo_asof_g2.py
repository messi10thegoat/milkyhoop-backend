"""G2: GET /bank-accounts/{id}/balance?as_of= -- saldo/total dari jurnal efektif bertanggal <= as_of; tanpa as_of = perilaku lama."""
import asyncio
import uuid
from datetime import date
from types import SimpleNamespace

import app.routers.bank_accounts as BAcc


class Kon:
    def __init__(self):
        self.q = []

    async def fetchrow(self, sql, *a):
        self.q.append((sql, a))
        if "FROM bank_accounts ba" in sql:
            return {"id": uuid.uuid4(), "account_name": "BCA", "opening_balance": 0, "ledger_balance": 1000}
        if "journal_lines jl" in sql:
            return {"total_deposits": 1500, "total_withdrawals": 500, "journal_txn_count": 2, "last_journal_date": date(2026, 11, 20)}
        return {"transaction_count": 2, "unreconciled_count": 1}


class Pool:
    def __init__(self, k):
        self.k = k

    def acquire(self):
        k = self.k

        class C:
            async def __aenter__(s):
                return k

            async def __aexit__(s, *a):
                return False
        return C()


def _panggil(as_of, monkeypatch):
    k = Kon()

    async def gp():
        return Pool(k)
    monkeypatch.setattr(BAcc, "get_pool", gp)
    monkeypatch.setattr(BAcc, "get_user_context", lambda r: {"tenant_id": "t"})
    out = asyncio.run(BAcc.get_bank_balance(SimpleNamespace(), uuid.uuid4(), as_of=as_of))
    return out, k


def test_tanpa_as_of_param_kosong_dan_field_null(monkeypatch):
    out, k = _panggil(None, monkeypatch)
    assert out["data"]["as_of"] is None and out["data"]["ledger_balance"] == 1000.0
    assert all(a[-1] is None for sql, a in k.q if "journal_date" in sql)


def test_dengan_as_of_dikirim_ke_kedua_query_jurnal(monkeypatch):
    out, k = _panggil(date(2026, 10, 31), monkeypatch)
    assert out["data"]["as_of"] == "2026-10-31"
    jurnal = [(sql, a) for sql, a in k.q if "journal_date" in sql]
    assert len(jurnal) == 2 and all(a[-1] == date(2026, 10, 31) and "is_effective_journal" in sql and "<= $3::date" in sql for sql, a in jurnal)
