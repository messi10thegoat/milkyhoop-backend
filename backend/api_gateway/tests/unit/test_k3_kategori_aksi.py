"""K-3 (17 Nov 2026; V498): master Kategori -- jadikan BAWAAN (satu per usaha; barang BARU tanpa kategori terisi), PINDAH barang massal (dari kategori lain / dari 'tanpa kategori'), GABUNG kategori.
Pratinjau == tulis (SATU penentu kategori_aksi); atomik; audit; tanpa jurnal."""
import asyncio
import pathlib
import re

import pytest

from app.services import kategori_aksi as KA

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


def k(nama="Minuman", jb=12, ja=10, aktif=True, bawaan=False, i="c-1"):
    return {"id": i, "name": nama, "is_active": aktif, "is_default": bawaan, "parent_id": None, "description": None, "created_at": None, "updated_at": None,
            "jumlah_barang": jb, "jumlah_aktif": ja}


def kode(r):
    return [b["code"] for b in r["blocks"]]


def test_rencana_bawaan():
    r = KA.rencana_bawaan(k("Umum"))
    assert r["ok"] and {x: v for x, v in r["rencana"].items() if x != "perubahan"} == {"id": "c-1", "nama": "Umum"} and "Barang baru tanpa kategori akan otomatis masuk 'Umum'" in r["peringatan"][0]
    assert kode(KA.rencana_bawaan(k(aktif=False))) == ["KATEGORI_NONAKTIF"] and kode(KA.rencana_bawaan(k(bawaan=True))) == ["KATEGORI_SUDAH_BAWAAN"] and kode(KA.rencana_bawaan(None)) == ["KATEGORI_TAK_ADA"]


def test_rencana_pindah():
    t = k("Umum", i="c-9")
    r = KA.rencana_pindah(None, t, 32, 30, [{"id": "p1"}], False)
    assert r["ok"] and r["rencana"]["dari"] is None and r["rencana"]["ke"] == "Umum" and r["rencana"]["jumlah_barang"] == 32 and r["rencana"]["jumlah_aktif"] == 30
    assert r["peringatan"] == ["32 barang (30 aktif) dari tanpa kategori berpindah ke kategori 'Umum'."]
    r2 = KA.rencana_pindah("Minuman", t, 5, 5, [], True)
    assert r2["peringatan"] == ["5 barang (5 aktif) dari kategori 'Minuman' berpindah ke kategori 'Umum'."]
    assert kode(KA.rencana_pindah("Minuman", None, 5, 5, [], True)) == ["KATEGORI_TUJUAN_TAK_ADA"] and kode(KA.rencana_pindah(None, k(aktif=False), 5, 5, [], False)) == ["KATEGORI_TUJUAN_NONAKTIF"]
    assert kode(KA.rencana_pindah("umum", t, 5, 5, [], True)) == ["KATEGORI_SAMA"] and kode(KA.rencana_pindah(None, t, 0, 0, [], False)) == ["TIDAK_ADA_BARANG"]


def test_rencana_gabung():
    s, t = k("Lama", jb=4, i="c-1"), k("Baru", i="c-2")
    r = KA.rencana_gabung(s, t)
    assert r["ok"] and {x: v for x, v in r["rencana"].items() if x != "perubahan"} == {"dari": "Lama", "dari_id": "c-1", "ke": "Baru", "ke_id": "c-2", "jumlah_barang": 4}
    assert r["peringatan"] == ["4 barang berpindah dari 'Lama' ke 'Baru', lalu kategori 'Lama' dihapus."]
    assert KA.rencana_gabung(k("Kosong", jb=0, ja=0, i="c-1"), t)["peringatan"] == ["Kategori 'Kosong' tidak punya barang; hanya dihapus."]
    assert kode(KA.rencana_gabung(k(bawaan=True), t)) == ["KATEGORI_BAWAAN"] and kode(KA.rencana_gabung(s, k(aktif=False, i="c-2"))) == ["KATEGORI_TUJUAN_NONAKTIF"]
    assert kode(KA.rencana_gabung(s, s)) == ["KATEGORI_SAMA"] and kode(KA.rencana_gabung(None, t)) == ["KATEGORI_TAK_ADA"] and kode(KA.rencana_gabung(s, None)) == ["KATEGORI_TAK_ADA"]


class Konn:
    def __init__(self, n_update="UPDATE 7", hitung=(3, 2), contoh=None, bawaan="Umum"):
        self.sql, self.n_update, self.hitung, self.contoh, self.bawaan = [], n_update, hitung, contoh or [], bawaan

    async def execute(self, sql, *a):
        self.sql.append((sql, a))
        return self.n_update if sql.startswith("UPDATE products") else "UPDATE 1"

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        return {"n": self.hitung[0], "a": self.hitung[1]}

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        return self.contoh

    async def fetchval(self, sql, *a):
        self.sql.append((sql, a))
        return self.bawaan


