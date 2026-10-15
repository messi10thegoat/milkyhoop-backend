"""LAMPIRAN-NK-QUOTE (15 Okt 2026, putusan MASTER #5) -- lampiran nota kredit & penawaran lewat SATU pabrik rute.

Yang dijaga: rute /{id}/attachments* dimenangkan pabrik (bukan /{id} modul) lewat TABEL RUTE app; izin per metode (U/R/R/D);
SQL tiap spesifikasi bertenant per alias + entity_type tetap; induk tenant lain -> 404 tanpa kueri lain; daftar ber-url download
modulnya; lepas = DELETE tautan + audit LAMPIRAN_DILEPAS di transaksi yang sama; unggah sukses -> documents + tautan, kuota dihitung
di bawah advisory lock; keluarga /api/lampiran: grup penawaran/NK kini berurl."""
import asyncio
import re
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.middleware import permission_middleware as pm
from app.routers import lampiran_induk as LI
from app.routers import sales_order_lampiran as SL
from tests.unit.test_lampiran_so_keluarga import _pemenang

T = "kaos-biru-konveksi"
DOC, AID = uuid.uuid4(), uuid.uuid4()
SPEC = [LI.SPEC_NOTA_KREDIT, LI.SPEC_PENAWARAN]


@pytest.mark.parametrize("s", SPEC, ids=lambda s: s.entitas)
def test_rute_dimenangkan_pabrik(s):
    base = f"/api/{s.modul_url}/{DOC}/attachments"
    for path, metode, nama in ((base, "POST", "unggah"), (base, "GET", "daftar"),
                               (f"{base}/{AID}/download", "GET", "unduh"), (f"{base}/{AID}", "DELETE", "lepas")):
        r = _pemenang(path, metode)
        assert r is not None, (path, metode)
        assert r.endpoint.__module__.rsplit(".", 1)[-1] == "lampiran_induk" and r.endpoint.__name__ == f"{nama}_lampiran_{s.entitas}"


@pytest.mark.parametrize("s,modul", [(LI.SPEC_NOTA_KREDIT, "credit_note"), (LI.SPEC_PENAWARAN, "quote")], ids=["nk", "quote"])
def test_izin_per_metode(s, modul):
    base = f"/api/{s.modul_url}/{DOC}/attachments"
    assert pm.izin_untuk_jalur(base, "POST") == (modul, "U")
    assert pm.izin_untuk_jalur(base, "GET") == (modul, "R")
    assert pm.izin_untuk_jalur(f"{base}/{AID}/download", "GET") == (modul, "R")
    assert pm.izin_untuk_jalur(f"{base}/{AID}", "DELETE") == (modul, "D")


@pytest.mark.parametrize("s", SPEC, ids=lambda s: s.entitas)
def test_sql_bertenant_dan_entitas_tetap(s):
    for k, sql in LI.sql_spec(s).items():
        for tabel, alias in re.findall(r"\b(?:FROM|JOIN|USING)\s+(\w+)\s+(\w+)", sql):
            if alias.upper() in {"WHERE", "ON", "SET"}:
                assert re.search(r"\btenant_id\s*=\s*\$\d", sql), (k, tabel)
            else:
                assert re.search(rf"\b{alias}\.tenant_id\s*=\s*\$\d", sql), (k, tabel, alias)
        if "document_attachments" in sql:
            assert f"'{s.entitas}'" in sql, k


def test_keluarga_grup_penawaran_dan_nk_kini_berurl():
    assert SL.JENIS_KELUARGA["quote"] == ("quotes", None) and SL.JENIS_KELUARGA["credit_note"] == ("credit-notes", None)
    assert pm.izin_untuk_jalur(SL.jalur_izin_jenis("quote")) == ("quote", "R")
    assert pm.izin_untuk_jalur(SL.jalur_izin_jenis("credit_note")) == ("credit_note", "R")


# ------------------------------------------------------------------ handler (DB palsu)
class Txn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class Conn:
    def __init__(self, ada=True, rows=(), lepas=True):
        self.ada, self.rows, self.lepas = ada, list(rows), lepas
        self.log = []

    def transaction(self):
        return Txn()

    async def execute(self, sql, *a):
        self.log.append((sql, a))

    async def fetchrow(self, sql, *a):
        self.log.append((sql, a))
        if "AS nomor FROM" in sql:
            return {"id": a[0], "nomor": "CN-1"} if (self.ada and a[1] == T) else None
        if "d.file_path" in sql:
            return {"file_name": "a.pdf", "file_path": "k", "file_type": "application/pdf", "storage_type": "s3"} if self.rows else None
        raise AssertionError(sql[:60])

    async def fetchval(self, sql, *a):
        self.log.append((sql, a))
        if "DELETE FROM document_attachments" in sql:
            return uuid.uuid4() if self.lepas else None
        if "count(*)" in sql or "LAMPIRAN" in sql or "deleted_at IS NULL" in sql and "checksum_sha256" not in sql:
            return 0
        if "checksum_sha256" in sql and sql.lstrip().startswith("SELECT"):
            return None
        if "INSERT INTO documents" in sql:
            return AID
        if "SELECT 1 FROM document_attachments" in sql:
            return None
        return 0

    async def fetch(self, sql, *a):
        self.log.append((sql, a))
        return self.rows


class Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


def req():
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": str(uuid.uuid4()), "tenant_id": T}))


def endpoint(s, nama):
    return next(r.endpoint for r in LI.buat_router(s).routes if r.endpoint.__name__ == f"{nama}_lampiran_{s.entitas}")


@pytest.fixture
def pasang(monkeypatch):
    def _p(conn):
        async def gp():
            return SimpleNamespace(acquire=lambda: Acq(conn))
        monkeypatch.setattr(LI, "get_pool", gp)
        ev = []

        async def riwayat(conn, tid, et, eid, nomor, event, uid, ringkas, meta=None, source="api"):
            ev.append((et, event, ringkas))
        from app.services import so_riwayat
        monkeypatch.setattr(so_riwayat, "catat_riwayat", riwayat)
        return ev
    return _p


@pytest.mark.parametrize("s", SPEC, ids=lambda s: s.entitas)
def test_induk_tenant_lain_404_tanpa_kueri_lampiran(pasang, s):
    c = Conn(ada=False)
    pasang(c)
    with pytest.raises(HTTPException) as e:
        asyncio.run(endpoint(s, "daftar")(req(), DOC))
    assert e.value.status_code == 404 and s.label in e.value.detail
    assert not any("document_attachments" in q for q, _ in c.log)


def test_daftar_berurl_modul(pasang):
    from datetime import datetime
    c = Conn(rows=[{"id": AID, "file_name": "bukti.pdf", "file_size": 10, "file_type": "application/pdf", "storage_type": "s3",
                    "deleted_at": None, "uploaded_at": datetime(2026, 10, 15)}])
    pasang(c)
    out = asyncio.run(endpoint(LI.SPEC_PENAWARAN, "daftar")(req(), DOC))
    assert out["data"][0]["url"] == f"/api/quotes/{DOC}/attachments/{AID}/download" and out["kuota"]["terpakai"] == 1


def test_lepas_menulis_audit_di_transaksi_sama(pasang):
    c = Conn()
    ev = pasang(c)
    asyncio.run(endpoint(LI.SPEC_NOTA_KREDIT, "lepas")(req(), DOC, AID))
    assert ev == [("credit_notes", "LAMPIRAN_DILEPAS", "Lampiran nota kredit dilepas (CN-1)")]
    i_kunci = next(i for i, (q, _) in enumerate(c.log) if "pg_advisory_xact_lock" in q)
    i_hapus = next(i for i, (q, _) in enumerate(c.log) if "DELETE FROM document_attachments" in q)
    assert i_kunci < i_hapus


def test_lepas_tak_ada_404_tanpa_audit(pasang):
    c = Conn(lepas=False)
    ev = pasang(c)
    with pytest.raises(HTTPException) as e:
        asyncio.run(endpoint(LI.SPEC_NOTA_KREDIT, "lepas")(req(), DOC, AID))
    assert e.value.status_code == 404 and ev == []


def test_unggah_menulis_dokumen_dan_tautan_dengan_entitas_spesifikasi(pasang, monkeypatch):
    c = Conn()
    pasang(c)
    disimpan = []

    async def simpan(storage, kunci, isi):
        disimpan.append(kunci)
    monkeypatch.setattr(LI, "simpan_objek_unggahan", simpan)
    monkeypatch.setattr(LI, "get_storage_service", lambda: object())

    class F:
        filename, content_type = "bukti.png", "image/png"

        async def read(self):
            return b"\x89PNG\r\n\x1a\n" + b"0" * 64
    out = asyncio.run(endpoint(LI.SPEC_PENAWARAN, "unggah")(req(), DOC, [F()]))
    assert out["hasil"][0]["ok"] is True and disimpan and disimpan[0].startswith(f"{T}/uploads/lampiran/")
    taut = [a for q, a in c.log if "INSERT INTO document_attachments" in q]
    assert len(taut) == 1 and "'quote'" in [q for q, _ in c.log if "INSERT INTO document_attachments" in q][0]


def test_penanda():
    assert LI.PENANDA_LAMPIRAN_INDUK == "lampiran-induk-nk-quote-w20261015"
