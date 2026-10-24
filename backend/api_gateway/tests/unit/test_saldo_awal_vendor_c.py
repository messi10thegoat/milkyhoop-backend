"""SALDO-AWAL-VENDOR-C (24/25 Okt 2026, pemilik "C now + A"): POST /vendors/{id}/opening-balance DITAHAN 409 {code, message} SEBELUM badan,
pool, atau jurnal apa pun -- jalur lama memposting Dr Hutang / Cr Modal (TERBALIK; V457 membacanya sebagai hutang negatif)."""
import ast
import asyncio
import inspect
import textwrap

import pytest
from fastapi import HTTPException

from app.routers import vendors as RV


def _tubuh(fn):
    s = textwrap.dedent(inspect.getsource(fn))
    f = ast.parse(s).body[0]
    isi = f.body[1:] if ast.get_docstring(f) is not None else f.body
    return "\n".join(ast.get_source_segment(s, n) for n in isi)


def test_penahan_aktif_dan_pesan_ringkas_tanpa_kurung():
    assert RV.SALDO_AWAL_VENDOR_DITAHAN is True
    assert RV.KODE_SALDO_AWAL_VENDOR_DITAHAN == "SALDO_AWAL_VENDOR_DITAHAN"
    p = RV.PESAN_SALDO_AWAL_VENDOR_DITAHAN
    assert p and "(" not in p and ")" not in p and "piutang" not in p.lower() and len(p) <= 100


def test_penahan_sebelum_badan_pool_dan_jurnal():
    t = _tubuh(RV.set_vendor_opening_balance)
    i = t.index("if SALDO_AWAL_VENDOR_DITAHAN:")
    assert i < t.index("await request.json()") and i < t.index("await get_pool()") and i < t.index("INSERT INTO journal_entries")


def test_perilaku_409_tanpa_menyentuh_badan_atau_pool(monkeypatch):
    async def pool_terlarang():
        raise AssertionError("pool tak boleh disentuh saat penahan aktif")

    class Req:
        headers = {}
        async def json(self):
            raise AssertionError("badan tak boleh dibaca saat penahan aktif")
    monkeypatch.setattr(RV, "get_pool", pool_terlarang)
    with pytest.raises(HTTPException) as e:
        asyncio.run(RV.set_vendor_opening_balance(Req(), "6a263caa-0ea6-47e1-aa78-fc6575e38ff0"))
    assert e.value.status_code == 409
    assert e.value.detail == {"code": "SALDO_AWAL_VENDOR_DITAHAN", "message": RV.PESAN_SALDO_AWAL_VENDOR_DITAHAN}
