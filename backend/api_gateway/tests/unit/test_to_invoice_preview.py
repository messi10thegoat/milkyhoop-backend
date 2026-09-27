"""to-invoice/preview + to-invoice {post:true} (27 Sep 2026; CW "Buat faktur"). Paritas & nol-tulisan & atomik
dibuktikan di journey (skenario_to_invoice_preview); di sini: kabel dan perilaku rute dengan fake."""
import ast
import inspect
import uuid

import pytest
from fastapi import HTTPException

from app.routers import sales_invoices as SI
from app.routers import sales_orders as SO
from app.schemas.sales_orders import ConvertToInvoiceRequest

SOID = str(uuid.uuid4())
INV = uuid.uuid4()


class _Tr:
    def __init__(self, log):
        self.log = log

    async def start(self):
        self.log.append("start")

    async def rollback(self):
        self.log.append("rollback")

    async def commit(self):
        self.log.append("commit")

    async def __aenter__(self):
        self.log.append("start")

    async def __aexit__(self, t, e, tb):
        self.log.append("rollback" if t else "commit")
        return False


class _Conn:
    def __init__(self, idem=None):
        self.log, self.idem, self.q = [], idem, []

    def transaction(self):
        return _Tr(self.log)

    async def execute(self, q, *a):
        self.q.append(q)

    async def fetchval(self, q, *a):
        return "posted"

    async def fetch(self, q, *a):
        return []

    async def fetchrow(self, q, *a):
        if "idempotency_keys" in q:
            return self.idem
        raise AssertionError(q)


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        c = self.c

        class _A:
            async def __aenter__(self):
                return c

            async def __aexit__(self, *e):
                return False
        return _A()


class _Req:
    def __init__(self, h=None):
        self.headers = h or {}


class _Resp:
    def __init__(self):
        self.headers = {}


def _pasang(monkeypatch, conn, inti=None):
    async def gp():
        return _Pool(conn)
    monkeypatch.setattr(SO, "get_pool", gp)
    monkeypatch.setattr(SO, "get_user_context", lambda r: {"tenant_id": "t", "user_id": "u"})

    async def pre(pool, t):
        return None
    monkeypatch.setattr(SI, "_ensure_role_preconditions", pre)
    if inti is not None:
        monkeypatch.setattr(SO, "_to_invoice_dalam_tx", inti)


def _hasil(posted=True):
    from datetime import date
    return {"invoice_id": INV, "invoice_number": "INV-1", "order_number": "SO-1", "invoice_date": date(2026, 9, 27),
            "due_date": date(2026, 10, 11), "due_date_source": "so_terms", "total": 100, "posted": posted,
            "deposit_applications": [], "post_result": {"journal_number": "J-1"} if posted else None}


@pytest.mark.asyncio
async def test_pratinjau_selalu_rollback_juga_saat_galat(monkeypatch):
    c = _Conn()

    async def inti(conn, ctx, oid, body):
        raise HTTPException(status_code=400, detail="Quantity 99 exceeds uninvoiced 1")
    _pasang(monkeypatch, c, inti)
    with pytest.raises(HTTPException) as e:
        await SO.preview_to_invoice(_Req(), SOID, ConvertToInvoiceRequest(post=True))
    assert e.value.status_code == 400 and c.log == ["start", "rollback"]


@pytest.mark.asyncio
async def test_pratinjau_sukses_tetap_rollback_tak_pernah_commit(monkeypatch):
    c = _Conn()

    async def inti(conn, ctx, oid, body):
        return _hasil()

    async def baca(conn, ctx, h, body):
        return {"total": 100.0}
    _pasang(monkeypatch, c, inti)
    monkeypatch.setattr(SO, "_baca_pratinjau", baca)
    r = await SO.preview_to_invoice(_Req(), SOID, ConvertToInvoiceRequest(post=True))
    assert r["data"]["total"] == 100.0 and c.log == ["start", "rollback"] and "commit" not in c.log


