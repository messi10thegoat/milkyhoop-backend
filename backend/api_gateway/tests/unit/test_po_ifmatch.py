"""PO-IM (25 Okt 2026): PATCH pesanan pembelian -- If-Match opt-in diperiksa SESUDAH replay idem PO_UPDATE dan SESUDAH kunci PO (pola AS-GP4b).
Ulang ber-kunci yang membawa If-Match lama = jawaban pertama (bukan 412); tanpa jawaban tersimpan, If-Match basi = 412 sebelum penentu/tulis."""
import ast
import asyncio
import inspect
import textwrap
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import purchase_orders as RP
from app.schemas.purchase_orders import UpdatePurchaseOrderRequest

ID = "6a263caa-0ea6-47e1-aa78-fc6575e38ff0"


def _tubuh(fn):
    s = textwrap.dedent(inspect.getsource(fn))
    f = ast.parse(s).body[0]
    isi = f.body[1:] if ast.get_docstring(f) is not None else f.body
    return "\n".join(ast.get_source_segment(s, n) for n in isi)


def test_if_match_sesudah_replay_dan_kunci_sebelum_penentu():
    p = _tubuh(RP.update_purchase_order)
    assert p.count("await assert_if_match_row(") == 1
    assert p.index("mulai_aksi(") < p.index("_kunci_po(") < p.index("await assert_if_match_row(") < p.index("rencana_ubah(")
    assert 'assert_if_match_row(request, "purchase_orders", po_id)' in p


def _pasang(monkeypatch, lama, catat):
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

    async def kunci(*a, **k):
        catat.append("kunci")
        return {"id": ID, "status": "draft", "po_number": "PO-1"}

    async def basi(*a, **k):
        catat.append("if_match")
        raise HTTPException(status_code=412, detail="If-Match basi")

    async def rencana(*a, **k):
        catat.append("rencana")
        return {"blocks": []}
    monkeypatch.setattr(RP, "get_pool", pool)
    monkeypatch.setattr(RP, "get_user_context", lambda r: {"tenant_id": "t", "user_id": "u"})
    monkeypatch.setattr(idem_buat, "kunci_dari", lambda r: "kunci-1")
    monkeypatch.setattr(idem_buat, "mulai_aksi", mulai_aksi)
    monkeypatch.setattr(RP, "_kunci_po", kunci)
    monkeypatch.setattr(RP.PR, "rencana_ubah", rencana)
    monkeypatch.setattr(OC, "assert_if_match_row", basi)


def test_ulang_berkunci_dengan_if_match_basi_tetap_jawaban_pertama(monkeypatch):
    catat = []
    _pasang(monkeypatch, {"pertama": True}, catat)
    req = SimpleNamespace(headers={"if-match": '"lama"'})
    assert asyncio.run(RP.update_purchase_order(req, ID, UpdatePurchaseOrderRequest(notes="x"))) == {"pertama": True}
    assert catat == []


def test_kontrol_tanpa_jawaban_tersimpan_if_match_basi_412_sesudah_kunci_sebelum_penentu(monkeypatch):
    catat = []
    _pasang(monkeypatch, None, catat)
    req = SimpleNamespace(headers={"if-match": '"lama"'})
    with pytest.raises(HTTPException) as e:
        asyncio.run(RP.update_purchase_order(req, ID, UpdatePurchaseOrderRequest(notes="x")))
    assert e.value.status_code == 412
    assert catat == ["kunci", "if_match"]
