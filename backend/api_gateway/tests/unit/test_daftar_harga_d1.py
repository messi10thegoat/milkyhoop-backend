"""DAFTAR HARGA D1 (putusan pemilik 13 Nov; PETA-be-daftar-harga-20261113): daftar standar SO -- ?aksi= (kedaluwarsa, belum_berlaku, nonaktif, bawaan),
/summary ({k}_count, total_count), urutan stabil (priority, nama, id), pelanggan_count + aksi_tersedia per baris (SATU bentuk), Σ server (total_baris),
dan per baris harga unit_text ("<satuan dasar>" bila unit kosong) + berlaku_text ("Ikut daftar" bila kosong), SATU penentu (services/harga_daftar)."""
import pathlib
from datetime import date
from types import SimpleNamespace

import pytest

from app.middleware import permission_middleware as PM
from app.schemas import price_lists as S
from app.schemas.aksi_tersedia import AksiTersediaPeta
from app.services import harga_daftar as HD

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
RUTE = (APP / "routers" / "price_lists.py").read_text()


# --- teks baris ----------------------------------------------------------------------------------------------------------
def test_berlaku_text_ikut_daftar_bila_kosong_dan_rentang():
    assert HD.berlaku_text(None, None) == "Ikut daftar"
    assert HD.berlaku_text(date(2026, 1, 1), date(2026, 12, 31)) == "1 Jan 2026 – 31 Des 2026"
    assert HD.berlaku_text(date(2026, 1, 1), None) == "Mulai 1 Jan 2026"
    assert HD.berlaku_text(None, date(2026, 12, 31)) == "Sampai 31 Des 2026"


def test_unit_text_satuan_dasar_bila_unit_kosong():
    assert HD.unit_text("Lusin", "Pcs") == "Lusin"
    assert HD.unit_text(None, "Pcs") == "Pcs" and HD.unit_text("  ", "Pcs") == "Pcs"
    assert HD.unit_text(None, None) == "Satuan dasar"


# --- kunci aksi ----------------------------------------------------------------------------------------------------------
def test_kunci_aksi_dan_predikat_satu_sumber():
    assert HD.AKSI == ("kedaluwarsa", "belum_berlaku") and HD.SARING == ("nonaktif", "bawaan")
    assert "end_date < $2::date" in HD.sql_aksi("kedaluwarsa", "p", "$2") and "is_active" in HD.sql_aksi("kedaluwarsa", "p", "$2")
    assert "start_date > $2::date" in HD.sql_aksi("belum_berlaku", "p", "$2")
    assert HD.sql_aksi("nonaktif", "p", "$2") == "(NOT p.is_active)"
    assert HD.sql_aksi("bawaan", "p", "$2") == "(p.is_default AND p.is_active)"
    with pytest.raises(KeyError):
        HD.sql_aksi("ngawur", "p", "$2")


# --- aksi_tersedia -------------------------------------------------------------------------------------------------------
def _r(**kw):
    r = {"is_active": True, "is_default": False}
    r.update(kw)
    return r


def test_aksi_tersedia_daftar_aktif_biasa():
    a = HD.aksi_tersedia(_r(), 0)
    assert list(a) == ["ubah", "jadikan_bawaan", "nonaktifkan", "aktifkan", "hapus"]
    assert a["ubah"]["tersedia"] and a["jadikan_bawaan"]["tersedia"] and a["nonaktifkan"]["tersedia"] and a["hapus"]["tersedia"]
    assert not a["aktifkan"]["tersedia"] and a["aktifkan"]["alasan"] == "Daftar ini sudah aktif."


def test_aksi_tersedia_hapus_ditolak_bila_dipakai_pelanggan_nonaktif_tetap_boleh():
    a = HD.aksi_tersedia(_r(), 3)
    assert not a["hapus"]["tersedia"] and "3 pelanggan" in a["hapus"]["alasan"]
    assert a["nonaktifkan"]["tersedia"]                      # nonaktifkan boleh (peringatan di pratinjau D2)