@pytest.mark.asyncio
@pytest.mark.parametrize("post", [False, True])
async def test_terbit_hanya_bila_post_lewat_jalur_bersama(monkeypatch, post):
    dipanggil = []

    async def buat(conn, ctx, oid, body):
        return {k: v for k, v in _hasil(False).items() if k not in ("posted", "deposit_applications", "post_result")}

    async def terbit(conn, ctx, inv, nomor, total, tgl, apply_deposits=True, skip_deposit_ids=()):
        dipanggil.append((inv, apply_deposits, list(skip_deposit_ids)))
        return {"deposit_applications": [], "journal_number": "J-1"}
    monkeypatch.setattr(SO, "_buat_faktur_dari_so", buat)
    monkeypatch.setattr(SI, "terbitkan_faktur", terbit)
    h = await SO._to_invoice_dalam_tx(_Conn(), {"tenant_id": "t"}, SOID,
                                      ConvertToInvoiceRequest(post=post, apply_deposits=False, skip_deposit_ids=["x"]))
    assert h["posted"] is post
    assert dipanggil == ([(INV, False, ["x"])] if post else [])


def test_post_invoice_memakai_terbitkan_faktur_yang_sama():
    src = inspect.getsource(SI.post_invoice)
    assert "await terbitkan_faktur(" in src and "_internal_post_invoice(" not in src
    t = inspect.getsource(SI.terbitkan_faktur)
    assert t.index("await check_period_is_open(") < t.index("await _internal_post_invoice(") < t.index("await _auto_apply_so_deposits(")


def test_pratinjau_dan_to_invoice_satu_inti():
    for fn in (SO.preview_to_invoice, SO.convert_to_invoice):
        assert "_to_invoice_dalam_tx(conn, ctx, order_id, body)" in inspect.getsource(fn)
    src = inspect.getsource(SO._baca_pratinjau)
    for rumus in ("plan_so_invoice", "tentukan_jatuh_tempo", "* ", "quantity *"):   # tak ada kalkulator kedua
        assert rumus not in src.replace('"', "")


@pytest.mark.asyncio
@pytest.mark.parametrize("rute", ["preview", "to_invoice"])
async def test_404_berkode_untuk_id_tak_sah(monkeypatch, rute):
    _pasang(monkeypatch, _Conn())
    with pytest.raises(HTTPException) as e:
        if rute == "preview":
            await SO.preview_to_invoice(_Req(), "bukan-uuid", None)
        else:
            await SO.convert_to_invoice(_Req(), _Resp(), "bukan-uuid", None)
    assert e.value.status_code == 404 and e.value.detail["code"] == "SO_TIDAK_ADA"


@pytest.mark.asyncio
async def test_idempotensi_replay_dan_409(monkeypatch):
    import json
    body = ConvertToInvoiceRequest(post=True, idempotency_key="k1")
    sidik = SO.hash_payload(body.model_dump(mode="json", exclude={"idempotency_key"}))
    asli = {"success": True, "message": "m", "data": {"invoice_id": str(INV), "invoice_number": "INV-1"}}
    c = _Conn(idem={"result": json.dumps({"payload_hash": sidik, "response": asli})})

    async def inti(*a):
        raise AssertionError("replay tak boleh membuat faktur lagi")
    _pasang(monkeypatch, c, inti)
    resp = _Resp()
    r = await SO.convert_to_invoice(_Req(), resp, SOID, body)
    assert r.data["invoice_id"] == str(INV) and resp.headers["X-Idempotent-Replay"] == "true"
    assert any("pg_advisory_xact_lock" in q for q in c.q)
    c2 = _Conn(idem={"result": json.dumps({"payload_hash": "lain", "response": asli})})
    _pasang(monkeypatch, c2, inti)
    with pytest.raises(HTTPException) as e:
        await SO.convert_to_invoice(_Req(), _Resp(), SOID, body)
    assert e.value.status_code == 409 and e.value.detail["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert e.value.detail["invoice_number"] == "INV-1"


def test_skema_bawaan_kompatibel():
    b = ConvertToInvoiceRequest()
    assert b.post is False and b.apply_deposits is True and b.skip_deposit_ids == []
    assert b.notes is None and b.idempotency_key is None
    assert ConvertToInvoiceRequest(notes="x").notes == "x"


def test_notes_masuk_insert_faktur():
    src = " ".join(inspect.getsource(SO._buat_faktur_dari_so).split())
    assert "shipping_dpp, notes ) VALUES" in src and '_rek_eksplisit(body, "notes")' in src
