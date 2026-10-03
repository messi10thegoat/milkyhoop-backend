"""Riwayat Penawaran 'diubah' (3 Okt 2026, MASTER): PATCH /quotes/{id} mencatat QUOTE_UPDATED (ringkas + medan yang
diubah) lewat catat_riwayat, di transaksi YANG SAMA dengan suntingannya -- pola SALES_ORDER_UPDATED. Gagal mencatat =
suntingan gagal (tak ditelan). 'Dibuat' tetap dari kolom created_at/created_by (beraktor), seperti SO."""
import json

import pytest
from fastapi import HTTPException

from app.routers import quotes as Q
from app.schemas.quotes import UpdateQuoteRequest
from app.services.so_riwayat import RINGKAS_AUDIT

from .test_t39_tarif_penawaran import DBQ, QUOTE_ID, _baris, _req, pasang  # noqa: F401


class DBT(DBQ):
    """+ kedalaman transaksi: tiap tulisan dicatat bersama 'di dalam transaksi?'."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.dalam = 0
        self.urut = []

    async def fetchval(self, sql, *a):
        if " ".join(sql.split()).startswith("SELECT quote_number FROM quotes"):
            return "QUO-UJI-0009"
        return await super().fetchval(sql, *a)

    async def execute(self, sql, *a):
        s = " ".join(sql.split())
        self.urut.append((s[:30], self.dalam > 0, a))
        return await super().execute(sql, *a)

    def transaction(self):
        db = self

        class T:
            async def __aenter__(self):
                db.dalam += 1
                return db

            async def __aexit__(self, *e):
                db.dalam -= 1
                return False
        return T()


def _audit(db):
    return [(dalam, a) for (s, dalam, a) in db.urut if s.startswith("INSERT INTO audit_logs")]


@pytest.mark.asyncio
async def test_patch_catatan_tercatat_quote_updated_di_transaksi(pasang):
    db = pasang(DBT())
    await Q.update_quote(_req(), QUOTE_ID, UpdateQuoteRequest(notes="TES E2E ubah"))
    [(dalam, a)] = _audit(db)
    assert dalam, "audit WAJIB di transaksi yang sama"
    assert a[1] == "QUOTE_UPDATED" and a[2] == "quotes" and str(a[3]) == QUOTE_ID and a[4] == "QUO-UJI-0009"
    meta = json.loads(a[7])
    assert meta["fields"] == ["notes"] and meta["ringkas"] == "Penawaran diubah (notes)"
    i_upd = [k for k, (s, _, _) in enumerate(db.urut) if s.startswith("UPDATE quotes")][0]
    i_aud = [k for k, (s, _, _) in enumerate(db.urut) if s.startswith("INSERT INTO audit_logs")][0]
    assert i_upd < i_aud


@pytest.mark.asyncio
async def test_patch_baris_dan_dp_masuk_daftar_medan(pasang):
    db = pasang(DBT())
    await Q.update_quote(_req(), QUOTE_ID, UpdateQuoteRequest(items=[_baris()], dp_percent=10))
    [(_, a)] = _audit(db)
    assert json.loads(a[7])["fields"] == ["dp_percent", "items"]


@pytest.mark.asyncio
async def test_patch_kosong_tanpa_audit(pasang):
    db = pasang(DBT())
    await Q.update_quote(_req(), QUOTE_ID, UpdateQuoteRequest())
    assert _audit(db) == []


@pytest.mark.asyncio
async def test_gagal_mencatat_tidak_ditelan(pasang, monkeypatch):
    pasang(DBT())

    async def rusak(*a, **k):
        raise RuntimeError("audit_logs mati")
    monkeypatch.setattr(Q, "catat_riwayat", rusak)
    with pytest.raises(HTTPException) as e:
        await Q.update_quote(_req(), QUOTE_ID, UpdateQuoteRequest(notes="x"))
    assert e.value.status_code == 500


def test_label_riwayat():
    assert RINGKAS_AUDIT["QUOTE_UPDATED"] == "Penawaran diubah"
