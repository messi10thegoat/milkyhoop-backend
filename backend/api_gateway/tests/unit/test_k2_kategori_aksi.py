"""K-2 (17 Nov 2026; V498): aksi master Kategori -- pratinjau == tulis (SATU penentu kategori_aksi): buat, UBAH NAMA (label SEMUA barangnya ikut, atomik), nonaktif/aktifkan
(nonaktif ber-barang aktif = PERINGATAN; bawaan = blok), audit/riwayat. Migrasi V498 aditif tanpa kolom akun (B1 ditunda)."""
import asyncio
import pathlib
import re

import pytest

from app.services import kategori_aksi as KA

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
BE = pathlib.Path(__file__).resolve().parents[3]


def k(nama="Minuman", jb=12, ja=10, aktif=True, bawaan=False, i="c-1"):
    return {"id": i, "name": nama, "is_active": aktif, "is_default": bawaan, "parent_id": None, "description": None, "created_at": None, "updated_at": None,
            "jumlah_barang": jb, "jumlah_aktif": ja}


def kode(r):
    return [b["code"] for b in r["blocks"]]


def test_rencana_buat():
    assert KA.rencana_buat("  Minuman  ", False) == {"ok": True, "blocks": [], "peringatan": [], "rencana": {"nama": "Minuman"}}
    assert kode(KA.rencana_buat("minuman", True)) == ["KATEGORI_GANDA"] and kode(KA.rencana_buat("   ", False)) == ["NAMA_KOSONG"]
    assert kode(KA.rencana_buat("x" * 101, False)) == ["NAMA_TERLALU_PANJANG"] and KA.rencana_buat("x" * 100, False)["ok"]
    assert KA.rencana_buat("Aqua   Gelas", False)["rencana"]["nama"] == "Aqua Gelas"   # spasi ganda dirapikan


def test_rencana_ubah_nama_peringatan_blok_dan_ganti_huruf():
    r = KA.rencana_ubah_nama(k(), "Minuman Ringan", False)
    assert r["ok"] and r["rencana"] == {"id": "c-1", "dari": "Minuman", "ke": "Minuman Ringan", "jumlah_barang": 12}
    assert r["peringatan"] == ["12 barang ikut berganti kategori dari 'Minuman' ke 'Minuman Ringan'."]
    assert KA.rencana_ubah_nama(k(jb=0, ja=0), "Baru", False)["peringatan"] == []
    assert kode(KA.rencana_ubah_nama(k(), "Minuman", False)) == ["NAMA_TIDAK_BERUBAH"]
    assert KA.rencana_ubah_nama(k(), "minuman", False)["ok"]          # ganti huruf besar/kecil SENDIRI boleh (bukan kembar)
    assert kode(KA.rencana_ubah_nama(k(), "Snack", True)) == ["KATEGORI_GANDA"]
    assert kode(KA.rencana_ubah_nama(None, "X", False)) == ["KATEGORI_TAK_ADA"] and kode(KA.rencana_ubah_nama(k(), " ", False)) == ["NAMA_KOSONG"]


def test_rencana_status():
    nonaktif = KA.rencana_status(k(), False)
    assert nonaktif["ok"] and nonaktif["peringatan"] == ["10 barang aktif masih memakai kategori 'Minuman'. Barangnya tetap membawa kategori ini; hanya tak muncul sebagai pilihan baru."]
    assert KA.rencana_status(k(jb=3, ja=0), False)["peringatan"] == [] and KA.rencana_status(k(aktif=False), True)["ok"]
    assert kode(KA.rencana_status(k(aktif=False), False)) == ["KATEGORI_SUDAH_NONAKTIF"] and kode(KA.rencana_status(k(), True)) == ["KATEGORI_SUDAH_AKTIF"]
    assert kode(KA.rencana_status(k(bawaan=True), False)) == ["KATEGORI_BAWAAN"] and KA.rencana_status(k(bawaan=True), True)["ok"] is False   # bawaan sudah aktif
    assert kode(KA.rencana_status(None, True)) == ["KATEGORI_TAK_ADA"]


class Konn:
    def __init__(self, hasil_update="UPDATE 7"):
        self.sql, self.hasil_update = [], hasil_update

    async def execute(self, sql, *a):
        self.sql.append((sql, a))
        return self.hasil_update if sql.startswith("UPDATE products") else "UPDATE 1"

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        return []


def test_tulis_ubah_nama_registry_dulu_lalu_products_lalu_audit_atomik_di_kunci():
    c = Konn()
    n = asyncio.run(KA.tulis_ubah_nama(c, "t1", "u1", k(), "Minuman Ringan"))
    urut = [s.split(" SET ")[0].split("(")[0].strip()[:40] for s, _ in c.sql]
    assert n == 7 and urut[0].startswith("SELECT pg_advisory_xact_lock") and urut[1] == "UPDATE item_categories" and urut[2] == "UPDATE products" and urut[3].startswith("INSERT INTO audit_logs")
    assert c.sql[1][1] == ("t1", "c-1", "Minuman Ringan") and c.sql[2][1] == ("t1", "Minuman", "Minuman Ringan")
    assert c.sql[1][0].startswith("UPDATE item_categories SET name = $3, updated_at = NOW()") and c.sql[2][0].startswith("UPDATE products SET kategori = $3, updated_at = NOW()")
    assert "lower(kategori) = lower($2)" in c.sql[2][0] and "tenant_id = $1" in c.sql[2][0] and "deleted_at" not in c.sql[2][0]   # SEMUA barang (termasuk terhapus-lunak) ikut label
    assert c.sql[3][1][0] == "ITEM_CATEGORY_RENAMED" and '"barang_diubah": 7' in c.sql[3][1][5]


