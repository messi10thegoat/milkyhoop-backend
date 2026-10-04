"""Saldo uang muka tersedia (GET /customer-deposits/summary) = saldo BUKU PENUH akun peran
CUSTOMER_DEPOSIT_LIABILITY (MASTER 4 Okt 2026, Law 1/16/29).

Cacat yang dikunci: penyaring source_id lama (jurnal uang muka + receive_payments.created_deposit_id) melewatkan
jurnal RECEIVE_PAYMENT_UNAPPLY (lepas bayar -> uang muka) padahal pemakaiannya (DEPOSIT_APPLICATION) terhitung ->
kaos tampil "Saldo tersedia Rp -1.079.570"; buku penuh = 15.095.030 = sisa per dokumen (diukur 4 Okt).
"""
import asyncio
import inspect

from app.routers import customer_deposits as CD


def _rata(f):
    return " ".join(inspect.getsource(f).split())


def test_saldo_buku_tanpa_penyaring_sumber_dan_berpagar_tenant():
    src = _rata(CD.saldo_buku_uang_muka)
    assert "AccountRole.CUSTOMER_DEPOSIT_LIABILITY" in src
    assert "WHERE je.tenant_id = $1 AND jl.account_id = $2 AND is_effective_journal(je.id)" in src
    assert "SUM(jl.credit) - SUM(jl.debit)" in src
    sql = src[src.index('"""', src.index("fetchval")):]
    for penyaring in ("source_id", "source_type", "created_deposit_id", "receive_payments", "customer_deposits"):
        assert penyaring not in sql, penyaring       # jalur ke-N yang menyentuh akun ikut terhitung
    assert "current_balance" not in src and "amount_applied" not in src   # bukan kolom cache


def test_summary_memakai_saldo_buku():
    src = _rata(CD.get_customer_deposits_summary)
    assert "available_balance = await saldo_buku_uang_muka(conn, ctx[\"tenant_id\"])" in src
    assert "created_deposit_id IS NOT NULL" not in src and "je.source_id IN" not in src


class _Konn:
    def __init__(self, saldo):
        self.saldo, self.sql = saldo, []

    async def execute(self, *a):
        return None

    async def fetchrow(self, sql, *a):
        return {"total": 3, "draft_count": 0, "posted_count": 1, "partial_count": 1, "applied_count": 1,
                "total_value": 300, "total_applied": 250, "total_refunded": 10}

    async def fetchval(self, sql, *a):
        self.sql.append((" ".join(sql.split()), a))
        return self.saldo


class _Pool:
    def __init__(self, k):
        self.k = k

    def acquire(self):
        k = self.k

        class _A:
            async def __aenter__(s):
                return k

            async def __aexit__(s, *a):
                return False
        return _A()


def _jalan(monkeypatch, saldo):
    k = _Konn(saldo)

    async def _pool():
        return _Pool(k)

    async def _akun(conn, tid, peran):
        assert peran == CD.AccountRole.CUSTOMER_DEPOSIT_LIABILITY
        return "akun-dp"

    class _Req:
        state = type("S", (), {"user": {"tenant_id": "t1", "user_id": "u1"}})()

    monkeypatch.setattr(CD, "get_pool", _pool)
    monkeypatch.setattr(CD, "resolve_account_id_by_role", _akun)
    monkeypatch.setattr(CD, "get_user_context", lambda r: {"tenant_id": "t1", "user_id": "u1"})
    return asyncio.run(CD.get_customer_deposits_summary(_Req())), k


def test_summary_mengembalikan_saldo_buku_dan_tenant_eksplisit(monkeypatch):
    r, k = _jalan(monkeypatch, 15095030)
    assert r["data"]["available_balance"] == 15095030.0
    sql, args = k.sql[-1]
    assert args == ("t1", "akun-dp") and "je.tenant_id = $1" in sql


def test_summary_none_jadi_nol(monkeypatch):
    r, _ = _jalan(monkeypatch, None)
    assert r["data"]["available_balance"] == 0.0 and isinstance(r["data"]["available_balance"], float)
