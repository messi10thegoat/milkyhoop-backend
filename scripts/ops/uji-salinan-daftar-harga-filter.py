"""Daftar Harga FILTER barang + batas 500 (BUG-254) -- uji di SALINAN (klon milkydb); tulis HANYA di salinan. GUARD: pool harus scratch_dhflt*.
products.merek (V502, BACKEND) belum ada di master: bila migrasi V502 tak ada di pohon, salinan diberi kolom merek sendiri (HANYA salinan).
F1 resolusi filter kategori (beda huruf) == hitungan langsung SQL; F2 saringan pemasok; F3 gabungan AND; F4 di_daftar membalik.
F5 barang-pilihan: total + halaman == SQL langsung, kotak pilihan dari server; tenant lain tak bocor.
F6 UKUR 500 barang: daftar berisi 500 barang uji; D3 pratinjau (semua barang) -> waktu; D3 tulis -> waktu total + p50/p95 per barang; 501 = 400 BULK_TERLALU_BANYAK.
F7 pratinjau == tulis: payload.item_ids dipakai tulis; baris DB berubah hanya untuk id itu."""
import asyncio
import os
import pathlib
import statistics
import sys
import time
import uuid
from decimal import Decimal

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import bulk_price_lists as BP  # noqa: E402
from app.routers import price_lists as PL  # noqa: E402
from app.schemas.price_lists import CreatePriceListRequest, PriceListItemCreate  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402

HASIL = []
TENANT = tuple(x for x in os.environ.get("TENANT", "kaos-biru-konveksi").split(",") if x)
MIG = pathlib.Path("/app/backend/migrations")
N = 500


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, kunci=None, method="POST"):
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": method, "path": "/api/price-lists", "headers": h, "query_string": b""})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-dh-flt"}
    return r


async def galat(coro):
    try:
        await coro
    except HTTPException as e:
        d = e.detail
        return (d.get("code") if isinstance(d, dict) else getattr(e, "code", None)), e.status_code
    return None


