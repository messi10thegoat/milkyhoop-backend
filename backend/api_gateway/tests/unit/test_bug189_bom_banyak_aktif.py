"""BUG-189 (putusan pemilik: ikut Accurate): banyak BOM AKTIF per produk, satu UTAMA (aktif ∧ is_current), WO memilih. Aktivasi tak menyentuh BOM lain (aktivasi pertama otomatis utama);
POST /{id}/jadikan-utama eksplisit; mengusangkan UTAMA mempromosikan BOM aktif terbaru (deterministik, teks di respons); draf/duplikat is_current=false; list/detail memuat utama/status_text/label."""
import asyncio
import inspect
import uuid
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.routers.bom as B
import app.routers.production as PR
from app.middleware import permission_middleware as pm
from app.schemas import bom as SB
from app.services import bom_status as BS

T = "pt-busana-indah-semesta"
BID, PID, NID = (uuid.uuid4() for _ in range(3))


def test_peta_status_utama_label():
    assert BS.STATUS_TEKS == {"draft": "Draf", "active": "Aktif", "obsolete": "Usang"} and BS.status_text("zzz") is None and BS.status_text(None) is None
    assert BS.utama("active", True) is True and BS.utama("active", False) is False and BS.utama("draft", True) is False and BS.utama("obsolete", True) is False
    assert BS.label(2, D("52300"), "active", True) == "v2 · Rp 52.300 · utama"
    assert BS.label(1, D("50300"), "active", False) == "v1 · Rp 50.300"
    assert BS.label(3, D("55000"), "draft", True) == "v3 · Rp 55.000 · draf"          # is_current mentah pada draf tak bermakna
    assert BS.label(4, D("0"), "obsolete", False) == "v4 · Rp 0 · usang"


class Kon:
    """Fake conn: jawaban per potongan SQL (urut pertama yang cocok); catat semua eksekusi."""

    def __init__(self, jawab):
        self.jawab, self.sql = jawab, []

    def transaction(self):
        class Tx:
            async def __aenter__(s):
                return None

            async def __aexit__(s, *e):
                return False
        return Tx()

    def _j(self, s):
        for kunci, hasil in self.jawab:
            if kunci in s:
                return hasil
        return None

    async def fetchrow(self, sql, *a):
        s = " ".join(sql.split())
        self.sql.append((s, a))
        return self._j(s)

    async def fetchval(self, sql, *a):
        s = " ".join(sql.split())
        self.sql.append((s, a))
        return self._j(s)

    async def execute(self, sql, *a):
        self.sql.append((" ".join(sql.split()), a))


class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


def _req():
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    r.state.user = {"tenant_id": T, "user_id": str(uuid.uuid4())}
    return r


def _jalan(monkeypatch, fn, kon, *a):
    async def gp():
        return SimpleNamespace(acquire=lambda: _Acq(kon))

    async def tgl(conn, tid):
        import datetime
        return datetime.date(2026, 11, 12)
    monkeypatch.setattr(B, "get_pool", gp)
    monkeypatch.setattr(B, "tanggal_dokumen", tgl)
    return asyncio.run(fn(_req(), BID, *a))


def _ups(kon, kata):
    return [(s, a) for s, a in kon.sql if s.startswith("UPDATE") and kata in s]


def test_aktivasi_pertama_otomatis_utama_dan_tak_menyentuh_lain(monkeypatch):
    k = Kon([("SELECT product_id, status", {"product_id": PID, "status": "draft"}), ("AND status = 'active' AND is_current LIMIT 1", None)])
    r = _jalan(monkeypatch, B.activate_bom, k)
    up = _ups(k, "SET status = 'active'")
    assert len(up) == 1 and up[0][1][2] is True and r["data"] == {"id": str(BID), "utama": True} and "utama" in r["message"]
    assert [s for s, _ in k.sql if s.startswith("UPDATE") and "SET status = 'active'" not in s] == []             # BOM lain TAK disentuh


def test_aktivasi_kedua_aktif_tapi_bukan_utama(monkeypatch):
    k = Kon([("SELECT product_id, status", {"product_id": PID, "status": "draft"}), ("AND status = 'active' AND is_current LIMIT 1", 1)])
    r = _jalan(monkeypatch, B.activate_bom, k)
    up = _ups(k, "SET status = 'active'")
    assert len(up) == 1 and up[0][1][2] is False and r["data"]["utama"] is False and "bukan utama" in r["message"]
    assert not any("obsolete" in s or "is_current = false" in s for s, _ in k.sql)                                # tak mengusangkan / menurunkan BOM lain


def test_jadikan_utama_tidak_ada_dan_bukan_aktif(monkeypatch):
    with pytest.raises(HTTPException) as e:
        _jalan(monkeypatch, B.jadikan_utama, Kon([]))
    assert e.value.status_code == 404
    k = Kon([("SELECT product_id, bom_code, status, is_current", {"product_id": PID, "bom_code": "B2", "status": "draft", "is_current": False})])
    with pytest.raises(HTTPException) as e:
        _jalan(monkeypatch, B.jadikan_utama, k)
    assert e.value.status_code == 400 and e.value.code == "BOM_BUKAN_AKTIF"
    assert not _ups(k, "is_current")


