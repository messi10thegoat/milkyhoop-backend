"""VENDOR-IDEM (25 Okt 2026): buat/status/hapus vendor -- idem X-Idempotency-Key (ulang ber-kunci sesudah sukses = jawaban pertama,
bukan 400 "sudah ada"/404/aktivitas ganda) + kunci (buat: nama/kode sebelum cek ganda; status/hapus: VENDOR; hapus: + VENDOR_OPENING).
Gabung vendor TIDAK di unit ini: rute itu membaca/menulis kolom yang tak ada (merged_into_id/deleted_at/deleted_by) -> unit terpisah."""
import ast
import asyncio
import inspect
import textwrap
from types import SimpleNamespace

import pytest

from app.routers import vendors as V
from app.schemas.vendors import CreateVendorRequest

A, B = "6a263caa-0ea6-47e1-aa78-fc6575e38ff0", "0e5d7795-3a39-47bf-8e31-689766ffb05d"
TULIS = [
    (V.create_vendor, "VENDOR_CREATE", "idem_buat.mulai(", "VENDOR_NAMA:", "SELECT id FROM vendors WHERE tenant_id = $1 AND name = $2"),
    (V.toggle_vendor_status, "VENDOR_STATUS", "idem_buat.mulai_aksi(", "_kunci_vendor(", "FOR UPDATE"),
    (V.delete_vendor, "VENDOR_DELETE", "idem_buat.mulai_aksi(", "_kunci_vendor(", "smart_delete("),
]


def _tubuh(fn):
    s = textwrap.dedent(inspect.getsource(fn))
    f = ast.parse(s).body[0]
    isi = f.body[1:] if ast.get_docstring(f) is not None else f.body
    return "\n".join(ast.get_source_segment(s, n) for n in isi)


@pytest.mark.parametrize("fn,prefix,idem,kunci,baca", TULIS, ids=[t[1] for t in TULIS])
def test_urutan_idem_lalu_kunci_lalu_baca_lalu_simpan(fn, prefix, idem, kunci, baca):
    p = _tubuh(fn)
    assert p.count(idem) == 1 and f'"{prefix}"' in p and "idem_buat.kunci_dari(request)" in p
    assert p.index(idem) < p.index("if lama is not None") < p.index(kunci) < p.index(baca) < p.index(f'idem_buat.simpan(conn, ctx, kp, sidik, "{prefix}"')


def test_hapus_ikut_kunci_saldo_awal_status_tidak():
    assert "_kunci_vendor(conn, ctx[\"tenant_id\"], vendor_id, saldo_awal=True)" in _tubuh(V.delete_vendor)
    assert "saldo_awal" not in _tubuh(V.toggle_vendor_status)


class _K:
    def __init__(s): s.log = []
    async def execute(s, sql, *a):
        s.log.append((sql, a))


def test_kunci_vendor_urut_lalu_saldo_awal():
    k = _K()
    asyncio.run(V._kunci_vendor(k, "t", B, A, saldo_awal=True))
    kunci = [a[0] for _, a in k.log]
    assert kunci[:2] == sorted([f"VENDOR:t:{A}", f"VENDOR:t:{B}"]) and kunci[2:] == [f"VENDOR_OPENING:{v}" for v in sorted([A, B])]


def _pasang(monkeypatch, lama):
    from app.services import idem_buat
    log = []

    class Tr:
        async def __aenter__(s): return s
        async def __aexit__(s, *a): return False

    class K:
        async def execute(s, sql, *a): log.append(sql)
        async def fetchrow(s, sql, *a): log.append(sql)
        async def fetchval(s, sql, *a): log.append(sql)
        def transaction(s): return Tr()

    class CM:
        async def __aenter__(s): return K()
        async def __aexit__(s, *a): return False

    class P:
        def acquire(s): return CM()

    async def pool(): return P()
    async def mulai(*a, **k): return ("kp", "sd", lama)
    monkeypatch.setattr(V, "get_pool", pool)
    monkeypatch.setattr(V, "get_user_context", lambda r: {"tenant_id": "t", "user_id": "u"})
    monkeypatch.setattr(idem_buat, "kunci_dari", lambda r: "kunci-1")
    monkeypatch.setattr(idem_buat, "mulai", mulai)
    monkeypatch.setattr(idem_buat, "mulai_aksi", mulai)
    return log


R = SimpleNamespace(headers={}, state=SimpleNamespace(user={"tenant_id": "t", "user_id": "u"}))
PANGGIL = {
    "buat": lambda: V.create_vendor(R, CreateVendorRequest(name="TES E2E V")),
    "status": lambda: V.toggle_vendor_status(R, A, "inactive"),
    "hapus": lambda: V.delete_vendor(R, A),
}


@pytest.mark.parametrize("aksi", list(PANGGIL))
def test_ulang_berkunci_jawaban_pertama_tanpa_kunci_tanpa_baca(monkeypatch, aksi):
    log = _pasang(monkeypatch, {"pertama": True})
    assert asyncio.run(PANGGIL[aksi]()) == {"pertama": True}
    assert log == []


def test_gabung_diparkir_409_jujur_sebelum_badan():
    import pytest as _p
    from fastapi import HTTPException
    rute = [r for r in V.router.routes if getattr(r, "path", "") == "/merge" and "POST" in r.methods]
    assert len(rute) == 1
    deps = [d.call for d in rute[0].dependant.dependencies]
    assert len(deps) == 1
    with _p.raises(HTTPException) as e:
        asyncio.run(deps[0]())
    assert e.value.status_code == 409 and e.value.detail["code"] == "FEATURE_NOT_AVAILABLE"
    assert "Gabung vendor" in e.value.detail["message"]
