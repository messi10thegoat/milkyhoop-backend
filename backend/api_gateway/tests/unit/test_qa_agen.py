"""QA-AGEN (25 Okt 2026, Milkybook): aksi penawaran untuk agen -- pratinjau tunggal READ ONLY (penentu SAMA dengan tulis) + idem
X-Idempotency-Key untuk /send, /accept, /decline, DELETE. Ulang ber-kunci sesudah sukses = jawaban pertama (bukan 400/404 -> kartu basi)."""
import ast
import asyncio
import inspect
import textwrap
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import quotes as Q
from app.schemas.quotes import DeclineQuoteRequest, SendQuoteRequest

ID = "6a263caa-0ea6-47e1-aa78-fc6575e38ff0"
TULIS = [  # (handler, prefix idem, panggilan penentu/inti)
    (Q.send_quote, "QUOTE_SEND", "kirim_penawaran_core("),
    (Q.accept_quote, "QUOTE_ACCEPT", 'rencana_terima_tolak(conn, ctx, quote_id, "diterima", kunci=True)'),
    (Q.decline_quote, "QUOTE_DECLINE", 'rencana_terima_tolak(conn, ctx, quote_id, "ditolak", kunci=True)'),
    (Q.delete_quote, "QUOTE_DELETE", "hapus_penawaran_core("),
]


def _tubuh(fn):
    s = textwrap.dedent(inspect.getsource(fn))
    f = ast.parse(s).body[0]
    isi = f.body[1:] if ast.get_docstring(f) is not None else f.body
    return "\n".join(ast.get_source_segment(s, n) for n in isi)


@pytest.mark.parametrize("fn,prefix,inti", TULIS, ids=[t[1] for t in TULIS])
def test_tulis_idem_sebelum_penentu_dan_simpan_sesudah(fn, prefix, inti):
    p = _tubuh(fn)
    assert p.count("mulai_aksi(") == 1 and f'"{prefix}"' in p
    assert p.index("mulai_aksi(") < p.index("if _lama is not None") < p.index(inti) < p.index(f'idem_buat.simpan(conn, ctx, _kp, _sd, "{prefix}"')
    assert "idem_buat.kunci_dari(request)" in p


def test_pratinjau_baca_readonly_tanpa_kunci_penentu_sama():
    assert "conn.transaction(readonly=True)" in _tubuh(Q._pratinjau_baca)
    assert "conn.transaction(readonly=True)" in _tubuh(Q.preview_delete_quote)
    assert "rencana_kirim_penawaran(conn, ctx, quote_id, kunci=False" in _tubuh(Q.preview_send_quote)
    assert "rencana_terima_tolak(conn, ctx, quote_id, aksi, kunci=False)" in _tubuh(Q._pratinjau_terima_tolak)
    assert "rencana_hapus_penawaran(conn, ctx, quote_id)" in _tubuh(Q.preview_delete_quote)
    p = _tubuh(Q.rencana_terima_tolak)
    assert 'if kunci:' in p and '(" FOR UPDATE" if kunci else "")' in p


class _Tr:
    async def __aenter__(s): return s
    async def __aexit__(s, *a): return False


class _K:
    def __init__(s, baris): s.baris, s.log = baris, []
    async def execute(s, sql, *a): s.log.append(sql)
    async def fetchrow(s, sql, *a):
        s.log.append(sql)
        return s.baris
    async def fetchval(s, sql, *a): return None
    def transaction(s, **kw): return _Tr()


def _pasang(monkeypatch, baris=None, lama=None):
    from app.services import idem_buat
    k = _K(baris)

    class CM:
        async def __aenter__(s): return k
        async def __aexit__(s, *a): return False

    class P:
        def acquire(s): return CM()

    async def pool(): return P()
    async def mulai_aksi(*a, **kw): return ("kp", "sd", lama)
    async def simpan(conn, ctx, kp, sd, st, resp, rid): return {"disimpan": st, "data": resp.data}
    async def riwayat(*a, **kw): return None
    async def dp(*a, **kw): return None
    monkeypatch.setattr(Q, "get_pool", pool)
    monkeypatch.setattr(Q, "get_user_context", lambda r: {"tenant_id": "t", "user_id": "u"})
    monkeypatch.setattr(idem_buat, "kunci_dari", lambda r: "kunci-1")
    monkeypatch.setattr(idem_buat, "mulai_aksi", mulai_aksi)
    monkeypatch.setattr(idem_buat, "simpan", simpan)
    monkeypatch.setattr(Q, "catat_riwayat", riwayat)
    monkeypatch.setattr(Q, "_tolak_bila_ada_uang_muka_aktif", dp)
    return k


R = SimpleNamespace(headers={})
PANGGIL = {
    "send": lambda: Q.send_quote(R, ID, SendQuoteRequest(send_email=False)),
    "accept": lambda: Q.accept_quote(R, ID),
    "decline": lambda: Q.decline_quote(R, ID, DeclineQuoteRequest(reason="mahal")),
    "delete": lambda: Q.delete_quote(R, ID),
}


@pytest.mark.parametrize("aksi", list(PANGGIL))
def test_ulang_berkunci_jawaban_pertama_tanpa_baca_tanpa_tulis(monkeypatch, aksi):
    k = _pasang(monkeypatch, baris=None, lama={"pertama": True})
    assert asyncio.run(PANGGIL[aksi]()) == {"pertama": True}
    assert k.log == []


@pytest.mark.parametrize("aksi,status", [("accept", "accepted"), ("decline", "declined")])
def test_kontrol_tanpa_jawaban_tersimpan_tulis_dan_simpan(monkeypatch, aksi, status):
    k = _pasang(monkeypatch, baris={"id": ID, "status": "sent", "quote_number": "QUO-1"}, lama=None)
    r = asyncio.run(PANGGIL[aksi]())
    assert r["disimpan"] == f"QUOTE_{aksi.upper()}" and r["data"]["status"] == status
    assert any("FOR UPDATE" in s for s in k.log) and any("UPDATE quotes SET status" in s for s in k.log)


@pytest.mark.parametrize("status,boleh", [("draft", False), ("sent", True), ("viewed", True), ("accepted", False), ("declined", False),
                                          ("void", False), ("converted", False)])
@pytest.mark.parametrize("aksi,after", [("diterima", "accepted"), ("ditolak", "declined")])
def test_paritas_pratinjau_menolak_iff_tulis_menolak(monkeypatch, status, boleh, aksi, after):
    baris = {"id": ID, "status": status, "quote_number": "QUO-1"}
    k = _pasang(monkeypatch, baris=baris)
    pv = asyncio.run(Q._pratinjau_terima_tolak(R, ID, aksi, after))["data"]
    try:
        asyncio.run(Q.rencana_terima_tolak(k, {"tenant_id": "t", "user_id": "u"}, ID, aksi, kunci=True))
        tulis = None
    except HTTPException as e:
        tulis = e.detail
    assert pv["ok"] is boleh and (tulis is None) is boleh
    if not boleh:
        assert pv["blocks"][0]["message"] == tulis
    assert pv["status_after"] == after


def test_pratinjau_404_tetap_404(monkeypatch):
    _pasang(monkeypatch, baris=None)
    with pytest.raises(HTTPException) as e:
        asyncio.run(Q.preview_accept_quote(R, ID))
    assert e.value.status_code == 404