def test_jadikan_utama_menukar_tepat_satu_dan_noop_bila_sudah(monkeypatch):
    lama = uuid.uuid4()
    k = Kon([("SELECT product_id, bom_code, status, is_current", {"product_id": PID, "bom_code": "B2", "status": "active", "is_current": False}), ("AND status = 'active' AND is_current ORDER BY created_at", lama)])
    r = _jalan(monkeypatch, B.jadikan_utama, k)
    ups = [s for s, _ in k.sql if s.startswith("UPDATE")]
    assert len(ups) == 2 and "SET is_current = false" in ups[0] and "id != $3" in ups[0] and "SET is_current = true" in ups[1]      # utama lama turun dulu, lalu ini naik
    assert r["data"] == {"id": str(BID), "utama": True, "utama_sebelumnya": str(lama)} and "B2" in r["message"]
    k2 = Kon([("SELECT product_id, bom_code, status, is_current", {"product_id": PID, "bom_code": "B2", "status": "active", "is_current": True}), ("AND status = 'active' AND is_current ORDER BY created_at", None)])
    r2 = _jalan(monkeypatch, B.jadikan_utama, k2)
    assert not _ups(k2, "is_current") and r2["data"]["utama"] is True and "sudah menjadi BOM utama" in r2["message"]


def test_usangkan_utama_mempromosikan_aktif_terbaru_dengan_teks(monkeypatch):
    k = Kon([("FROM bill_of_materials WHERE tenant_id = $1 AND id = $2 AND status = 'active' FOR UPDATE", {"product_id": PID, "bom_code": "BOM-V2", "is_current": True}),
             ("ORDER BY effective_date DESC NULLS LAST, version DESC, created_at DESC, id LIMIT 1", {"id": NID, "bom_code": "BOM-V1", "version": 1})])
    r = _jalan(monkeypatch, B.obsolete_bom, k)
    assert r["data"] == {"id": str(BID), "utama_baru": str(NID)} and "BOM utama sekarang: BOM-V1 (v1)" in r["message"] and "BOM-V2 diusangkan" in r["message"]
    assert any(s.startswith("UPDATE") and "SET is_current = true" in s and a[1] == NID for s, a in k.sql)
    # usangkan BUKAN utama: tak ada promosi
    k2 = Kon([("FOR UPDATE", {"product_id": PID, "bom_code": "BOM-V1", "is_current": False})])
    r2 = _jalan(monkeypatch, B.obsolete_bom, k2)
    assert r2["data"]["utama_baru"] is None and not _ups(k2, "is_current = true") and "utama sekarang" not in r2["message"]
    # usangkan utama TANPA penerus
    k3 = Kon([("FOR UPDATE", {"product_id": PID, "bom_code": "BOM-V2", "is_current": True})])
    assert "tidak punya BOM aktif lagi" in _jalan(monkeypatch, B.obsolete_bom, k3)["message"]
    with pytest.raises(HTTPException):
        _jalan(monkeypatch, B.obsolete_bom, Kon([]))


def test_draf_dan_duplikat_tak_pernah_utama_dan_resolver_deterministik():
    assert "false)" in inspect.getsource(B.create_bom).split("INSERT INTO bill_of_materials")[1].split("RETURNING id")[0]
    assert "'draft', false, $4" in inspect.getsource(B.duplicate_bom)
    assert "ORDER BY effective_date DESC NULLS LAST, version DESC, created_at DESC LIMIT 1" in inspect.getsource(B.get_product_bom)


def test_wo_menerima_bom_aktif_mana_pun_bukan_hanya_utama():
    s = inspect.getsource(PR.create_production_order)
    assert "bom.status = 'active'" in s and "is_current" not in s                      # dikunci: WO memilih; tak bergantung is_current


def test_list_dan_detail_memuat_utama_status_text_label():
    for kls in (SB.BOMListItem, SB.BOMDetail):
        assert "utama" in kls.model_fields and "status_text" in kls.model_fields
    assert "label" in SB.BOMListItem.model_fields
    s = inspect.getsource(B.list_boms)
    assert "BS.utama(" in s and "BS.status_text(" in s and "BS.label(" in s
    d = inspect.getsource(B.get_bom)
    assert "BS.utama(" in d and "BS.status_text(" in d


def test_izin_jadikan_utama_U_modul_bom_dan_inventaris():
    assert pm.izin_untuk_jalur(f"/api/bom/{BID}/jadikan-utama", "POST") == ("bom", "U")
    assert pm.izin_untuk_jalur("/api/bom", "POST") == ("bom", "C")
    import pathlib
    inv = (pathlib.Path(__file__).parent / "inventaris_rute_tulis.txt").read_text().splitlines()
    assert "POST /api/bom/{bom_id}/jadikan-utama" in inv
