"""BUG-354 (23 Nov 2026, putusan pemilik LANGSUNG): POST /api/journals/{id}/reverse -- FE hanya mengirim {reason}; dulu reversal_date WAJIB -> 422.
Kini opsional, bawaan = TANGGAL JURNAL ASLI (pola Accurate); periode tertutup = 409 pesan Indonesia (nama periode) di 'detail'."""
import asyncio
from datetime import date
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.routers import journals as RJ
from app.schemas.journals import ReverseJournalRequest

TGL_ASLI = date(2026, 11, 5)


def test_skema_reversal_date_opsional_reason_tetap_wajib():
    assert ReverseJournalRequest(reason="salah input").reversal_date is None
    assert ReverseJournalRequest(reason="x", reversal_date=date(2026, 11, 20)).reversal_date == date(2026, 11, 20)
    with pytest.raises(Exception):
        ReverseJournalRequest()


def _panggil(monkeypatch, periode, body):
    tangkap = {}

    class K:
        def transaction(self):
            class T:
                async def __aenter__(s):
                    return s

                async def __aexit__(s, *a):
                    return False
            return T()

        async def fetchrow(self, sql, *a):
            if "FROM journal_entries je" in sql and "LEFT JOIN journal_entries rb" in sql:
                return {"id": a[0], "status": "POSTED", "reversed_by_id": None, "journal_number": "JV-2611-0004", "description": "x",
                        "source_type": "MANUAL", "reversal_of_id": None, "journal_date": TGL_ASLI, "nomor_pembalik": None}
            if "FROM fiscal_periods" in sql:
                tangkap["tgl_periode"] = a[1]
                return periode
            raise AssertionError(sql[:80])

        async def fetch(self, sql, *a):
            return [{"account_id": uuid4(), "memo": "", "debit": 1000, "credit": 0}]

    class P:
        def acquire(self):
            class Cm:
                async def __aenter__(s):
                    return K()

                async def __aexit__(s, *a):
                    return False
            return Cm()

    async def pool():
        return P()
    monkeypatch.setattr(RJ, "get_pool", pool)
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    r.state.user = {"tenant_id": "t1", "user_id": "00000000-0000-0000-0000-000000000001"}
    with pytest.raises(HTTPException) as e:
        asyncio.run(RJ.reverse_journal(r, uuid4(), body))
    return e.value, tangkap


def test_tanpa_reversal_date_periode_dicari_pada_tanggal_jurnal_asli_tertutup_409(monkeypatch):
    e, t = _panggil(monkeypatch, {"id": uuid4(), "status": "CLOSED", "period_name": "Nov 2026"}, ReverseJournalRequest(reason="salah input"))
    assert t["tgl_periode"] == TGL_ASLI
    assert e.status_code == 409
    assert "Nov 2026" in e.detail and "sudah ditutup" in e.detail and "2026-11-05" in e.detail and "reversal_date" in e.detail


def test_reversal_date_eksplisit_dipakai_dan_pesan_tanpa_catatan_tanggal_asli(monkeypatch):
    e, t = _panggil(monkeypatch, {"id": uuid4(), "status": "LOCKED", "period_name": "Okt 2026"},
                    ReverseJournalRequest(reason="x", reversal_date=date(2026, 10, 3)))
    assert t["tgl_periode"] == date(2026, 10, 3)
    assert e.status_code == 409 and "Okt 2026" in e.detail and "tanggal jurnal asli" not in e.detail
