"""AS-G1b (24 Okt 2026): PATCH penawaran -- If-Match diperiksa SESUDAH replay idem QUOTE_UPDATE dan SESUDAH kunci QUOTE (pola AS-G10/GP4b).
Dulu sebelum keduanya: ulang ber-kunci membawa If-Match lama = 412 alih-alih jawaban pertama."""
import ast
import asyncio
import inspect
import textwrap
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import quotes as RQ
from app.schemas.quotes import UpdateQuoteRequest


def _tubuh(fn):
    s = textwrap.dedent(inspect.getsource(fn))
    f = ast.parse(s).body[0]
    isi = f.body[1:] if ast.get_docstring(f) is not None else f.body
    return "\n".join(ast.get_source_segment(s, n) for n in isi)


def test_if_match_sesudah_replay_dan_kunci_sebelum_penentu():
    p = _tubuh(RQ.update_quote)
    assert p.count("await assert_if_match_row(") == 1
    assert p.index("mulai_aksi(") < p.index('f"QUOTE:{ctx[\'tenant_id\']}:{quote_id}"') < p.index("await assert_if_match_row(") \
        < p.index("_ubah_penawaran(conn, ctx, quote_id, body)")
    assert 'f"QUOTE:{ctx[\'tenant_id\']}:{quote_id}"' in _tubuh(RQ._ubah_penawaran)  # kunci SAMA dengan penentu


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

    async def ubah(*a, **k): raise AssertionError("penentu tak boleh tercapai: 412 dulu")
    monkeypatch.setattr(RQ, "get_pool", pool)
    monkeypatch.setattr(RQ, "get_user_context", lambda r: {"tenant_id": "t", "user_id": "u"})
    monkeypatch.setattr(RQ, "_ubah_penawaran", ubah)
    monkeypatch.setattr(idem_buat, "kunci_dari", lambda request, kunci_body=None: "kunci-1")
    monkeypatch.setattr(idem_buat, "mulai_aksi", mulai_aksi)
    monkeypatch.setattr(OC, "assert_if_match_row", basi)


def test_ulang_berkunci_dengan_if_match_basi_tetap_jawaban_pertama(monkeypatch):
    _pasang(monkeypatch, {"pertama": True})
    req = SimpleNamespace(headers={"if-match": '"lama"'})
    assert asyncio.run(RQ.update_quote(req, "6a263caa-0ea6-47e1-aa78-fc6575e38ff0", UpdateQuoteRequest(subject="x"))) == {"pertama": True}


def test_kontrol_tanpa_jawaban_tersimpan_if_match_basi_tetap_412(monkeypatch):
    _pasang(monkeypatch, None)
    req = SimpleNamespace(headers={"if-match": '"lama"'})
    with pytest.raises(HTTPException) as e:
        asyncio.run(RQ.update_quote(req, "6a263caa-0ea6-47e1-aa78-fc6575e38ff0", UpdateQuoteRequest(subject="x")))
    assert e.value.status_code == 412