def jalan(aw):
    return asyncio.run(aw)


def test_tulis_bawaan_cabut_lama_dulu_baru_pasang_lalu_audit():
    c = Konn()
    jalan(KA.tulis_bawaan(c, "t1", "u1", k("Umum", i="c-9")))
    urut = [s[:48] for s, _ in c.sql]
    assert urut[0].startswith("SELECT pg_advisory_xact_lock") and c.sql[1][0].startswith("UPDATE item_categories SET is_default = false") and "is_default = true" in c.sql[2][0] and urut[3].startswith("INSERT INTO audit_logs")
    assert "AND is_default AND id <> $2" in c.sql[1][0] and c.sql[1][1] == ("t1", "c-9") and c.sql[3][1][0] == "ITEM_CATEGORY_DEFAULT_SET"   # cabut lama dulu: indeks unik satu bawaan


def test_pindah_dari_tanpa_kategori_dan_dari_kategori_dengan_id_sebagian():
    c = Konn(contoh=[{"id": "p1", "item_code": "A", "nama_produk": "Aqua", "status": "active"}])
    n, a, contoh = jalan(KA.ukur_pindah(c, "t1", None, False, None))
    assert (n, a, contoh) == (3, 2, [{"id": "p1", "kode": "A", "nama": "Aqua", "status": "active"}])
    assert c.sql[0][0] == KA.SQL_PINDAH_HITUNG["tanpa"] and c.sql[0][1] == ("t1", None) and c.sql[1][1] == ("t1", None, KA.CONTOH_MAKS)
    c2 = Konn()
    jalan(KA.ukur_pindah(c2, "t1", "Minuman", True, ["i1", "i2"]))
    assert c2.sql[0][0] == KA.SQL_PINDAH_HITUNG["kategori"] and c2.sql[0][1] == ("t1", ["i1", "i2"], "Minuman")
    c3 = Konn(hitung=(0, 0))
    assert jalan(KA.ukur_pindah(c3, "t1", None, False)) == (0, 0, []) and len(c3.sql) == 1      # tak ada barang -> tak ambil contoh


def test_tulis_pindah_kunci_update_atomik_audit_dan_parameter():
    c = Konn(n_update="UPDATE 32")
    n = jalan(KA.tulis_pindah(c, "t1", "u1", None, False, k("Umum", i="c-9"), None))
    assert n == 32 and c.sql[0][0].startswith("SELECT pg_advisory_xact_lock") and c.sql[1][0] == KA.SQL_PINDAH_TULIS["tanpa"]
    assert c.sql[1][1] == ("t1", None, "Umum") and c.sql[2][1][0] == "ITEM_CATEGORY_ITEMS_MOVED" and '"jumlah_barang": 32' in c.sql[2][1][5] and '"dari_tanpa_kategori": true' in c.sql[2][1][5]
    c2 = Konn(n_update="UPDATE 2")
    jalan(KA.tulis_pindah(c2, "t1", "u1", "Minuman", True, k("Umum", i="c-9"), ["i1"]))
    assert c2.sql[1][0] == KA.SQL_PINDAH_TULIS["kategori"] and c2.sql[1][1] == ("t1", ["i1"], "Minuman", "Umum") and '"sebagian": true' in c2.sql[2][1][5]


def test_tulis_gabung_pindah_semua_termasuk_terhapus_lunak_lalu_hapus_registry_lalu_audit():
    c = Konn(n_update="UPDATE 9")
    n = jalan(KA.tulis_gabung(c, "t1", "u1", k("Lama", i="c-1"), k("Baru", i="c-2")))
    urut = [s[:30] for s, _ in c.sql]
    assert n == 9 and urut[1] == "UPDATE products SET kategori =" and c.sql[2][0].startswith("DELETE FROM item_categories") and urut[3].startswith("INSERT INTO audit_logs")
    assert "deleted_at" not in c.sql[1][0] and "lower(kategori) = lower($2)" in c.sql[1][0] and c.sql[1][1] == ("t1", "Lama", "Baru") and c.sql[2][1] == ("t1", "c-1")
    assert c.sql[3][1][0] == "ITEM_CATEGORY_MERGED"


