"""C2 (26 Sep 2026): POST /api/receive-payments — kunci klien sama + isi BEDA = 409.

Dulu execute_idempotent me-replay respons tersimpan tanpa melihat isi: pengguna
membetulkan nominal di form yang sama lalu kirim ulang -> "sukses" dengan
pembayaran PERTAMA, nominal baru tak pernah tercatat (Law 14: kunci per MAKSUD).
"""
import json
import os
from decimal import Decimal

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from app.utils import idempotency as IDEM  # noqa: E402
from app.routers import receive_payments as RP  # noqa: E402
from app.schemas.receive_payments import CreateReceivePaymentRequest  # noqa: E402


class _KonIdem:
    """Tiruan idempotency_keys: PK (tenant, key); result disimpan sebagai teks JSON."""

    def __init__(self):
        self.baris = {}

    def is_in_transaction(self):
        return True

    async def fetchrow(self, sql, tenant, key):
        r = self.baris.get((tenant, key))
        return {"result": r, "result_id": None, "result_status": "SUCCESS"} if r else None

    async def execute(self, sql, key, tenant, source_type, result, *a):
        self.baris.setdefault((tenant, key), result)


def _op(hasil, catat):
    async def op():
        catat.append(1)
        return dict(hasil)
    return op


ASLI = {"success": True, "data": {"id": "p-1", "payment_number": "RCV-1"}}


@pytest.mark.asyncio
async def test_sidik_sama_replay_tanpa_medan_sidik():
    k, c = _KonIdem(), []
    r1 = await IDEM.execute_idempotent(k, "t", "K", "RP", _op(ASLI, c), payload_hash="h1")
    r2 = await IDEM.execute_idempotent(k, "t", "K", "RP", _op(ASLI, c), payload_hash="h1")
    assert (r1.was_cached, r2.was_cached, len(c)) == (False, True, 1)
    assert r2.data == ASLI  # medan sidik TIDAK bocor ke respons replay
    assert json.loads(k.baris[("t", "K")])["_idem_payload_hash"] == "h1"


@pytest.mark.asyncio
async def test_sidik_beda_409_membawa_respons_asli():
    k, c = _KonIdem(), []
    await IDEM.execute_idempotent(k, "t", "K", "RP", _op(ASLI, c), payload_hash="h1")
    with pytest.raises(IDEM.KunciIdempotensiDipakai) as e:
        await IDEM.execute_idempotent(k, "t", "K", "RP", _op(ASLI, c), payload_hash="h2")
    assert e.value.respons == ASLI and len(c) == 1


@pytest.mark.asyncio
async def test_baris_lama_tanpa_sidik_tetap_replay():
    k, c = _KonIdem(), []
    k.baris[("t", "K")] = json.dumps(ASLI)
    r = await IDEM.execute_idempotent(k, "t", "K", "RP", _op(ASLI, c), payload_hash="h9")
    assert r.was_cached and r.data == ASLI and c == []


@pytest.mark.asyncio
async def test_pemanggil_tanpa_sidik_perilaku_lama():
    k, c = _KonIdem(), []
    await IDEM.execute_idempotent(k, "t", "K", "BP", _op(ASLI, c))
    assert json.loads(k.baris[("t", "K")]) == ASLI  # bentuk tersimpan tak berubah
    r = await IDEM.execute_idempotent(k, "t", "K", "BP", _op({"lain": 1}, c))
    assert r.was_cached and r.data == ASLI and len(c) == 1


def _badan(**ubah):
    d = {"customer_id": "c1", "payment_date": "2026-09-26", "bank_account_id": "b1",
         "total_amount": "150000", "allocations": [{"invoice_id": "i1", "amount_applied": "100000"},
                                                   {"invoice_id": "i2", "amount_applied": "50000"}]}
    d.update(ubah)
    return CreateReceivePaymentRequest(**d)


def test_sidik_nominal_dinormalisasi_urutan_alokasi_bebas():
    a = RP._sidik_penerimaan(_badan())
    assert a == RP._sidik_penerimaan(_badan(total_amount="150000.00"))
    assert a == RP._sidik_penerimaan(_badan(allocations=[{"invoice_id": "i2", "amount_applied": "50000.0"},
                                                          {"invoice_id": "i1", "amount_applied": 100000}]))
    assert a != RP._sidik_penerimaan(_badan(total_amount="150001"))
    assert a != RP._sidik_penerimaan(_badan(allocations=[{"invoice_id": "i1", "amount_applied": "150000"}]))
    assert a != RP._sidik_penerimaan(_badan(notes="dibetulkan"))


# ---------- rute: header -> sidik; KunciIdempotensiDipakai -> 409 ----------

class _Kon:
    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a):
        return None


class _Pool:
    def acquire(self):
        return _Kon()


class _SetUser(BaseHTTPMiddleware):
    async def dispatch(self, req, nxt):
        req.state.user = {"user_id": "00000000-0000-0000-0000-0000000000aa", "tenant_id": "t", "role": "OWNER"}
        return await nxt(req)


@pytest.fixture
def klien(monkeypatch):
    tangkap = {}

    async def pool():
        return _Pool()

    async def pra(*a, **k):
        return None

    async def idem(conn, tenant, key, st, op, ttl_hours=24, payload_hash=None):
        tangkap["sidik"] = payload_hash
        if tangkap.get("tolak"):
            raise IDEM.KunciIdempotensiDipakai(ASLI)
        return IDEM.IdempotencyResult(data=dict(ASLI), was_cached=True, idempotency_key=key)

    monkeypatch.setattr(RP, "get_pool", pool)
    monkeypatch.setattr(RP, "_ensure_receive_payments_role_preconditions", pra)
    monkeypatch.setattr(RP, "execute_idempotent", idem)
    app = FastAPI()
    app.include_router(RP.router, prefix="/api/receive-payments")
    app.add_middleware(_SetUser)
    return TestClient(app, raise_server_exceptions=False), tangkap


BADAN = {"customer_id": "c1", "payment_date": "2026-09-26", "bank_account_id": "b1",
         "total_amount": "100000", "allocations": [{"invoice_id": "i1", "amount_applied": "100000"}]}


def test_kunci_klien_isi_beda_409(klien):
    k, t = klien
    t["tolak"] = True
    r = k.post("/api/receive-payments", json=BADAN, headers={"X-Idempotency-Key": "form-1"})
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert (d["payment_id"], d["payment_number"]) == ("p-1", "RCV-1")
    assert t["sidik"] == RP._sidik_penerimaan(CreateReceivePaymentRequest(**BADAN))


def test_tanpa_header_tanpa_sidik(klien):
    k, t = klien
    k.post("/api/receive-payments", json=BADAN)
    # status tak dinilai: respons tiruan tak memenuhi response_model (alat). Yang dinilai:
    # handler SAMPAI ke execute_idempotent dan TIDAK mengirim sidik.
    assert "sidik" in t and t["sidik"] is None
