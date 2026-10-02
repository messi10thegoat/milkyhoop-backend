"""Penawaran CW BE (2 Okt 2026, MASTER). Diuji nyata di salinan DB 11/11: calculate == create; pratinjau to-order nol
tulis + nomor/angka == to-order nyata; accept bersamaan -> satu menang; riwayat beraktor tanpa duplikat kolom;
Perpanjang di 'sent'; DP lewat penawaran saja -> 422. Di sini: PERILAKU kunci di tiap aksi + blok DP."""
import asyncio
import uuid

import pytest

from app.routers import quotes as Q

QID = str(uuid.uuid4())


class _Conn:
    def __init__(self, status):
        self.status, self.log = status, []

    def transaction(self):
        class _T:
            async def __aenter__(s): return None
            async def __aexit__(s, *a): return False
        return _T()

    async def execute(self, sql, *a):
        self.log.append(("execute", " ".join(sql.split()), a))
        return "UPDATE 1"

    async def fetchrow(self, sql, *a):
        self.log.append(("fetchrow", " ".join(sql.split()), a))
        if "FROM quotes" in sql:
            return {"id": uuid.UUID(QID), "status": self.status, "quote_number": "QUO-T", "total_amount": 0,
                    "customer_name": "x"}
        return None

    async def fetchval(self, sql, *a):
        return None


def _jalan(monkeypatch, fn, status, *arg):
    conn = _Conn(status)

    class _P:
        def acquire(self):
            class _A:
                async def __aenter__(s): return conn
                async def __aexit__(s, *a): return False
            return _A()

    async def gp(): return _P()
    async def tanpa(*a, **k): return None
    monkeypatch.setattr(Q, "get_pool", gp)
    monkeypatch.setattr(Q, "get_user_context", lambda r: {"tenant_id": "t1", "user_id": None})
    monkeypatch.setattr(Q, "_tolak_bila_ada_uang_muka_aktif", tanpa)
    monkeypatch.setattr(Q, "catat_riwayat", tanpa)
    asyncio.run(fn(object(), QID, *arg))
    return conn


@pytest.mark.parametrize("fn,status,arg", [
    ("send_quote", "draft", (None,)),
    ("accept_quote", "sent", ()),
    ("decline_quote", "sent", ("DECLINE",)),
    ("void_quote", "draft", (None,)),
])
def test_aksi_mengunci_lalu_for_update(monkeypatch, fn, status, arg):
    from app.schemas.quotes import DeclineQuoteRequest
    arg = tuple(DeclineQuoteRequest(reason="mahal") if a == "DECLINE" else a for a in arg)
    conn = _jalan(monkeypatch, getattr(Q, fn), status, *arg)
    assert conn.log[0][0] == "execute" and "pg_advisory_xact_lock" in conn.log[0][1]
    assert conn.log[0][2] == (f"QUOTE:t1:{QID}",)
    baca = next(x for x in conn.log if x[0] == "fetchrow")
    assert baca[1].endswith("FOR UPDATE")


def test_dp_lewat_penawaran_tanpa_so_ditolak_sebelum_db(monkeypatch):
    from app.routers import customer_deposits as CD
    from app.schemas.customer_deposits import CreateCustomerDepositRequest
    from types import SimpleNamespace

    async def dilarang():
        raise AssertionError("DB tak boleh disentuh")
    monkeypatch.setattr(CD, "get_pool", dilarang)
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": "t1", "user_id": str(uuid.uuid4())}), headers={})
    body = CreateCustomerDepositRequest(customer_name="x", amount=1000, deposit_date="2026-10-02",
                                        account_id=str(uuid.uuid4()), payment_method="cash", quote_id=QID)
    with pytest.raises(Exception) as e:
        asyncio.run(CD.create_customer_deposit(req, body))
    assert getattr(e.value, "status_code", None) == 422 and "wajib lewat SO" in str(e.value.detail)


def test_pratinjau_to_order_memakai_jalan_yang_sama_lalu_batal():
    import inspect
    src = inspect.getsource(Q.preview_convert_to_sales_order)
    assert "_konversi_penawaran(conn, ctx, quote_id, body)" in src and "raise _Batalkan(" in src
    assert "_konversi_penawaran(conn, ctx, quote_id, body)" in inspect.getsource(Q.convert_to_sales_order)
