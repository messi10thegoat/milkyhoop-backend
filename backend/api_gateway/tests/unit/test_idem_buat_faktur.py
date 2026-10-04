"""Idempotensi POST /sales-invoices (4 Okt 2026, MASTER GO; temuan uji nyata U1c: header X-Idempotency-Key DIABAIKAN ->
klik ganda = dua faktur, dengan auto_post = dua piutang terposting). Tanpa DB; perilaku nyata (1 faktur + 1 set jurnal,
409, tanpa kunci = lama, rollback tanpa lubang nomor) dibuktikan harness kaos satu-koneksi ber-tx luar rollback."""
import asyncio
import inspect
import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.responses import Response

from app.routers import sales_invoices as SI
from app.schemas.sales_invoices import CreateInvoiceRequest as CI

T = "t-uji"
UID = str(uuid.uuid4())
FID = str(uuid.uuid4())


class _Conn:
    def __init__(self):
        self.sql = []

    def transaction(self):
        class _T:
            async def __aenter__(s): return None
            async def __aexit__(s, *x): return False
        return _T()

    async def execute(self, q, *a):
        self.sql.append(q)

    async def fetchval(self, q, *a):
        raise AssertionError("replay/409 tak boleh sampai ke penomoran/penulisan: " + q)

    async def fetchrow(self, q, *a):
        assert "FROM sales_invoices" in q and a[1] == T, "nomor tersimpan dibaca dengan filter tenant"
        return {"id": uuid.UUID(FID), "invoice_number": "INV-9"}


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        c = self.c

        class _A:
            async def __aenter__(s): return c
            async def __aexit__(s, *x): return False
        return _A()


def _body():
    return CI(customer_name="A", invoice_date=date(2026, 10, 4),
              items=[{"description": "x", "quantity": 1, "unit_price": 1000}])


def _req(kunci):
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": UID, "tenant_id": T, "role": "OWNER"}),
                           headers={"X-Idempotency-Key": kunci})


@pytest.fixture
def conn(monkeypatch):
    c = _Conn()
    async def gp(): return _Pool(c)
    async def ok(*a): return None
    monkeypatch.setattr(SI, "get_pool", gp)
    monkeypatch.setattr(SI, "_ensure_role_preconditions", ok)
    return c


def test_kunci_sama_badan_sama_replay_tanpa_penomoran(monkeypatch, conn):
    lama = {"success": True, "message": "Invoice created successfully", "data": {"id": FID, "invoice_number": "INV-9"}}
    async def replay(c, t, k, s):
        assert k == f"SI_CREATE:{UID}:k-1" and t == T
        return lama
    monkeypatch.setattr(SI, "ambil_replay_klien", replay)
    resp = Response()
    r = asyncio.run(SI.create_invoice(_req("k-1"), _body(), resp))
    assert r == lama and resp.headers.get("X-Idempotent-Replay") == "true"
    assert any("IDEM:" in str(q) or "pg_advisory_xact_lock" in q for q in conn.sql)


def test_kunci_sama_badan_beda_409_menyebut_faktur_tersimpan(monkeypatch, conn):
    from app.utils.idempotency import KunciIdempotensiDipakai
    async def beda(*a):
        raise KunciIdempotensiDipakai({"data": {"id": FID}})
    monkeypatch.setattr(SI, "ambil_replay_klien", beda)
    with pytest.raises(HTTPException) as e:
        asyncio.run(SI.create_invoice(_req("k-1"), _body(), Response()))
    assert e.value.status_code == 409 and e.value.detail["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert e.value.detail["invoice_number"] == "INV-9" and e.value.detail["invoice_id"] == FID


def test_bentuk_kode():
    src = inspect.getsource(SI.create_invoice)
    assert src.index("IDEM:") < src.index("generate_sales_invoice_number"), "replay SEBELUM nomor terbit"
    assert src.count("_simpan_idem_buat(") == 2, "draf DAN auto_post menyimpan replay"
    assert "\n                return {" not in src and "\n                    return {" not in src
    assert inspect.signature(SI.create_invoice).parameters["response"].default is None


def test_replay_bentuk_json_sama():
    """Decimal di respons auto_post -> angka JSON yang sama untuk jawaban pertama dan replay."""
    from decimal import Decimal
    simpan = []
    class _C:
        async def execute(self, q, *a):
            simpan.append(a)
    r = asyncio.run(SI._simpan_idem_buat(_C(), {"tenant_id": T}, "SI_CREATE:x", "s", {"data": {"total_amount": Decimal("100000.00")}}, FID))
    assert r == {"data": {"total_amount": 100000.0}} and simpan