def test_tulis_status_dan_audit():
    c = Konn()
    asyncio.run(KA.tulis_status(c, "t1", "u1", k(), False))
    assert c.sql[1][0].startswith("UPDATE item_categories SET is_active = $3") and c.sql[1][1] == ("t1", "c-1", False)
    assert c.sql[2][1][0] == "ITEM_CATEGORY_DEACTIVATED"
    c2 = Konn()
    asyncio.run(KA.tulis_status(c2, "t1", "u1", k(aktif=False), True))
    assert c2.sql[2][1][0] == "ITEM_CATEGORY_ACTIVATED"


def test_sql_ukur_dan_bentrok_berpagar_tenant_audit_append_only():
    assert "c.tenant_id = $1 AND c.id = $2" in KA.SQL_UKUR and "p.tenant_id = c.tenant_id" in KA.SQL_UKUR and "p.deleted_at IS NULL" in KA.SQL_UKUR
    assert "tenant_id = $1 AND lower(name) = lower($2) AND ($3::uuid IS NULL OR id <> $3)" in KA.SQL_BENTROK
    src = (APP / "services" / "kategori_aksi.py").read_text()
    assert not re.search(r"(UPDATE|DELETE FROM)\s+audit_logs", src, re.I)   # audit hanya INSERT (Law 12)
    assert set(KA.EVENT) == {"buat", "ubah_nama", "nonaktif", "aktif", "hapus"} and set(KA.EVENT.values()) == set(KA.TEKS_EVENT)


def test_migrasi_v498_aditif_tanpa_kolom_akun_idempoten_dengan_pembalik():
    m = (BE / "migrations" / "V498__item_categories_standar_so.sql").read_text()
    rb = (BE / "migrations" / "V498__item_categories_standar_so_ROLLBACK.sql").read_text()
    kode_m = "\n".join(b for b in m.splitlines() if not b.lstrip().startswith("--"))
    for kol in ("is_active BOOLEAN NOT NULL DEFAULT true", "is_default BOOLEAN NOT NULL DEFAULT false", "parent_id UUID REFERENCES item_categories(id)", "description TEXT", "updated_at TIMESTAMPTZ"):
        assert f"ADD COLUMN IF NOT EXISTS {kol};" in kode_m
    assert not re.search(r"account_id|sales_account|inventory_account|cogs_account|purchase_account", kode_m)   # kolom akun B1 TIDAK di V498 (putusan)
    assert "uq_item_categories_default ON item_categories (tenant_id) WHERE is_default" in kode_m and "IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_item_categories_default_aktif')" in kode_m
    assert "UPDATE " not in kode_m.upper() and "DELETE" not in kode_m.upper()
    for t in (m, rb):
        assert not re.search(r"^\s*(BEGIN|COMMIT)\s*;", t, re.M | re.I)   # transaksi eksplisit; BEGIN di dalam DO $$ plpgsql sah (pola V496)
    for kol in ("is_active", "is_default", "parent_id", "description", "updated_at"):
        assert f"DROP COLUMN IF EXISTS {kol};" in rb


def test_rute_dan_izin():
    src = (APP / "routers" / "items.py").read_text()
    for r in ('@router.post("/items/categories/preview")', '@router.get("/items/categories/{category_id}")', '@router.get("/items/categories/{category_id}/history")',
              '@router.post("/items/categories/{category_id}/rename/preview")', '@router.post("/items/categories/{category_id}/rename")',
              '@router.post("/items/categories/{category_id}/status/preview")', '@router.post("/items/categories/{category_id}/status")'):
        assert r in src
    assert src.index('@router.get("/items/categories/summary")') < src.index('@router.get("/items/categories/{category_id}")')   # statis dulu: 'summary' bukan id
    assert 'KA.catat_audit(conn, tenant_id, ctx["user_id"], "buat"' in src and 'KA.catat_audit(conn, tenant_id, ctx["user_id"], "hapus"' in src
    assert "WHERE tenant_id = $1 AND is_active\n            ORDER BY name ASC" in src   # bentuk lama = pilihan: nonaktif tak ikut
    izin = (APP / "middleware" / "permission_middleware.py").read_text()
    assert '(r"^/api/items/categories/[^/]+/(rename|status)(/preview)?$", ["POST"], "item", "U")' in izin and '(r"^/api/items/categories/preview$", ["POST"], "item", "C")' in izin
    assert '(r"^/api/items/categories/[^/]+$", ["GET"], "item", "R")' in izin and '(r"^/api/items/categories/[^/]+/history$", ["GET"], "item", "R")' in izin
