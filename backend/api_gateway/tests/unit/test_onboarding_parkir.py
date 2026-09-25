"""Audit izin usul E (25 Sep 2026) — parkir 2 rute onboarding ber-tenant-di-PATH.

POST /api/onboarding/chat/{tenant_id} (WRITE_EXEMPT) dan GET
/api/onboarding/profile/{tenant_id} (READ_OPEN) memakai tenant dari PATH tanpa
mencocokkannya ke tenant pemanggil. FE 0 pemanggil; access log 209 arsip = 0.
Kini 409 FEATURE_NOT_AVAILABLE SEBELUM handler (nol layanan/berkas disentuh);
rute onboarding lain (penyiapan tenant yang dipakai) TIDAK diparkir.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import onboarding as O

DIPARKIR = [
    ("POST", "/api/onboarding/chat/tenant-lain", {"message": "halo"}),
    ("GET", "/api/onboarding/profile/tenant-lain", None),
]


@pytest.fixture
def klien(monkeypatch):
    def meledak(*a, **k):
        raise AssertionError("handler berjalan padahal rute diparkir")
    from types import SimpleNamespace
    monkeypatch.setattr(O, "os", SimpleNamespace(path=SimpleNamespace(exists=meledak)))
    monkeypatch.setattr(O, "generate_trace_id", meledak, raising=False)
    app = FastAPI()
    app.include_router(O.router, prefix="/api/onboarding")
    return TestClient(app)


@pytest.mark.parametrize("metode,path,badan", DIPARKIR)
def test_diparkir_409_sebelum_handler(klien, metode, path, badan):
    r = klien.request(metode, path, json=badan)
    assert r.status_code == 409, (path, r.status_code, r.text)
    assert r.json()["detail"] == {"code": "FEATURE_NOT_AVAILABLE", "message": "Fitur ini belum tersedia."}


def test_badan_rusak_tetap_409_bukan_422(klien):
    assert klien.post("/api/onboarding/chat/x", json={}).status_code == 409


def test_hanya_dua_rute_diparkir():
    diparkir = set()
    for r in O.router.routes:
        d = getattr(r, "dependant", None)
        if d and any(getattr(x.call, "__qualname__", "").startswith("fitur_belum_tersedia_dengan")
                     for x in d.dependencies):
            for m in r.methods:
                diparkir.add((m, r.path))
    # + audit chat 26 Sep 2026: percakapan onboarding (dict dalam proses TANPA pemilik:
    # siapa pun baca/timpa/hapus session_id siapa pun; tenant/user dari BODY) + setup.
    assert diparkir == {("POST", "/chat/{tenant_id}"), ("GET", "/profile/{tenant_id}"),
                        ("POST", "/conversational-setup"), ("GET", "/conversation/{session_id}"),
                        ("DELETE", "/conversation/{session_id}")}
