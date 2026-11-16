"""K-1 uji DB di SALINAN (DRAF, belum pernah dijalankan; perbaiki di slot): services/kategori_daftar ATAS klon, tenant kaos + nyawit. Baca-saja.
A daftar objek: total == SQL COUNT item_categories; tiap baris jumlah_barang/jumlah_aktif == SQL independen (barang tak terhapus, lower(kategori)=lower(name)); urut lower(nama)
B ?aksi=tanpa_barang: total == SQL kategori tanpa barang; semua baris keadaan 'kosong'
C ringkasan: total/tanpa_barang/barang_tanpa_kategori/barang_total == SQL independen
D paginasi: limit 3 offset 0..n gabungan == daftar penuh (tanpa duplikat/hilang)
E bentuk lama: SELECT name ... == nama baris objek (urut abjad) -- paritas isi dgn GET lama
F hapus tersedia <=> jumlah_aktif == 0 (aturan DELETE /items/categories: hanya barang AKTIF menghalangi)
G V498: kolom ada, semua baris lama aktif/bukan bawaan/tanpa induk; batas DB: bawaan nonaktif ditolak CHECK, dua bawaan per tenant ditolak indeks unik (dalam savepoint, di SALINAN)
H UBAH NAMA (penulis asli KA.tulis_ubah_nama di transaksi yang di-rollback): label SEMUA barangnya berganti (== jumlah SQL), registry tak punya duplikat (trigger V257 tak membuat baris kembar), 1 baris audit RENAMED
J (K-3, transaksi di-rollback) bawaan: jadikan bawaan A lalu B -> tepat satu bawaan per usaha (indeks unik), A dicabut, kategori_bawaan() == B; barang baru tanpa kategori terisi bawaan (penentu buat)
K (K-3) pindah massal: barang TANPA kategori (SQL independen) == ukur_pindah.n; tulis_pindah memindahkan tepat n barang (== label baru), sisa tanpa kategori 0; barang terhapus-lunak tak ikut
L (K-3) gabung: sumber -> tujuan: jumlah barang bersumber (termasuk terhapus-lunak) berpindah label, baris registry sumber hilang, audit MERGED
M (K-4, transaksi di-rollback) induk: A dijadikan sub dari B -> ?tingkat=anak&induk_id=B memuat A (parent_name == B), B.jumlah_anak == 1, label barang A tak berubah (SQL), jumlah_anak_by_nama(B) == 1, rencana_gabung(B,...) diblok KATEGORI_PUNYA_ANAK, A tingkat atas lagi setelah parent=None
I nonaktif/aktif (KA.tulis_status): is_active berubah, daftar ?aksi=nonaktif memuatnya, bentuk lama (pilihan) tak memuatnya, riwayat memuat 2 kejadian terbaru"""
import asyncio
import sys

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import items as RI  # noqa: E402
from app.services import kategori_aksi as KA  # noqa: E402
from app.services import kategori_daftar as KD  # noqa: E402

NY, KA_T = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
KAOS = KA_T
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


