"""K-4 (17 Nov 2026; V498 parent_id): sub kategori SATU tingkat -- buat dengan induk, atur induk, daftar (tingkat/parent_name/jumlah_anak, filter), blok hapus/gabung bila punya sub.
Label barang TETAP nama kategorinya (nama kategori unik per usaha; indeks V257), jadi atur induk tak menyentuh products."""
import asyncio
import pathlib

import pytest

from app.services import kategori_aksi as KA
from app.services import kategori_daftar as KD

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


def k(nama="Minuman", jb=12, ja=10, aktif=True, bawaan=False, i="c-1", parent=None, pname=None, anak=0):
    return {"id": i, "name": nama, "is_active": aktif, "is_default": bawaan, "parent_id": parent, "parent_name": pname, "jumlah_anak": anak, "description": None,
            "created_at": None, "updated_at": None, "jumlah_barang": jb, "jumlah_aktif": ja}


def kode(r):
    return [b["code"] for b in r["blocks"]]


def test_rencana_buat_dengan_induk():
    induk = k("Minuman", i="c-1")
    r = KA.rencana_buat("Teh", False, induk, True)
    assert r["ok"] and r["rencana"] == {"nama": "Teh", "induk": "Minuman", "induk_id": "c-1"}
    assert KA.rencana_buat("Teh", False)["rencana"] == {"nama": "Teh", "induk": None, "induk_id": None}
    assert kode(KA.rencana_buat("Teh", False, None, True)) == ["KATEGORI_INDUK_TAK_ADA"]
    assert kode(KA.rencana_buat("Teh", False, k("Sub", parent="c-0", pname="Atas"), True)) == ["KATEGORI_INDUK_BUKAN_TINGKAT_ATAS"]
    assert kode(KA.rencana_buat("Teh", False, k("Lama", aktif=False), True)) == ["KATEGORI_INDUK_NONAKTIF"] and kode(KA.rencana_buat("Teh", True, induk, True)) == ["KATEGORI_GANDA"]


def test_rencana_induk():
    atas, sub = k("Teh", i="c-2"), k("Hijau", i="c-3", parent="c-1", pname="Minuman")
    induk = k("Minuman", i="c-1")
    r = KA.rencana_induk(atas, induk, True)
    assert r["ok"] and r["rencana"]["induk"] == "Minuman" and r["peringatan"] == ["'Teh' menjadi sub kategori dari 'Minuman'. Barangnya tetap berkategori 'Teh'."]
    assert kode(KA.rencana_induk(atas, atas, True)) == ["KATEGORI_INDUK_DIRI_SENDIRI"]
    assert kode(KA.rencana_induk(k("Teh", i="c-2", anak=2), induk, True)) == ["KATEGORI_PUNYA_ANAK"]          # satu tingkat: yang punya sub tak bisa menjadi sub
    assert kode(KA.rencana_induk(atas, sub, True)) == ["KATEGORI_INDUK_BUKAN_TINGKAT_ATAS"] and kode(KA.rencana_induk(sub, induk, True)) == ["INDUK_TIDAK_BERUBAH"]
    r2 = KA.rencana_induk(sub, None, False)
    assert r2["ok"] and r2["rencana"]["induk"] is None and kode(KA.rencana_induk(atas, None, False)) == ["INDUK_TIDAK_BERUBAH"]
    assert kode(KA.rencana_induk(None, induk, True)) == ["KATEGORI_TAK_ADA"] and kode(KA.rencana_induk(atas, k("Nonaktif", aktif=False, i="c-9"), True)) == ["KATEGORI_INDUK_NONAKTIF"]


def test_gabung_dan_status_dengan_sub():
    assert kode(KA.rencana_gabung(k("Induk", anak=3, i="c-1"), k("Lain", i="c-2"))) == ["KATEGORI_PUNYA_ANAK"]
    r = KA.rencana_status(k("Induk", anak=3), False)
    assert r["ok"] and any("3 sub kategori; sub kategorinya tetap aktif" in w for w in r["peringatan"])
    assert not any("sub kategori" in w for w in KA.rencana_status(k("Induk", anak=0), False)["peringatan"])


class Konn:
    def __init__(self):
        self.sql = []

    async def execute(self, sql, *a):
        self.sql.append((sql, a))
        return "UPDATE 1"

    async def fetchval(self, sql, *a):
        self.sql.append((sql, a))
        return 2


