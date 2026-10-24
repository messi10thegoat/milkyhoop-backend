"""AS-GP4b (24 Okt 2026): PATCH UM Pembelian -- If-Match diperiksa SESUDAH replay idem dan SESUDAH kunci VENDOR_DEPOSIT (pola AS-G10).
Dulu sebelum keduanya: ulang ber-kunci membawa If-Match lama = 412 (updated_at sudah maju oleh tulisan pertama) alih-alih jawaban pertama."""
import ast
import asyncio
import inspect
import textwrap
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import vendor_deposits as RV
from app.schemas.vendor_deposits import VendorDepositUpdate


def _tubuh(fn):
    s = textwrap.dedent(inspect.getsource(fn))
    f = ast.parse(s).body[0]
    isi = f.body[1:] if ast.get_docstring(f) is not None else f.body
    return "\n".join(ast.get_source_segment(s, n) for n in isi)


def test_if_match_sesudah_replay_dan_kunci_sebelum_baca():
    p = _tubuh(RV.update_vendor_deposit)
    assert p.count("await assert_if_match_row(") == 1
    assert p.index("mulai_aksi(") < p.index('f"VENDOR_DEPOSIT:{deposit_id}"') < p.index("await assert_if_match_row(") < p.index("FOR UPDATE")


def _pasang(monkeypatch, lama):
    from app.services import idem_buat, optimistic_concurrency as OC

    class Tr:
        async def __aenter__(s): return s
        async def __aexit__(s, *a): return False

    class K:
        async def execute(s, *a): return None
        def transaction(s): return Tr()

    class CM:
        async def __aenter__(s): return K()
        async def __aexit__(s, *a): return False

    class P:
        def acquire(s): return CM()

    async def pool(): return P()

    async def mulai_aksi(*a, **k): return ("kp", "sd", lama)

    async def basi(*a, **k): raise HTTPException(status_code=412, detail="If-Match basi")
    monkeypatch.setattr(RV, "get_pool", pool)
    monkeypatch.setattr(RV, "get_user_context", lambda r: {"tenant_id": "t", "user_id": "u"})
    monkeypatch.setattr(RV, "_kunci_idem", lambda r: "kunci-1")
    monkeypatch.setattr(idem_buat, "mulai_aksi", mulai_aksi)
    monkeypatch.setattr(OC, "assert_if_match_row", basi)


def test_ulang_berkunci_dengan_if_match_basi_tetap_jawaban_pertama(monkeypatch):
    _pasang(monkeypatch, {"pertama": True})
    req = SimpleNamespace(headers={"if-match": '"lama"'})
    assert asyncio.run(RV.update_vendor_deposit(req, "6a263caa-0ea6-47e1-aa78-fc6575e38ff0", VendorDepositUpdate(notes="x"))) == {"pertama": True}


def test_kontrol_tanpa_jawaban_tersimpan_if_match_basi_tetap_412(monkeypatch):
    _pasang(monkeypatch, None)
    req = SimpleNamespace(headers={"if-match": '"lama"'})
    with pytest.raises(HTTPException) as e:
        asyncio.run(RV.update_vendor_deposit(req, "6a263caa-0ea6-47e1-aa78-fc6575e38ff0", VendorDepositUpdate(notes="x")))
    assert e.value.status_code == 412
