"""Idempotensi jalur BUAT CW (4 Okt 2026, MASTER GO sesudah ukur): (6) POST /customer-deposits menerima header sebagai
cadangan body (beda -> 422), (8) POST /credit-notes dan (7) POST /quotes pola W0 lewat services/idem_buat. Tanpa DB;
perilaku nyata (1 dokumen per klik ganda, 409 + nomor, tanpa kunci = lama, nol jurnal) = harness kaos rollback."""
import asyncio
import inspect
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.responses import Response

from app.routers import credit_notes as CN, customer_deposits as CD, quotes as QU
from app.services import idem_buat as IB

T = "t-uji"
UID = str(uuid.uuid4())
DID = str(uuid.uuid4())
CTX = {"tenant_id": T, "user_id": UID}


class _Body:
    def model_dump(self, mode=None):
        return {"a": 1}


class _C:
    def __init__(self):
        self.sql, self.simpan = [], []

    async def execute(self, q, *a):
        self.sql.append(q)

    async def fetchrow(self, q, *a):
        assert a[1] == T, "nomor tersimpan dibaca berpagar tenant"
        return {"id": uuid.UUID(DID), "nomor": "CN-9"}


def test_tanpa_kunci_tak_menyentuh_apa_pun():
    c = _C()
    assert asyncio.run(IB.mulai(c, CTX, None, "CN_CREATE", _Body(), "credit_notes", "credit_note")) == (None, None, None)
    assert c.sql == []


def test_replay_dan_header(monkeypatch):
    lama = {"success": True, "data": {"id": DID}}
    async def replay(c, t, k, s):
        assert k == f"CN_CREATE:{UID}:k1" and t == T
        return lama
    monkeypatch.setattr(IB, "ambil_replay_klien", replay)
    r = Response()
    kp, sd, l = asyncio.run(IB.mulai(_C(), CTX, "k1", "CN_CREATE", _Body(), "credit_notes", "credit_note", r))
    assert l == lama and r.headers.get("X-Idempotent-Replay") == "true" and kp == f"CN_CREATE:{UID}:k1"


def test_badan_beda_409_menyebut_dokumen(monkeypatch):
    from app.utils.idempotency import KunciIdempotensiDipakai
    async def beda(*a):
        raise KunciIdempotensiDipakai({"data": {"id": DID}})
    monkeypatch.setattr(IB, "ambil_replay_klien", beda)
    with pytest.raises(HTTPException) as e:
        asyncio.run(IB.mulai(_C(), CTX, "k1", "CN_CREATE", _Body(), "credit_notes", "credit_note"))
    assert e.value.status_code == 409 and e.value.detail["credit_note_number"] == "CN-9"
    assert e.value.detail["credit_note_id"] == DID and e.value.detail["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_simpan_bentuk_json_dan_hanya_berkunci(monkeypatch):
    dicatat = []
    async def catat(*a, **k):
        dicatat.append(a)
    monkeypatch.setattr(IB, "simpan_replay_klien", catat)
    r = asyncio.run(IB.simpan(None, CTX, None, None, "X", {"t": Decimal("5.00")}, DID))
    assert r == {"t": 5.0} and dicatat == []
    asyncio.run(IB.simpan(None, CTX, "k", "s", "X", {"t": Decimal("5.00")}, DID))
    assert len(dicatat) == 1


@pytest.mark.parametrize("fn,prefix,penanda", [
    (CN.create_credit_note, "CN_CREATE", "buat_nota_kredit("),
    (QU.create_quote, "QUOTE_CREATE", "generate_quote_number"),
])
def test_jalur_buat_memakai_kunci_sebelum_menulis(fn, prefix, penanda):
    src = inspect.getsource(fn)
    assert f'idem_buat.mulai(conn, ctx, _kunci, "{prefix}"' in src
    assert src.index("idem_buat.mulai(") < src.index(penanda), "replay SEBELUM penomoran/penulisan"
    assert src.count("idem_buat.simpan(") == 1
    assert inspect.signature(fn).parameters["response"].default is None


def _req(kunci):
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": UID, "tenant_id": T, "role": "OWNER"}),
                           headers=({"X-Idempotency-Key": kunci} if kunci else {}))


def _dp(kunci_body=None):
    from app.schemas.customer_deposits import CreateCustomerDepositRequest as B
    return B(customer_name="A", amount=1000, deposit_date=date(2026, 10, 4), account_id=str(uuid.uuid4()), payment_method="transfer",
             idempotency_key=kunci_body)


@pytest.mark.parametrize("header,body,harap", [
    ("h-1", None, "h-1"),       # header saja: dulu DIABAIKAN
    (None, "b-1", "b-1"),       # body saja: jalur FE hari ini
    ("s-1", "s-1", "s-1"),      # sama: boleh
    (None, None, None),         # tanpa kunci: lama
])
def test_dp_header_cadangan_body(monkeypatch, header, body, harap):
    terlihat = []
    b = _dp(body)
    async def gp():
        terlihat.append(b.idempotency_key)
        raise RuntimeError("berhenti di sini")
    monkeypatch.setattr(CD, "get_pool", gp)
    monkeypatch.setattr(CD, "get_user_context", lambda r: r.state.user)
    with pytest.raises(HTTPException):
        asyncio.run(CD.create_customer_deposit(_req(header), b))
    assert terlihat == [harap]


def test_dp_body_dan_header_beda_422(monkeypatch):
    monkeypatch.setattr(CD, "get_user_context", lambda r: r.state.user)
    with pytest.raises(HTTPException) as e:
        asyncio.run(CD.create_customer_deposit(_req("h-1"), _dp("b-1")))
    assert e.value.status_code == 422 and e.value.detail["code"] == "IDEMPOTENCY_KEY_MISMATCH"