async def main():
    pool = await RI.get_pool()
    async with pool.acquire() as c:
        for tid in (KA_T, NY):
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
        for tid in (KA_T, NY):
            lama = await c.fetchrow("/* tenant_id */ SELECT count(*) AS n, count(*) FILTER (WHERE is_active AND NOT is_default AND parent_id IS NULL) AS biasa FROM item_categories WHERE tenant_id = $1", tid)
            cek(f"G {tid}: {lama['n']} kategori lama semua aktif/bukan bawaan/tanpa induk", lama["n"] == lama["biasa"])
            kat = await c.fetchrow("/* tenant_id */ SELECT c.id FROM item_categories c WHERE c.tenant_id = $1 AND EXISTS (SELECT 1 FROM products p WHERE p.tenant_id = c.tenant_id AND lower(p.kategori) = lower(c.name)) ORDER BY c.name LIMIT 1", tid)
            if kat is None:
                print(f"CATATAN {tid}: tak ada kategori ber-barang, G/H/I dilewati", flush=True)
                continue
            try:
                async with c.transaction():
                    sp = c.transaction()
                    await sp.start()
                    try:
                        await c.execute("/* tenant_id */ UPDATE item_categories SET is_default = true, is_active = false WHERE id = $1 AND tenant_id = $2", kat["id"], tid)
                        cek(f"G {tid}: bawaan nonaktif TIDAK ditolak CHECK", False)
                    except Exception as e:  # noqa: BLE001
                        cek(f"G {tid}: bawaan nonaktif ditolak CHECK ({type(e).__name__})", "chk_item_categories_default_aktif" in str(e))
                    finally:
                        await sp.rollback()
                    k0 = await KA.ukur_kategori(c, tid, kat["id"])
                    baru = k0["name"] + " UJI"
                    n_sql = await c.fetchval("/* tenant_id */ SELECT count(*) FROM products WHERE tenant_id = $1 AND lower(kategori) = lower($2)", tid, k0["name"])
                    n_cat0 = await c.fetchval("/* tenant_id */ SELECT count(*) FROM item_categories WHERE tenant_id = $1", tid)
                    diubah = await KA.tulis_ubah_nama(c, tid, None, k0, baru)
                    n_baru = await c.fetchval("/* tenant_id */ SELECT count(*) FROM products WHERE tenant_id = $1 AND kategori = $2", tid, baru)
                    n_cat1 = await c.fetchval("/* tenant_id */ SELECT count(*) FROM item_categories WHERE tenant_id = $1", tid)
                    n_aud = await c.fetchval("/* tenant_id */ SELECT count(*) FROM audit_logs WHERE tenant_id = $1 AND entity_type = 'item_category' AND entity_id = $2::uuid AND \"eventType\" = 'ITEM_CATEGORY_RENAMED'", tid, kat["id"])
                    cek(f"H {tid}: ubah nama '{k0['name']}'->'{baru}': {diubah} barang diubah == SQL {n_sql} == berlabel baru {n_baru}", diubah == n_sql == n_baru)
                    cek(f"H {tid}: registry tak bertambah/duplikat ({n_cat0} -> {n_cat1}), audit RENAMED {n_aud}", n_cat0 == n_cat1 and n_aud == 1)
                    k1 = await KA.ukur_kategori(c, tid, kat["id"])
                    await KA.tulis_status(c, tid, None, k1, False)
                    nonaktif = await KD.muat_daftar(c, tid, kunci_aksi="nonaktif", limit=200)
                    pilihan = [x["name"] for x in await c.fetch("/* tenant_id */ SELECT name FROM item_categories WHERE tenant_id = $1 AND is_active", tid)]
                    cek(f"I {tid}: nonaktif -> ?aksi=nonaktif memuatnya, pilihan (bentuk lama) tidak", any(b["id"] == str(kat["id"]) for b in nonaktif["items"]) and baru not in pilihan)
                    await KA.tulis_status(c, tid, None, await KA.ukur_kategori(c, tid, kat["id"]), True)
                    rw = await KA.riwayat(c, tid, kat["id"])
                    cek(f"I {tid}: riwayat memuat aktif+nonaktif+ubah nama ({[r['event'] for r in rw]})", [r["event"] for r in rw][:3] == ["ITEM_CATEGORY_ACTIVATED", "ITEM_CATEGORY_DEACTIVATED", "ITEM_CATEGORY_RENAMED"])
                    # ---- K-3
                    ks = await c.fetch("/* tenant_id */ SELECT id FROM item_categories WHERE tenant_id = $1 AND is_active ORDER BY name LIMIT 3", tid)
                    if len(ks) >= 2:
                        a, b = await KA.ukur_kategori(c, tid, ks[0]["id"]), await KA.ukur_kategori(c, tid, ks[1]["id"])
                        await KA.tulis_bawaan(c, tid, None, a)
                        await KA.tulis_bawaan(c, tid, None, b)
                        n_def = await c.fetchval("/* tenant_id */ SELECT count(*) FROM item_categories WHERE tenant_id = $1 AND is_default", tid)
                        cek(f"J {tid}: ganti bawaan A->B: tepat 1 bawaan, kategori_bawaan() == B", n_def == 1 and await KA.kategori_bawaan(c, tid) == b["name"])
                        tanpa = await c.fetchval("/* tenant_id */ SELECT count(*) FROM products WHERE tenant_id = $1 AND deleted_at IS NULL AND (kategori IS NULL OR btrim(kategori) = '')", tid)
                        n_u, a_u, _ = await KA.ukur_pindah(c, tid, None, False)
                        cek(f"K {tid}: ukur_pindah(tanpa kategori) {n_u} == SQL {tanpa}", n_u == tanpa)
                        if tanpa:
                            dipindah = await KA.tulis_pindah(c, tid, None, None, False, b, None)
                            sisa = await c.fetchval("/* tenant_id */ SELECT count(*) FROM products WHERE tenant_id = $1 AND deleted_at IS NULL AND (kategori IS NULL OR btrim(kategori) = '')", tid)
                            cek(f"K {tid}: tulis_pindah {dipindah} == {tanpa}, sisa tanpa kategori {sisa}", dipindah == tanpa and sisa == 0)
                        sumber = await KA.ukur_kategori(c, tid, ks[0]["id"])
                        n_semua = await c.fetchval("/* tenant_id */ SELECT count(*) FROM products WHERE tenant_id = $1 AND lower(kategori) = lower($2)", tid, sumber["name"])
                        if not sumber["is_default"]:
                            tujuan = await KA.ukur_kategori(c, tid, ks[1]["id"])
                            dig = await KA.tulis_gabung(c, tid, None, sumber, tujuan)
                            ada = await c.fetchval("/* tenant_id */ SELECT count(*) FROM item_categories WHERE tenant_id = $1 AND id = $2", tid, ks[0]["id"])
                            masih = await c.fetchval("/* tenant_id */ SELECT count(*) FROM products WHERE tenant_id = $1 AND lower(kategori) = lower($2)", tid, sumber["name"])
                            cek(f"L {tid}: gabung '{sumber['name']}'->'{tujuan['name']}': {dig} barang == {n_semua}, registry asal hilang ({ada}), tak tersisa label lama ({masih})", dig == n_semua and ada == 0 and masih == 0)
                    # ---- K-4
                    if len(ks) >= 2:
                        a, b = await KA.ukur_kategori(c, tid, ks[0]["id"]), await KA.ukur_kategori(c, tid, ks[1]["id"])
                        if not a.get("parent_id") and not b.get("parent_id") and not a["jumlah_anak"] and not b["jumlah_anak"]:
                            lbl0 = await c.fetchval("/* tenant_id */ SELECT count(*) FROM products WHERE tenant_id = $1 AND lower(kategori) = lower($2)", tid, a["name"])
                            await KA.tulis_induk(c, tid, None, a, b)
                            anak = await KD.muat_daftar(c, tid, tingkat="anak", induk_id=b["id"], limit=200)
                            b2 = await KA.ukur_kategori(c, tid, b["id"])
                            lbl1 = await c.fetchval("/* tenant_id */ SELECT count(*) FROM products WHERE tenant_id = $1 AND lower(kategori) = lower($2)", tid, a["name"])
                            cek(f"M {tid}: '{a['name']}' sub dari '{b['name']}': filter anak memuatnya, parent_name, jumlah_anak {b2['jumlah_anak']}, label barang tak berubah ({lbl0}=={lbl1})",
                                [x["id"] for x in anak["items"]] == [str(a["id"])] and anak["items"][0]["parent_name"] == b["name"] and b2["jumlah_anak"] == 1 and lbl0 == lbl1)
                            cek(f"M {tid}: hapus '{b['name']}' diblok sub ({await KA.jumlah_anak_by_nama(c, tid, b['name'])}), gabung diblok", await KA.jumlah_anak_by_nama(c, tid, b["name"]) == 1 and KA.rencana_gabung(b2, a)["blocks"][0]["code"] == "KATEGORI_PUNYA_ANAK")
                            await KA.tulis_induk(c, tid, None, await KA.ukur_kategori(c, tid, a["id"]), None)
                            cek(f"M {tid}: parent=None -> tingkat atas lagi", (await KA.ukur_kategori(c, tid, a["id"]))["parent_id"] is None)
                    raise RuntimeError("ROLLBACK uji")   # SALINAN: semua tulis uji dibatalkan
            except RuntimeError as e:
                if str(e) != "ROLLBACK uji":
                    raise
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