def test_kategori_bawaan_sql_dan_audit_event_terdaftar():
    assert KA.SQL_BAWAAN == "SELECT name FROM item_categories WHERE tenant_id = $1 AND is_default AND is_active"
    assert jalan(KA.kategori_bawaan(Konn(bawaan="Umum"), "t1")) == "Umum"
    assert {"bawaan", "pindah", "gabung"} <= set(KA.EVENT) and set(KA.EVENT.values()) == set(KA.TEKS_EVENT)


def test_sql_pindah_berpagar_tenant_tanpa_terhapus_lunak_hanya_tulis_di_update():
    for j in ("tanpa", "kategori"):
        for q in (KA.SQL_PINDAH_HITUNG[j], KA.SQL_PINDAH_CONTOH[j], KA.SQL_PINDAH_TULIS[j]):
            assert "p.tenant_id = $1" in q and "p.deleted_at IS NULL" in q and "($2::uuid[] IS NULL OR p.id = ANY($2::uuid[]))" in q
        assert not re.search(r"\\b(UPDATE|INSERT|DELETE)\\b", KA.SQL_PINDAH_HITUNG[j] + KA.SQL_PINDAH_CONTOH[j])
    assert "(p.kategori IS NULL OR btrim(p.kategori) = '')" in KA.SQL_PINDAH_TULIS["tanpa"] and "lower(p.kategori) = lower($3)" in KA.SQL_PINDAH_TULIS["kategori"]
    assert KA.SQL_PINDAH_TULIS["tanpa"].startswith("UPDATE products p SET kategori = $3")


def test_rute_izin_dan_isi_bawaan_di_penentu_buat_barang():
    src = (APP / "routers" / "items.py").read_text()
    for r in ('@router.post("/items/categories/move/preview")', '@router.post("/items/categories/move")', '@router.post("/items/categories/{category_id}/default/preview")',
              '@router.post("/items/categories/{category_id}/default")', '@router.post("/items/categories/{category_id}/merge/preview")', '@router.post("/items/categories/{category_id}/merge")'):
        assert r in src
    siap = src.split("async def _siapkan_buat_barang")[1].split("Check for duplicate name")[0]
    assert 'if not (body.kategori or "").strip():' in siap and "KA.kategori_bawaan(conn, tenant_id)" in siap and "body.kategori = _bawaan" in siap   # satu penentu: pratinjau + POST + impor
    assert "async def _rencana_pindah" in src and src.count("_rencana_pindah(conn") >= 2 and src.count("_rencana_gabung(conn") >= 2   # pratinjau == tulis
    izin = (APP / "middleware" / "permission_middleware.py").read_text()
    assert '(r"^/api/items/categories/[^/]+/(rename|status|default|merge|parent|accounts)(/preview)?$", ["POST"], "item", "U")' in izin and '(r"^/api/items/categories/move(/preview)?$", ["POST"], "item", "U")' in izin


def test_pindah_nomor_parameter_berurutan_tanpa_lubang():
    """Regresi harness server 17 Nov: varian 'tanpa' memakai LIMIT $4 / SET kategori = $4 padahal tak ada $3 -> asyncpg IndeterminateDatatypeError.
    Tiap SQL harus memakai $1..$n berurutan, dan n == jumlah argumen yang dikirim kode."""
    import re, asyncio

    def nomor(sql):
        return sorted({int(x) for x in re.findall(r"\$(\d+)", sql)})

    class C:
        def __init__(self): self.argn = []
        async def fetchrow(self, sql, *a): self.argn.append((sql, len(a))); return {"n": 1, "a": 1}
        async def fetch(self, sql, *a): self.argn.append((sql, len(a))); return []
        async def execute(self, sql, *a): self.argn.append((sql, len(a))); return "UPDATE 1"
        async def fetchval(self, sql, *a): self.argn.append((sql, len(a))); return None

    for d in (KA.SQL_PINDAH_HITUNG, KA.SQL_PINDAH_CONTOH, KA.SQL_PINDAH_TULIS):
        for j, sql in d.items():
            n = nomor(sql)
            assert n == list(range(1, len(n) + 1)), (j, sql)
    c = C()
    for sumber in (False, True):
        asyncio.run(KA.ukur_pindah(c, "t", "Kain" if sumber else None, sumber))
    for sql, k in c.argn:
        assert nomor(sql) == list(range(1, k + 1)), (sql, k)
    c2 = C()
    async def tulis():
        for sumber in (False, True):
            try:
                await KA.tulis_pindah(c2, "t", None, "Kain" if sumber else None, sumber, {"id": "i", "name": "Baru"})
            except Exception:
                pass
    asyncio.run(tulis())
    for sql, k in c2.argn:
        if sql.startswith("UPDATE products p SET kategori"):
            assert nomor(sql) == list(range(1, k + 1)), (sql, k)
