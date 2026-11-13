"""BUG-199 (putusan pemilik 13 Nov 2026): izin Penyesuaian Stok PER AKSI di STOCK_ADJUST (posting P, void V, ubah U, hapus D) + aksi_tersedia memuat izin pengguna.
Paritas nyata (peran asli di klon) = uji salinan."""
import asyncio
import inspect
import uuid

import pytest

import app.middleware.permission_middleware as PM
import app.routers.stock_adjustments as SA
from app.services import stok_sesuai as SS

BASE = "/api/stock-adjustments"
SID = uuid.uuid4()


def jalan(aw):
    return asyncio.run(aw)


@pytest.mark.parametrize("metode,jalur,modul,huruf", [
    ("GET", BASE, "stock_adjust", "R"), ("GET", f"{BASE}/{SID}", "stock_adjust", "R"), ("GET", f"{BASE}/{SID}/history", "stock_adjust", "R"),
    ("POST", BASE, "stock_adjust", "C"), ("POST", f"{BASE}/preview", "stock_adjust", "C"),
    ("POST", f"{BASE}/{SID}/post", "stock_adjust", "P"), ("POST", f"{BASE}/{SID}/post/preview", "stock_adjust", "P"),
    ("POST", f"{BASE}/{SID}/void", "stock_adjust", "V"), ("POST", f"{BASE}/{SID}/void/preview", "stock_adjust", "V"),
    ("PATCH", f"{BASE}/{SID}", "stock_adjust", "U"), ("POST", f"{BASE}/{SID}/edit/preview", "stock_adjust", "U"),
    ("DELETE", f"{BASE}/{SID}", "stock_adjust", "D"), ("POST", f"{BASE}/{SID}/delete/preview", "stock_adjust", "D"),
])
def test_peta_izin_rute_per_aksi(metode, jalur, modul, huruf):
    assert PM.izin_untuk_jalur(jalur, metode) == (modul, huruf)


def test_tak_ada_lagi_modul_product_di_rute_penyesuaian():
    for metode, jalur in (("PATCH", f"{BASE}/{SID}"), ("DELETE", f"{BASE}/{SID}"), ("POST", f"{BASE}/{SID}/edit/preview"), ("POST", f"{BASE}/{SID}/delete/preview")):
        assert PM.izin_untuk_jalur(jalur, metode)[0] != "item"


class _Kon:
    pass


@pytest.fixture
def stub(monkeypatch):
    async def posting(conn, tid, sid, bl, **kw):
        return {"blocks": []}

    async def batal(conn, tid, sid, alasan, bl, **kw):
        return {"blocks": []}
    monkeypatch.setattr(SS, "rencana_posting", posting)
    monkeypatch.setattr(SS, "rencana_batal", batal)


SA_POSTED = {"id": SID, "status": "posted"}
SA_DRAF = {"id": SID, "status": "draft"}


def test_aksi_tersedia_tanpa_izin_perilaku_lama(stub):
    a = jalan(SS.aksi_tersedia(_Kon(), "t", SA_DRAF))
    assert [k for k, v in a.items() if v["tersedia"]] == ["posting", "ubah", "hapus"]
    assert list(a) == ["posting", "ubah", "hapus", "batalkan"]


@pytest.mark.parametrize("kunci,kalimat", [("posting", "Anda tidak punya izin untuk memposting penyesuaian stok."), ("ubah", "Anda tidak punya izin untuk mengubah penyesuaian stok."),
                                           ("hapus", "Anda tidak punya izin untuk menghapus penyesuaian stok."), ("batalkan", "Anda tidak punya izin untuk membatalkan penyesuaian stok.")])
def test_tak_berizin_menutup_aksi_dengan_kalimat_indonesia(stub, kunci, kalimat):
    sa = SA_POSTED if kunci == "batalkan" else SA_DRAF
    izin = {"posting": True, "ubah": True, "hapus": True, "batalkan": True, kunci: False}
    a = jalan(SS.aksi_tersedia(_Kon(), "t", sa, izin))
    assert a[kunci] == {"tersedia": False, "alasan": kalimat}
    assert set(a[kunci]) == {"tersedia", "alasan"}                                  # bentuk {tersedia, alasan} tetap


def test_izin_didahulukan_dan_keadaan_tetap_berlaku(stub):
    a = jalan(SS.aksi_tersedia(_Kon(), "t", SA_POSTED, {"posting": False, "ubah": True, "hapus": True, "batalkan": True}))
    assert a["posting"]["alasan"] == "Anda tidak punya izin untuk memposting penyesuaian stok."     # izin didahulukan walau keadaan juga menolak
    assert a["ubah"]["tersedia"] is False and a["ubah"]["alasan"].startswith("Penyesuaian yang sudah diposting")   # izin ada, keadaan menolak
    assert a["batalkan"] == {"tersedia": True, "alasan": None}


def test_izin_aksi_memakai_penentu_rute_yang_sama_dengan_middleware(monkeypatch):
    panggilan = []

    async def palsu(user, jalur, method="GET"):
        panggilan.append((method, jalur[0]))
        return {jalur[0]: method != "DELETE"}
    monkeypatch.setattr(PM, "izin_rute_pengguna", palsu)
    h = jalan(SS.izin_aksi({"user_id": "u", "tenant_id": "t"}, SID))
    assert h == {"posting": True, "batalkan": True, "ubah": True, "hapus": False}
    assert panggilan == [("POST", f"{BASE}/{SID}/post"), ("POST", f"{BASE}/{SID}/void"), ("PATCH", f"{BASE}/{SID}"), ("DELETE", f"{BASE}/{SID}")]


def test_detail_memberi_izin_ke_aksi_tersedia():
    src = inspect.getsource(SA.get_stock_adjustment)
    assert 'SS.aksi_tersedia(conn, ctx["tenant_id"], sa, await SS.izin_aksi(request.state.user, sa["id"]))' in src
