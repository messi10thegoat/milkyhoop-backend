"""retry-all-failed + process (1 Okt 2026): rute dibuka untuk non-OWNER, izin ditegakkan di HANDLER.

Dulu: kedua rute TAK TERPETAKAN -> hanya OWNER. retry-all-failed me-reset SEMUA dokumen posting_failed
tenant lalu mengeksekusi ulang tanpa cek izin apa pun (aman hanya karena OWNER-saja). Membukanya
"cukup anggota aktif" = staf baca-saja bisa memposting jurnal. Kini: izin PER DOKUMEN sama dengan
execute-batch (_saring_izin_per_dokumen); yang ditolak TIDAK di-reset dan TIDAK dieksekusi.
process (OCR + klasifikasi, tak memposting) = anggota aktif, sama dengan /upload.
"""
from uuid import UUID

import pytest

from app.routers import document_intake as DI
from .test_intake_override_legacy import DOC, DOC2, IZIN, LEGACY, REST, _Conn, _Ctx, _Eng, _doc, uji  # noqa: F401


@pytest.fixture
def uji2(uji, monkeypatch):
    k, db, catat = uji
    db["_args"] = []

    async def fetch(self, sql, *a):
        assert "posting_failed" in sql
        return [{"id": UUID(i)} for i, d in self.db.items() if not i.startswith("_") and d["status"] == "posting_failed"]

    lama_execute = _Conn.execute

    async def execute(self, sql, *a):
        self.db["_args"].append(a)
        return await lama_execute(self, sql, *a)

    monkeypatch.setattr(_Conn, "fetch", fetch, raising=False)
    monkeypatch.setattr(_Conn, "execute", execute)
    monkeypatch.setitem(IZIN, "NONAKTIF", {("C", "receipt"), ("C", "journal")})

    async def ctx_nonaktif(self, user_id, tenant_id, peran):
        c = _Ctx(peran)
        c.membership_active = peran != "NONAKTIF"
        return c

    monkeypatch.setattr(_Eng, "get_user_context", ctx_nonaktif)
    return k, db, catat


def _ulang(k, peran):
    return k.post("/api/document-intake/retry-all-failed", headers={"x-peran": peran})


def test_tanpa_izin_modul_tak_ada_yang_disentuh(uji2):
    k, db, catat = uji2
    _doc(db, DOC, REST, "posting_failed")
    _doc(db, DOC2, LEGACY, "posting_failed")
    r = _ulang(k, "KOSONG")
    assert r.status_code == 200, r.text
    assert {d["document_id"] for d in r.json()["denied"]} == {DOC, DOC2}
    assert catat["batch"] == [] and db["_tulis"] == []


def test_campuran_hanya_yang_berizin_direset_dan_dieksekusi(uji2):
    k, db, catat = uji2
    _doc(db, DOC, REST, "posting_failed")      # STAF punya C receipt -> boleh
    _doc(db, DOC2, LEGACY, "posting_failed")   # jalur jurnal legacy, STAF tanpa C journal -> ditolak
    r = _ulang(k, "STAF")
    assert r.status_code == 200, r.text
    assert catat["batch"] == [DOC]
    assert [d["document_id"] for d in r.json()["denied"]] == [DOC2]
    assert len(db["_tulis"]) == 1 and db["_args"][0][1] == [UUID(DOC)]  # UPDATE dibatasi id = ANY(berizin)


def test_berizin_penuh_semua_dieksekusi(uji2):
    k, db, catat = uji2
    _doc(db, DOC, REST, "posting_failed")
    _doc(db, DOC2, LEGACY, "posting_failed")
    r = _ulang(k, "STAF_JURNAL")
    assert r.status_code == 200, r.text
    assert sorted(catat["batch"]) == sorted([DOC, DOC2]) and r.json()["denied"] == []


def test_anggota_nonaktif_403_tanpa_menyentuh(uji2):
    k, db, catat = uji2
    _doc(db, DOC, REST, "posting_failed")
    assert _ulang(k, "NONAKTIF").status_code == 403
    assert catat["batch"] == [] and db["_tulis"] == []


def test_process_anggota_nonaktif_403(uji2, monkeypatch):
    k, db, catat = uji2
    dipanggil = []

    class _Proc:
        def __init__(self, *a, **kw):
            pass

        async def process_next_batch(self, **kw):
            dipanggil.append(kw)
            return {"processed": 0, "failed": 0, "remaining": 0, "details": []}

    import app.services.document_processor as DP
    monkeypatch.setattr(DP, "DocumentProcessor", _Proc)
    assert k.post("/api/document-intake/process", json={}, headers={"x-peran": "NONAKTIF"}).status_code == 403
    assert dipanggil == []
    r = k.post("/api/document-intake/process", json={}, headers={"x-peran": "KOSONG"})
    assert r.status_code == 200, r.text
    assert len(dipanggil) == 1