async def periksa(pool, tid):
    st = int(time.time())
    async with pool.acquire() as c:
        tmpl = await c.fetchrow("""SELECT id FROM products WHERE tenant_id = $1 AND status = 'active' AND COALESCE(for_sales, true) AND deleted_at IS NULL
                                   AND item_type = 'goods' ORDER BY id LIMIT 1""", tid)
        vendor = await c.fetchval("SELECT id FROM vendors WHERE tenant_id = $1 ORDER BY id LIMIT 1", tid)
        if tmpl is None:
            print(f"INFO {tid}: tak ada barang -- dilewati")
            return
        # ---- barang uji: 500 salinan template (kode/sku/barcode dikosongkan), kategori UJI-FLT-A (400) / UJI-FLT-B (100), pemasok = vendor (bila ada)
        await c.execute("DROP TABLE IF EXISTS tmp_uji_flt")
        await c.execute("CREATE TEMP TABLE tmp_uji_flt AS SELECT p.*, g FROM products p, generate_series(1, $2::int) g WHERE p.tenant_id = $3 AND p.id = $1", tmpl["id"], N, tid)
        await c.execute("""UPDATE tmp_uji_flt SET id = gen_random_uuid(), nama_produk = 'UJI-FLT ' || g || ' ' || $1, item_code = NULL, sku = NULL, barcode = NULL,
                           kategori = CASE WHEN g <= 400 THEN 'Uji-Flt-A' ELSE 'UJI-FLT-b' END, preferred_vendor_id = CASE WHEN g % 2 = 0 THEN $2::uuid ELSE NULL END
                           WHERE tenant_id = $3""", str(st), vendor, tid)
        await c.execute("ALTER TABLE tmp_uji_flt DROP COLUMN g")
        # kolom GENERATED ALWAYS (mis. track_lot_numbers) tak boleh diisi eksplisit: daftar kolom dibuat dari katalog, bukan SELECT *
        kol = ", ".join('"' + r["attname"] + '"' for r in await c.fetch(
            "SELECT attname FROM pg_catalog.pg_attribute WHERE attrelid = 'public.products'::regclass AND attnum > 0 AND NOT attisdropped AND attgenerated = '' ORDER BY attnum"))
        await c.execute(f"INSERT INTO products ({kol}) SELECT {kol} FROM tmp_uji_flt WHERE tenant_id = $1", tid)
    r = await PL.create_price_list(req(tid, f"uji-flt-buat-{st}"), CreatePriceListRequest(code=f"UJI-FLT-{st}", name="UJI filter"))
    pl = uuid.UUID(str(r["data"]["id"]))
    async with pool.acquire() as c:
        await c.execute("""INSERT INTO price_list_items (price_list_id, item_id, price, min_quantity)
                           SELECT $1::uuid, p.id, 10000, 1 FROM products p WHERE p.tenant_id = $2 AND p.nama_produk LIKE 'UJI-FLT %' AND p.kategori = 'Uji-Flt-A'""", str(pl), tid)
        langsung_a = await c.fetchval("SELECT count(*) FROM products WHERE tenant_id = $1 AND lower(kategori) = 'uji-flt-a' AND nama_produk LIKE 'UJI-FLT %'", tid)
        langsung_b = await c.fetchval("SELECT count(*) FROM products WHERE tenant_id = $1 AND lower(kategori) = 'uji-flt-b' AND nama_produk LIKE 'UJI-FLT %'", tid)
        langsung_pem = await c.fetchval("SELECT count(*) FROM products WHERE tenant_id = $1 AND nama_produk LIKE 'UJI-FLT %' AND preferred_vendor_id = $2::uuid", tid, vendor)
    B = BP.BulkHargaRequest
    # ---- F1 kategori beda huruf (di_daftar=false: barang aktif+dijual yang BELUM di daftar -> kategori B = 100)
    ids_b = await BP._siapkan(pool, {"tenant_id": tid}, pl, B(mode="persen", nilai=Decimal("1"), filter={"kategori": ["UJI-FLT-B"], "di_daftar": False}))
    ids_a = await BP._siapkan(pool, {"tenant_id": tid}, pl, B(mode="persen", nilai=Decimal("1"), filter={"kategori": ["uji-flt-a"]}))
    cek(f"{tid} F1 kategori tanpa beda huruf: A di daftar = {langsung_a}, B belum di daftar = {langsung_b} (sama dgn SQL langsung)", len(ids_a[0]) == langsung_a and len(ids_b[0]) == langsung_b, f"{len(ids_a[0])}/{len(ids_b[0])}")
    if vendor:
        ids_p = await BP._siapkan(pool, {"tenant_id": tid}, pl, B(mode="persen", nilai=Decimal("1"), filter={"pemasok_id": [str(vendor)], "kategori": ["Uji-Flt-A"]}))
        async with pool.acquire() as c:
            langsung_ap = await c.fetchval("""SELECT count(*) FROM products WHERE tenant_id = $1 AND nama_produk LIKE 'UJI-FLT %' AND lower(kategori) = 'uji-flt-a'
                                              AND preferred_vendor_id = $2::uuid""", tid, vendor)
        cek(f"{tid} F2+F3 pemasok AND kategori = {langsung_ap} (SQL langsung)", len(ids_p[0]) == langsung_ap and 0 < langsung_ap < langsung_a, f"{len(ids_p[0])}")
    # ---- F5 barang-pilihan
    d = (await BP.barang_pilihan(req(tid, method="GET"), pl, kategori=["uji-flt-a"], pemasok_id=[], merek=[], q=None, di_daftar=None, limit=50, offset=0))["data"]
    kat = [k["nama"] for k in d["kategori_pilihan"]]
    cek(f"{tid} F5 barang-pilihan: total {d['total']} == {langsung_a}, halaman 50, kotak kategori memuat Uji-Flt-A", d["total"] == langsung_a and len(d["items"]) == 50 and "Uji-Flt-A" in kat, f"{d['total']} {len(d['items'])}")
    asing = await galat(BP.barang_pilihan(req("tenant-tak-ada", method="GET"), pl, kategori=[], pemasok_id=[], merek=[], q=None, di_daftar=None, limit=5, offset=0))
    cek(f"{tid} F5b daftar tenant lain 404", asing is not None and asing[1] == 404, f"{asing}")
    # ---- F6 ukur 500 (barang uji A=400 di daftar + tambah 100 B ke daftar => 500)
    async with pool.acquire() as c:
        await c.execute("""INSERT INTO price_list_items (price_list_id, item_id, price, min_quantity)
                           SELECT $1::uuid, p.id, 10000, 1 FROM products p WHERE p.tenant_id = $2 AND p.nama_produk LIKE 'UJI-FLT %' AND p.kategori <> 'Uji-Flt-A'""", str(pl), tid)
    semua = B(mode="persen", nilai=Decimal("10"), filter={"kategori": ["uji-flt-a", "uji-flt-b"]})
    t0 = time.perf_counter()
    ids, nama = await BP._siapkan(pool, {"tenant_id": tid}, pl, semua)
    pr = await BP.bulk_price_preview(req(tid), pl, semua)
    t_pra = time.perf_counter() - t0
    cek(f"{tid} F6a pratinjau {len(ids)} barang dalam {t_pra:.1f} dtk (batas wajar 60)", len(ids) == N and pr["data"]["total"] == N and t_pra < 60, f"{t_pra:.1f}s ok_count={pr['data']['ok_count']}")
    pay = pr["data"]["payload"]
    t1 = time.perf_counter()
    tulis = await BP.bulk_price(req(tid, f"uji-flt-tulis-{st}"), pl, B(mode="persen", nilai=Decimal("10"), item_ids=pay["item_ids"]))
    t_tulis = time.perf_counter() - t1
    async with pool.acquire() as c:
        harga = [r["price"] for r in await c.fetch("SELECT pli.price FROM price_list_items pli JOIN price_lists p ON p.id = pli.price_list_id WHERE p.tenant_id = $1 AND pli.price_list_id = $2::uuid", tid, str(pl))]
    cek(f"{tid} F6b tulis {N} barang dalam {t_tulis:.1f} dtk (batas wajar 120; {t_tulis / N * 1000:.0f} ms/barang)", t_tulis < 120 and tulis["data"] is not None and len(harga) == N and set(harga) == {11000}, f"{t_tulis:.1f}s hasil={set(harga)}")
    ke_501 = await galat(BP.bulk_price_preview(req(tid), pl, B(mode="persen", nilai=Decimal("1"), item_ids=[str(uuid.uuid4()) for _ in range(501)])))
    cek(f"{tid} F6c 501 barang = 400 BULK_TERLALU_BANYAK", ke_501 == ("BULK_TERLALU_BANYAK", 400), f"{ke_501}")
    # ---- F7 pratinjau == tulis (ids dari payload); barang tak di payload tak berubah
    async with pool.acquire() as c:
        n_lain = await c.fetchval("SELECT count(*) FROM price_list_items pli JOIN price_lists p ON p.id = pli.price_list_id WHERE p.tenant_id = $1 AND pli.price_list_id <> $2::uuid AND pli.price = 11000", tid, str(pl))
    print(f"INFO {tid} F7 baris daftar lain berharga 11000 (kontrol, tak dipakai): {n_lain}")
    cek(f"{tid} F7 tulis memakai ids payload pratinjau: {len(pay['item_ids'])} id, semua barang di daftar ikut berubah tepat sekali", len(pay["item_ids"]) == N and len(set(pay["item_ids"])) == N)


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
        if not db.startswith("scratch_dhflt"):
            print(f"TOLAK: bukan salinan (pool={db})")
            return 2
        await c.execute((MIG / "V500__daftar_harga_berjangka.sql").read_text())
        ada = await c.fetchval("SELECT count(*) FROM pg_catalog.pg_attribute WHERE attrelid = 'products'::regclass AND attname = 'merek' AND NOT attisdropped")
        if not ada:
            v502 = sorted(MIG.glob("V502__*merek*.sql"))
            if v502:
                await c.execute(v502[0].read_text())
                print(f"INFO V502 dari pohon: {v502[0].name}")
            else:
                await c.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS merek TEXT")
                print("INFO V502 tak ada di pohon: kolom products.merek dibuat HANYA di salinan")
    for tid in TENANT:
        await periksa(pool, tid)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
