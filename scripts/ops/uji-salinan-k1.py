"""K-1 uji DB di SALINAN (DRAF, belum pernah dijalankan; perbaiki di slot): services/kategori_daftar ATAS klon, tenant kaos + nyawit. Baca-saja.
A daftar objek: total == SQL COUNT item_categories; tiap baris jumlah_barang/jumlah_aktif == SQL independen (barang tak terhapus, lower(kategori)=lower(name)); urut lower(nama)
B ?aksi=tanpa_barang: total == SQL kategori tanpa barang; semua baris keadaan 'kosong'
C ringkasan: total/tanpa_barang/barang_tanpa_kategori/barang_total == SQL independen
D paginasi: limit 3 offset 0..n gabungan == daftar penuh (tanpa duplikat/hilang)
E bentuk lama: SELECT name ... == nama baris objek (urut abjad) -- paritas isi dgn GET lama
F hapus tersedia <=> jumlah_aktif == 0 (aturan DELETE /items/categories: hanya barang AKTIF menghalangi)"""
import asyncio
import sys

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import items as RI  # noqa: E402
from app.services import kategori_daftar as KD  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


async def main():
    pool = await RI.get_pool()
    async with pool.acquire() as c:
        for tid in (KA, NY):
            sql = {r["name"]: (int(r["n"]), int(r["a"])) for r in await c.fetch(
                "/* tenant_id */ SELECT c.name, (SELECT COUNT(*) FROM products p WHERE p.tenant_id = c.tenant_id AND lower(p.kategori) = lower(c.name) AND p.deleted_at IS NULL) AS n, "
                "(SELECT COUNT(*) FROM products p WHERE p.tenant_id = c.tenant_id AND lower(p.kategori) = lower(c.name) AND p.deleted_at IS NULL AND p.status = 'active') AS a "
                "FROM item_categories c WHERE c.tenant_id = $1", tid)}
            d = await KD.muat_daftar(c, tid, limit=200)
            cek(f"A {tid}: total {d['total']} == SQL {len(sql)}, baris {len(d['items'])}", d["total"] == len(sql) and len(d["items"]) == min(len(sql), 200))
            cek(f"A {tid}: jumlah_barang/jumlah_aktif tiap baris == SQL independen", all((b["jumlah_barang"], b["jumlah_aktif"]) == sql[b["name"]] for b in d["items"]))
            nama = [b["name"] for b in d["items"]]
            cek(f"A {tid}: urut lower(nama)", [n.lower() for n in nama] == sorted(n.lower() for n in nama))
            kosong = [k for k, v in sql.items() if v[0] == 0]
            tb = await KD.muat_daftar(c, tid, kunci_aksi="tanpa_barang", limit=200)
            cek(f"B {tid}: tanpa_barang total {tb['total']} == SQL {len(kosong)}, semua kosong", tb["total"] == len(kosong) and all(b["keadaan"] == "kosong" for b in tb["items"]))
            r = await KD.ringkasan(c, tid)
            n_tk = await c.fetchval("/* tenant_id */ SELECT count(*) FROM products WHERE tenant_id = $1 AND deleted_at IS NULL AND (kategori IS NULL OR btrim(kategori) = '')", tid)
            n_all = await c.fetchval("/* tenant_id */ SELECT count(*) FROM products WHERE tenant_id = $1 AND deleted_at IS NULL", tid)
            cek(f"C {tid}: ringkasan total {r['total_count']}, tanpa_barang {r['tanpa_barang_count']}, tanpa kategori {r['barang_tanpa_kategori_count']}/{r['barang_total']}",
                r["total_count"] == len(sql) and r["tanpa_barang_count"] == len(kosong) and r["barang_tanpa_kategori_count"] == n_tk and r["barang_total"] == n_all)
            gab, off = [], 0
            while True:
                h = await KD.muat_daftar(c, tid, limit=3, offset=off)
                gab += [b["id"] for b in h["items"]]
                if not h["has_more"]:
                    break
                off += 3
            cek(f"D {tid}: paginasi limit 3 == daftar penuh ({len(gab)} baris, tanpa duplikat)", gab == [b["id"] for b in d["items"]] and len(set(gab)) == len(gab))
            lama = [x["name"] for x in await c.fetch("/* tenant_id */ SELECT name FROM item_categories WHERE tenant_id = $1 ORDER BY name ASC", tid)]
            cek(f"E {tid}: bentuk lama {len(lama)} nama == himpunan baris objek", sorted(lama)[:len(nama)] == sorted(nama) if len(sql) <= 200 else len(nama) == 200)
            cek(f"F {tid}: hapus tersedia <=> jumlah_aktif == 0", all(b["aksi_tersedia"]["hapus"]["tersedia"] == (b["jumlah_aktif"] == 0) for b in d["items"]))
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
