"""K-1 (16 Nov 2026): daftar STANDAR SO master Kategori barang, TANPA migrasi -- GET /api/items/categories?bentuk=objek (+ /summary). Bentuk lama (array nama) TIDAK berubah.
Jumlah barang lewat teks kategori (lower), hapus tersedia hanya bila 0 barang AKTIF (aturan sama dgn DELETE), kunci aksi tanpa_barang, cari, paginasi."""
import asyncio
import pathlib
import re
from datetime import datetime

import pytest

from app.services import kategori_daftar as KD

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


def r(i, nama, n, a, aktif=True, bawaan=False):
    return {"id": f"c-{i}", "name": nama, "created_at": datetime(2026, 11, 1, 9, 0), "is_active": aktif, "is_default": bawaan, "parent_id": None, "parent_name": None, "jumlah_anak": 0, "description": None,
            "jumlah_barang": n, "jumlah_aktif": a}


class Konn:
    def __init__(self, rows, total=None, ring=None):
        self.rows, self.total, self.ring, self.panggilan = rows, len(rows) if total is None else total, ring, []

    async def fetch(self, sql, *a):
        self.panggilan.append((sql, a))
        assert sql == KD.SQL_DAFTAR
        return self.rows

    async def fetchval(self, sql, *a):
        self.panggilan.append((sql, a))
        assert sql == KD.SQL_TOTAL
        return self.total

    async def fetchrow(self, sql, *a):
        assert sql == KD.SQL_RINGKASAN
        return self.ring


def jalan(k, **kw):
    return asyncio.run(KD.muat_daftar(k, "t1", **kw))


def test_baris_jumlah_keadaan_teks_dan_aksi_tersedia_hapus():
    d = jalan(Konn([r(1, "Minuman", 12, 10), r(2, "Kosong", 0, 0), r(3, "Semua aktif", 4, 4), r(4, "Nonaktif saja", 3, 0)]))
    a, b, c, e = d["items"]
    assert (a["jumlah_barang"], a["jumlah_aktif"], a["keadaan"], a["position_text"], a["aksi_semua"]) == (12, 10, "terpakai", "12 barang · 10 aktif", [])
    assert a["aksi_tersedia"]["hapus"] == {"tersedia": False, "alasan": "Kategori 'Minuman' dipakai 10 barang aktif; pindahkan barangnya dulu."}   # kalimat = pratinjau hapus
    assert list(a["aksi_tersedia"]) == ["ubah", "nonaktifkan", "aktifkan", "jadikan_bawaan", "atur_induk", "hapus"] and a["aksi_tersedia"]["ubah"]["tersedia"] and a["aksi_tersedia"]["nonaktifkan"]["tersedia"]
    assert a["aksi_tersedia"]["aktifkan"] == {"tersedia": False, "alasan": "Kategori sudah aktif."}
    assert (b["keadaan"], b["position_text"], b["aksi"], b["aksi_semua"], b["aksi_tersedia"]["hapus"]) == ("kosong", "Belum ada barang", "tanpa_barang", ["tanpa_barang"], {"tersedia": True, "alasan": None})
    assert (c["position_text"], c["aksi_tersedia"]["hapus"]["tersedia"]) == ("4 barang", False)
    assert (e["position_text"], e["aksi_tersedia"]["hapus"]["tersedia"], e["aksi"]) == ("3 barang · 0 aktif", True, None)   # hanya barang nonaktif: hapus boleh (aturan DELETE)
    assert a["id"] == "c-1" and a["created_at"] == "2026-11-01T09:00:00"


def test_paginasi_total_has_more_dan_jepit_limit():
    k = Konn([r(1, "A", 1, 1)], total=500)
    d = jalan(k, limit=999, offset=-5)
    assert (d["total"], d["limit"], d["offset"], d["has_more"]) == (500, KD.BATAS_MAKS, 0, True)
    sql, a = k.panggilan[-1]
    assert a == ("t1", None, None, None, None, KD.BATAS_MAKS, 0)
    assert jalan(Konn([], total=3), limit=50, offset=0)["has_more"] is False


