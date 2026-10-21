"""Void penawaran Diterima (22 Okt 2026, MASTER; pola NetSuite: estimate belum dikonversi boleh ditutup kapan saja)."""
import asyncio
import os
import uuid
from datetime import date

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.routers import quotes as Q  # noqa: E402
from app.schemas.quotes import VoidQuoteRequest  # noqa: E402

QID = str(uuid.uuid4())


class _Tx:
    def __init__(self, k): self.k = k
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def start(self): self.k.log.append(("tx", "start"))
    async def rollback(self): self.k.log.append(("tx", "rollback"))


class Kon:
    def __init__(self, status, so=None, faktur=None, nomor="QUO-1"):
        self.status, self.so, self.faktur, self.nomor = status, so, faktur, nomor
        self.log, self.riwayat, self.update = [], [], []

    def transaction(self): return _Tx(self)

    async def execute(self, sql, *a):
        q = " ".join(sql.split())
        self.log.append(("exec", q[:80]))
        if q.startswith("UPDATE quotes SET status = 'void'"): self.update.append(a)
        return "OK"

    async def fetchrow(self, sql, *a):
        q = " ".join(sql.split())
        self.log.append(("fetchrow", q[:80]))
        if "FROM quotes" in q:
            return {"id": uuid.UUID(QID), "status": self.status, "quote_number": self.nomor, "total_amount": 1000,
                    "customer_name": "TES E2E", "converted_to_type": None, "converted_to_id": None}
        if "FROM sales_orders" in q: return {"order_number": self.so} if self.so else None
        if "FROM sales_invoices" in q: return {"invoice_number": self.faktur} if self.faktur else None
        return None


def _jalan(monkeypatch, kon, fn, *arg, dp=False):
    class _P:
        def acquire(s):
            class _A:
                async def __aenter__(x): return kon
                async def __aexit__(x, *a): return False
            return _A()
    async def gp(): return _P()
    async def dp_ok(*a, **k):
        if dp: raise HTTPException(status_code=409, detail="Ada uang muka aktif.")
    async def riwayat(conn, tid, tabel, id_, nomor, kode, user, teks, **k): kon.riwayat.append((kode, teks))
    monkeypatch.setattr(Q, "get_pool", gp)
    monkeypatch.setattr(Q, "get_user_context", lambda r: {"tenant_id": "t1", "user_id": "u-1"})
    monkeypatch.setattr(Q, "_tolak_bila_ada_uang_muka_aktif", dp_ok)
    monkeypatch.setattr(Q, "catat_riwayat", riwayat)
    return asyncio.run(fn(object(), QID, *arg))


def test_void_diterima_tanpa_turunan_dengan_alasan_berhasil_dan_berakor(monkeypatch):
    kon = Kon("accepted")
    r = _jalan(monkeypatch, kon, Q.void_quote, VoidQuoteRequest(reason="Pelanggan batal"))
    assert r.data["status"] == "void" and r.data["previous_status"] == "accepted"
    assert kon.update and kon.riwayat == [("QUOTE_VOIDED", "Penawaran QUO-1 dibatalkan: Pelanggan batal")]
    # lock dulu, FOR UPDATE, turunan dibaca SESUDAH lock
    assert "pg_advisory_xact_lock" in kon.log[0][1]
    i_lock = 0
    i_turunan = next(i for i, (k, q) in enumerate(kon.log) if "FROM sales_orders" in q)
    assert i_turunan > i_lock


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_alasan_wajib_untuk_diterima(monkeypatch, reason):
    kon = Kon("accepted")
    with pytest.raises(HTTPException) as e:
        _jalan(monkeypatch, kon, Q.void_quote, VoidQuoteRequest(reason=reason) if reason is not None else None)
    assert e.value.status_code == 422 and e.value.detail["code"] == "ALASAN_WAJIB"
    assert not kon.update


@pytest.mark.parametrize("status", ["draft", "sent"])
def test_alasan_opsional_untuk_draf_dan_terkirim(monkeypatch, status):
    kon = Kon(status)
    r = _jalan(monkeypatch, kon, Q.void_quote, None)
    assert r.data["status"] == "void" and kon.update


def test_diterima_yang_sudah_jadi_pesanan_ditolak_dengan_nomor_so(monkeypatch):
    kon = Kon("accepted", so="SO-2610-0007")
    with pytest.raises(HTTPException) as e:
        _jalan(monkeypatch, kon, Q.void_quote, VoidQuoteRequest(reason="x"))
    assert e.value.status_code == 400 and "sudah jadi pesanan SO-2610-0007" in e.value.detail and not kon.update


def test_diterima_yang_sudah_ada_faktur_ditolak(monkeypatch):
    kon = Kon("accepted", faktur="INV-2610-0003")
    with pytest.raises(HTTPException) as e:
        _jalan(monkeypatch, kon, Q.void_quote, VoidQuoteRequest(reason="x"))
    assert "INV-2610-0003" in e.value.detail


def test_converted_tetap_ditolak_dan_menyebut_pesanan(monkeypatch):
    kon = Kon("converted", so="SO-2610-0009")
    with pytest.raises(HTTPException) as e:
        _jalan(monkeypatch, kon, Q.void_quote, VoidQuoteRequest(reason="x"))
    assert "sudah jadi pesanan SO-2610-0009" in e.value.detail and not kon.update


@pytest.mark.parametrize("status", ["declined", "void"])
def test_status_lain_tetap_ditolak(monkeypatch, status):
    kon = Kon(status)
    with pytest.raises(HTTPException) as e:
        _jalan(monkeypatch, kon, Q.void_quote, VoidQuoteRequest(reason="x"))
    assert e.value.status_code == 400 and not kon.update


def test_uang_muka_aktif_tetap_memblok(monkeypatch):
    kon = Kon("accepted")
    with pytest.raises(HTTPException) as e:
        _jalan(monkeypatch, kon, Q.void_quote, VoidQuoteRequest(reason="x"), dp=True)
    assert "uang muka" in e.value.detail.lower() and not kon.update


def test_pratinjau_tak_menulis_dan_selalu_rollback(monkeypatch):
    kon = Kon("accepted", so="SO-9")
    r = _jalan(monkeypatch, kon, Q.preview_void_quote, VoidQuoteRequest(reason="x"))
    assert r["data"]["ok"] is False and r["data"]["blocks"][0]["code"] == "QUOTE_SUDAH_DIKONVERSI"
    assert r["data"]["alasan_wajib"] is True
    assert kon.log[0] == ("tx", "start") and kon.log[-1] == ("tx", "rollback")
    assert not kon.update and not kon.riwayat
    ok = _jalan(monkeypatch, Kon("accepted"), Q.preview_void_quote, VoidQuoteRequest(reason="alasan"))
    assert ok["data"]["ok"] is True and ok["data"]["blocks"] == []
    tanpa = _jalan(monkeypatch, Kon("accepted"), Q.preview_void_quote, None)
    assert tanpa["data"]["blocks"][0]["code"] == "ALASAN_WAJIB"


def test_pratinjau_dan_rute_memakai_penentu_yang_sama():
    import inspect
    assert "_rencana_void_penawaran(conn" in inspect.getsource(Q.void_quote)
    assert "_rencana_void_penawaran(conn" in inspect.getsource(Q.preview_void_quote)
    assert Q.VOID_STATUS == ("draft", "sent", "accepted")