def test_aksi_tersedia_bawaan_dan_nonaktif():
    b = HD.aksi_tersedia(_r(is_default=True), 0)
    assert not b["jadikan_bawaan"]["tersedia"] and b["jadikan_bawaan"]["alasan"] == "Daftar ini sudah bawaan."
    n = HD.aksi_tersedia(_r(is_active=False), 0)
    assert n["aktifkan"]["tersedia"] and not n["nonaktifkan"]["tersedia"] and not n["jadikan_bawaan"]["tersedia"]
    assert n["jadikan_bawaan"]["alasan"] == "Daftar nonaktif tidak bisa jadi bawaan."


def test_position_text():
    assert HD.position_text(True, False, "aktif") == "Aktif"
    assert HD.position_text(True, True, "aktif") == "Aktif · Bawaan"
    assert HD.position_text(False, False, "nonaktif") == "Nonaktif"
    assert HD.position_text(True, False, "kedaluwarsa") == "Kedaluwarsa"


# --- skema + rute --------------------------------------------------------------------------------------------------------
def test_skema_memuat_medan_standar():
    for kelas in (S.PriceListListItem, S.PriceListDetail):
        f = kelas.model_fields
        assert "aksi_tersedia" in f and "pelanggan_count" in f and "position_text" in f, kelas
        assert f["aksi_tersedia"].annotation == AksiTersediaPeta, kelas      # satu bentuk (penjaga AST)
    assert "total_baris" in S.PriceListListResponse.model_fields
    f = S.PriceListItemResponse.model_fields
    assert "unit_text" in f and "berlaku_text" in f


def test_rute_summary_sebelum_detail_dan_aksi_di_daftar():
    assert RUTE.index('@router.get("/summary")') < RUTE.index('@router.get("/{price_list_id}"')   # bukan ditangkap sebagai id
    i = RUTE.index("async def list_price_lists")
    fn = RUTE[i:RUTE.index("@router.get", i + 10)]
    assert "aksi: Optional[str]" in fn and "HD.sql_aksi(" in fn and "p.priority, p.name, p.id" in fn
    assert "pelanggan_count" in fn and "HD.aksi_tersedia(" in fn and "total_baris" in fn


def test_summary_memakai_predikat_sama_dengan_daftar():
    i = RUTE.index('@router.get("/summary")')
    fn = RUTE[i:RUTE.index("@router.get", i + 10)]
    for k in ("kedaluwarsa", "belum_berlaku", "nonaktif"):
        assert f"{k}_count" in fn
    assert "HD.sql_aksi(" in fn and "total_count" in fn and "tenant_id = $1" in fn


def test_baris_harga_memuat_teks_di_detail_dan_respons_tulis():
    i = RUTE.index("async def get_price_list(")
    fn = RUTE[i:RUTE.index("@router.post", i)]
    assert "HD.unit_text(" in fn and "HD.berlaku_text(" in fn and "HD.aksi_tersedia(" in fn
    assert RUTE.count("HD.teks_baris(") >= 2             # respons tambah + ubah baris, SATU penentu


def test_izin_summary_baca():
    assert PM.PermissionMiddleware(lambda *a: None, False)._find_permission("/api/price-lists/summary", "GET") == ("price_list", "R")


class _CS:
    def __init__(self):
        self.sql = []

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        return {**{f"{k}_count": 0 for k in HD.SEMUA_KUNCI}, "total_count": 0}

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        return 0

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        return []


class _Kolam:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        c = self.c

        class _A:
            async def __aenter__(s):
                return c

            async def __aexit__(s, *a):
                return False
        return _A()


@pytest.mark.asyncio
async def test_summary_dan_daftar_memakai_predikat_aksi_yang__sama(monkeypatch):
    from app.routers import price_lists as PL
    c = _CS()

    async def gp():
        return _Kolam(c)

    async def hari(conn, tid):
        return date(2026, 11, 13)
    monkeypatch.setattr(PL, "get_pool", gp)
    monkeypatch.setattr(PL, "tanggal_dokumen", hari)
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": "t1", "user_id": None}))
    await PL.price_lists_summary(req)
    for k in HD.SEMUA_KUNCI:
        assert f"FILTER (WHERE {HD.sql_aksi(k, 'p', '$2')}) AS {k}_count" in c.sql[0], k     # predikat summary == ?aksi= (tanpa tambahan)
    c.sql.clear()
    await PL.list_price_lists(req, skip=0, limit=20, search=None, is_active=None, aksi="kedaluwarsa")
    assert any(HD.sql_aksi("kedaluwarsa", "p", "$2") in q for q in c.sql)