def test_cari_dan_kunci_aksi_diteruskan_aksi_tak_dikenal_ditolak():
    k = Konn([])
    jalan(k, cari="  min  ", kunci_aksi="tanpa_barang")
    assert k.panggilan[-1][1][:3] == ("t1", "min", "tanpa_barang")
    jalan(k, kunci_aksi="nonaktif")
    assert k.panggilan[-1][1][2] == "nonaktif"
    with pytest.raises(ValueError):
        jalan(Konn([]), kunci_aksi="xyz")


def test_ringkasan_dan_teks_barang_tanpa_kategori():
    ring = {"total": 5, "nonaktif": 1, "tanpa_barang": 2, "barang_tanpa_kategori": 32, "barang_total": 40}
    s = asyncio.run(KD.ringkasan(Konn([], ring=ring), "t1"))
    assert s == {"total_count": 5, "nonaktif_count": 1, "tanpa_barang_count": 2, "barang_tanpa_kategori_count": 32, "barang_total": 40, "barang_tanpa_kategori_text": "32 dari 40 barang belum berkategori"}
    s0 = asyncio.run(KD.ringkasan(Konn([], ring={"total": 1, "nonaktif": 0, "tanpa_barang": 0, "barang_tanpa_kategori": 0, "barang_total": 3}), "t1"))
    assert s0["barang_tanpa_kategori_text"] is None


def test_sql_berpagar_tenant_hitung_lewat_teks_tanpa_barang_terhapus_hanya_baca():
    for q in (KD.SQL_DAFTAR, KD.SQL_TOTAL):
        assert "c.tenant_id = $1" in q and "p.tenant_id = c.tenant_id" in q and "lower(p.kategori) = lower(c.name)" in q and "p.deleted_at IS NULL" in q
        assert "HAVING ($3::text IS DISTINCT FROM 'tanpa_barang' OR COUNT(p.id) = 0)" in q and "($3::text IS DISTINCT FROM 'nonaktif' OR NOT c.is_active)" in q
    assert "ORDER BY lower(c.name), c.id LIMIT $6 OFFSET $7" in KD.SQL_DAFTAR and "COUNT(p.id) FILTER (WHERE p.status = 'active')" in KD.SQL_DAFTAR
    assert "tenant_id = $1" in KD.SQL_RINGKASAN and "deleted_at IS NULL" in KD.SQL_RINGKASAN
    for q in (KD.SQL_DAFTAR, KD.SQL_TOTAL, KD.SQL_RINGKASAN):
        assert not re.search(r"\b(INSERT|UPDATE|DELETE)\b", q, re.I)


def test_rute_bentuk_lama_utuh_bentuk_objek_summary_izin_dan_422():
    src = (APP / "routers" / "items.py").read_text()
    assert '@router.get("/items/categories/summary")' in src and 'if bentuk == "objek":' in src and "KD.muat_daftar(conn, tenant_id, search, aksi, limit, offset, tingkat if isinstance(tingkat, str) else None" in src
    lama = src.split('if bentuk == "objek":')[1].split("@router.post")[0]
    assert "SELECT name" in lama and "CategoryListResponse(success=True, categories=categories)" in lama   # bentuk lama TAK berubah
    assert "raise HTTPException(status_code=422, detail=str(e))" in src and "except HTTPException:\n        raise\n    except Exception as e:\n        logger.error(f\"Error listing categories" in src
    izin = (APP / "middleware" / "permission_middleware.py").read_text()
    assert '(r"^/api/items/categories/summary$", ["GET"], "item", "R")' in izin


def test_nonaktif_dan_bawaan_keadaan_teks_aksi():
    d = jalan(Konn([r(1, "Lama", 5, 0, aktif=False), r(2, "Umum", 7, 7, bawaan=True)]))
    n, u = d["items"]
    assert (n["keadaan"], n["position_text"], n["aksi_semua"], n["is_active"]) == ("nonaktif", "Nonaktif · 5 barang · 0 aktif", ["nonaktif"], False)
    assert n["aksi_tersedia"]["aktifkan"]["tersedia"] and n["aksi_tersedia"]["nonaktifkan"] == {"tersedia": False, "alasan": "Kategori sudah nonaktif."}
    assert (u["position_text"], u["is_default"], u["aksi_tersedia"]["nonaktifkan"]["tersedia"]) == ("Bawaan · 7 barang", True, False)
    assert "bawaan" in u["aksi_tersedia"]["nonaktifkan"]["alasan"]