def test_tulis_induk_hanya_registry_tanpa_menyentuh_products_dan_audit():
    c = Konn()
    asyncio.run(KA.tulis_induk(c, "t1", "u1", k("Teh", i="c-2"), k("Minuman", i="c-1")))
    assert c.sql[1][0].startswith("UPDATE item_categories SET parent_id = $3") and c.sql[1][1] == ("t1", "c-2", "c-1")
    assert not any(s.startswith("UPDATE products") for s, _ in c.sql) and c.sql[2][1][0] == "ITEM_CATEGORY_PARENT_SET"
    c2 = Konn()
    asyncio.run(KA.tulis_induk(c2, "t1", "u1", k("Teh", i="c-2", parent="c-1", pname="Minuman"), None))
    assert c2.sql[1][1] == ("t1", "c-2", None) and '"induk_lama": "Minuman"' in c2.sql[2][1][5]
    assert asyncio.run(KA.jumlah_anak_by_nama(Konn(), "t1", "Minuman")) == 2


def test_daftar_baris_tingkat_parent_name_jumlah_anak_aksi_dan_teks():
    d = KD.baris(k("Minuman", jb=0, ja=0, anak=2))
    assert (d["tingkat"], d["jumlah_anak"], d["parent_name"], d["position_text"]) == ("induk", 2, None, "Belum ada barang · 2 sub")
    assert d["aksi_tersedia"]["atur_induk"] == {"tersedia": False, "alasan": "Kategori 'Minuman' punya 2 sub kategori; sub kategori hanya satu tingkat."}
    assert d["aksi_tersedia"]["hapus"] == {"tersedia": False, "alasan": "Kategori 'Minuman' punya 2 sub kategori; hapus atau pindahkan sub kategorinya dulu."}
    s = KD.baris(k("Teh", jb=5, ja=5, parent="c-1", pname="Minuman"))
    assert (s["tingkat"], s["parent_id"], s["parent_name"], s["position_text"], s["aksi_tersedia"]["atur_induk"]["tersedia"]) == ("anak", "c-1", "Minuman", "Sub Minuman · 5 barang", True)
    assert list(s["aksi_tersedia"]) == ["ubah", "nonaktifkan", "aktifkan", "atur_induk", "hapus"]
    assert KD.baris(k("Kosong", jb=0, ja=0))["aksi_tersedia"]["hapus"]["tersedia"] is True


class KonnDaftar:
    def __init__(self):
        self.panggilan = []

    async def fetch(self, sql, *a):
        self.panggilan.append((sql, a))
        return []

    async def fetchval(self, sql, *a):
        self.panggilan.append((sql, a))
        return 0


def test_filter_tingkat_dan_induk_id_diteruskan_dan_tak_dikenal_ditolak():
    c = KonnDaftar()
    asyncio.run(KD.muat_daftar(c, "t1", tingkat="anak", induk_id="c-1"))
    assert c.panggilan[0][1] == ("t1", None, None, "anak", "c-1") and c.panggilan[1][1] == ("t1", None, None, "anak", "c-1", 50, 0)
    with pytest.raises(ValueError):
        asyncio.run(KD.muat_daftar(KonnDaftar(), "t1", tingkat="cucu"))
    for q in (KD.SQL_DAFTAR, KD.SQL_TOTAL):
        assert "$4::text IS NULL OR ($4 = 'induk' AND c.parent_id IS NULL) OR ($4 = 'anak' AND c.parent_id IS NOT NULL)" in q and "$5::uuid IS NULL OR c.parent_id = $5" in q


def test_rute_skema_hapus_dan_izin():
    src = (APP / "routers" / "items.py").read_text()
    for r in ('@router.post("/items/categories/{category_id}/parent/preview")', '@router.post("/items/categories/{category_id}/parent")'):
        assert r in src
    assert "tingkat: Optional[str] = Query(None" in src and "induk_id: Optional[UUID] = Query(None" in src and "KD.muat_daftar(conn, tenant_id, search, aksi, limit, offset, tingkat if isinstance(tingkat, str) else None" in src
    assert "INSERT INTO item_categories (tenant_id, name, parent_id)" in src and "KA._blok_induk(induk, diminta)" in src                    # buat dengan induk: aturan satu tingkat
    assert "KA.jumlah_anak_by_nama(conn, tenant_id, name)" in src and '"KATEGORI_PUNYA_ANAK"' in src                                          # hapus: sub menghalangi (tulis + pratinjau)
    assert src.count("_rencana_induk(conn") >= 2                                                                                          # pratinjau == tulis
    assert "parent_id: Optional[UUID]" in (APP / "schemas" / "items.py").read_text()
    assert '(rename|status|default|merge|parent)(/preview)?$' in (APP / "middleware" / "permission_middleware.py").read_text()
