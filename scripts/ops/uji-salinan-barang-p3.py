"""P3 Barang & Jasa (pratinjau status/massal/hapus/duplikat/kategori) -- uji di SALINAN (klon milkydb); tulis HANYA di salinan.
GUARD: koneksi get_db_connection DAN pool harus menunjuk database scratch_bgp3*; selain itu berhenti SEBELUM menulis.
H1 pratinjau status nonaktif barang ber-stok: ok=true + peringatan BARANG_MASIH_BERSTOK (stok/nilai = ledger), NOL tulis (status tak berubah).
H2 pratinjau hapus: barang berjejak -> BARANG_BERJEJAK + rincian; barang segar -> hapus_permanen, lalu penulis DELETE benar-benar menghapus.
H3 duplikat: kode baru unik, konversi + item_pricing tersalin, nol baris ledger untuk salinan; pratinjau == penulis (nama/kode).
H4 bulk/status: pratinjau gabungan, tulis per barang + riwayat item_activities, kunci sama = replay, kunci baru = ditolak.
H5 kategori: dipakai barang aktif -> KATEGORI_DIPAKAI + jumlah == hitung SQL; kategori baru kosong -> ok.
H6 semua pratinjau READ ONLY: hitung baris products/item_pricing/unit_conversions tenant sama sebelum-sesudah."""
import asyncio
import os
import sys
import time

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import items as I  # noqa: E402
from app.schemas.items import CreateItemRequest  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402

HASIL = []
TENANT = tuple(x for x in os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(",") if x)


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, metode="POST", path="/api/items"):
    r = Request({"type": "http", "method": metode, "path": path, "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-barang-p3"}
    return r


def reqk(tid, kunci):
    r = Request({"type": "http", "method": "POST", "path": "/api/items/bulk/status", "headers": [(b"x-idempotency-key", kunci.encode())]})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-barang-p3"}
    return r


async def buat(tid, nama):
    b = CreateItemRequest(name=nama, item_type="goods", track_inventory=False, base_unit="Pcs", sales_price=1000, purchase_price=500)
    r = await I.create_item(req(tid), b)
    return str(r.data["id"]) if hasattr(r, "data") and isinstance(r.data, dict) else str(r["data"]["id"])


async def hitung(pool, tid):
    async with pool.acquire() as c:
        return tuple([await c.fetchval("SELECT count(*) FROM products WHERE tenant_id = $1", tid),
                      await c.fetchval("SELECT count(*) FROM item_pricing WHERE tenant_id = $1", tid),
                      await c.fetchval("SELECT count(*) FROM unit_conversions WHERE tenant_id = $1", tid)])


async def periksa(pool, tid):
    st = int(time.time())
    sebelum = await hitung(pool, tid)
    # ---- H1
    async with pool.acquire() as c:
        b = await c.fetchrow("""SELECT p.id, p.status FROM products p WHERE p.tenant_id = $1 AND p.status = 'active' AND p.deleted_at IS NULL
                                 AND p.item_type = 'goods' AND p.track_inventory = true
                                 AND (SELECT COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) FROM inventory_ledger il
                                      WHERE il.product_id = p.id AND il.tenant_id = p.tenant_id) <> 0 LIMIT 1""", tid)
    if b:
        r = (await I.preview_status_barang(req(tid), b["id"], {"is_active": False}))["data"]
        kode = [p["code"] for p in r["peringatan"]]
        async with pool.acquire() as c:
            tetap = await c.fetchval("SELECT status FROM products WHERE id = $1 AND tenant_id = $2", b["id"], tid)
        cek(f"{tid} H1 nonaktif ber-stok: ok + peringatan BARANG_MASIH_BERSTOK, status tak berubah",
            r["ok"] and "BARANG_MASIH_BERSTOK" in kode and tetap == "active" and r["rencana"]["jurnal"] is False, f"{kode} stok={r['rencana']['stok']}")
    else:
        print(f"INFO {tid}: tak ada barang ber-stok di salinan -> H1 dilewati (dijaga unit)")
    # ---- H2
    async with pool.acquire() as c:
        bj = await c.fetchval("SELECT product_id FROM inventory_ledger WHERE tenant_id = $1 LIMIT 1", tid)
    if bj:
        r = (await I.preview_hapus_barang(req(tid), bj))["data"]
        cek(f"{tid} H2a hapus berjejak: BARANG_BERJEJAK + rincian", (not r["ok"]) and r["blocks"][0]["code"] == "BARANG_BERJEJAK"
            and any(x["sumber"] == "inventory_ledger" for x in r["rencana"]["jejak"]), str(r["rencana"]["jejak"])[:90])
    seg = await buat(tid, f"UJI P3 Hapus {st}")
    r = (await I.preview_hapus_barang(req(tid), seg))["data"]
    await I.delete_item(req(tid, "DELETE"), seg)
    async with pool.acquire() as c:
        ada = await c.fetchval("SELECT count(*) FROM products WHERE id = $1 AND tenant_id = $2", seg, tid)
    cek(f"{tid} H2b hapus segar: pratinjau hapus_permanen, penulis benar-benar menghapus", r["ok"] and r["rencana"]["jenis"] == "hapus_permanen" and ada == 0)
    # ---- H3
    src = await buat(tid, f"UJI P3 Dup {st}")
    async with pool.acquire() as c:
        await c.execute("""INSERT INTO unit_conversions (tenant_id, product_id, base_unit, conversion_unit, conversion_factor, is_active)
                           VALUES ($1, $2, 'Pcs', 'Lusin', 12, true)""", tid, src)
        await c.execute("""INSERT INTO item_pricing (tenant_id, product_id, unit, pricing_type, price, is_active)
                           VALUES ($1, $2, 'Lusin', 'sales', 11000, true)""", tid, src)
    pr = (await I.preview_duplikat_barang(req(tid), __import__("uuid").UUID(src)))["data"]
    d = await I.duplicate_item(req(tid), __import__("uuid").UUID(src))
    baru = str(d.data["id"])
    async with pool.acquire() as c:
        nk = await c.fetchval("SELECT count(*) FROM unit_conversions WHERE product_id = $1 AND tenant_id = $2", baru, tid)
        nh = await c.fetchval("SELECT count(*) FROM item_pricing WHERE product_id = $1 AND tenant_id = $2", baru, tid)
        led = await c.fetchval("SELECT count(*) FROM inventory_ledger WHERE product_id = $1 AND tenant_id = $2", baru, tid)
        kd = await c.fetchrow("SELECT item_code, sku, nama_produk FROM products WHERE id = $1 AND tenant_id = $2", baru, tid)
        ks = await c.fetchval("SELECT item_code FROM products WHERE id = $1 AND tenant_id = $2", src, tid)
    cek(f"{tid} H3 duplikat: kode baru unik, konversi+harga tersalin, nol ledger, pratinjau == penulis",
        kd["item_code"] and kd["item_code"] != ks and kd["sku"] == kd["item_code"] and nk == 1 and nh == 1 and led == 0
        and pr["rencana"]["nama_baru"] == kd["nama_produk"] and pr["rencana"]["kode_baru"] == kd["item_code"],
        f"kode={kd['item_code']} pratinjau={pr['rencana']['kode_baru']} konv={nk} harga={nh}")
    # ---- H4 (bulk/status: pratinjau gabungan + tulis per barang idempoten + riwayat)
    from app.routers import bulk_items as BI
    a, b2 = await buat(tid, f"UJI P3 Massal A {st}"), await buat(tid, f"UJI P3 Massal B {st}")
    kunci = f"uji-p3-{st}-{tid}"
    pv = (await BI.bulk_status_preview(req(tid), BI.BulkStatusRequest(ids=[a, b2], is_active=False)))["data"]
    t1 = (await BI.bulk_status(reqk(tid, kunci), BI.BulkStatusRequest(ids=[a, b2], is_active=False)))["data"]
    async with pool.acquire() as c:
        sts = [await c.fetchval("SELECT status FROM products WHERE id = $1 AND tenant_id = $2", i, tid) for i in (a, b2)]
        akt = await c.fetchval("SELECT count(*) FROM item_activities WHERE tenant_id = $1 AND item_id = ANY($2::uuid[]) AND type = 'deactivated'", tid, [__import__("uuid").UUID(a), __import__("uuid").UUID(b2)])
    t2 = (await BI.bulk_status(reqk(tid, kunci), BI.BulkStatusRequest(ids=[a, b2], is_active=False)))["data"]   # kunci SAMA: replay
    t3 = (await BI.bulk_status(reqk(tid, kunci + "-lain"), BI.BulkStatusRequest(ids=[a, b2], is_active=False)))["data"]  # kunci baru: sudah nonaktif -> ditolak
    async with pool.acquire() as c:
        akt2 = await c.fetchval("SELECT count(*) FROM item_activities WHERE tenant_id = $1 AND item_id = ANY($2::uuid[]) AND type = 'deactivated'", tid, [__import__("uuid").UUID(a), __import__("uuid").UUID(b2)])
    cek(f"{tid} H4 bulk/status: pratinjau 2/2 ok, tulis menonaktifkan + 2 riwayat, kunci sama = replay (tanpa riwayat baru), kunci baru = ditolak",
        pv["ok_count"] == 2 and t1["all_ok"] and sts == ["inactive", "inactive"] and akt == 2
        and all(x["replay"] for x in t2["items"]) and akt2 == 2 and t3["rejected_count"] == 2,
        f"pv={pv['ok_count']} t1={t1['ok_count']} riwayat={akt}->{akt2} replay={[x['replay'] for x in t2['items']]} t3_tolak={t3['rejected_count']}")
    # ---- H5
    async with pool.acquire() as c:
        kat = await c.fetchrow("""SELECT kategori AS k, count(*) AS n FROM products WHERE tenant_id = $1 AND status = 'active' AND kategori IS NOT NULL
                                  AND kategori <> '' GROUP BY kategori ORDER BY count(*) DESC LIMIT 1""", tid)
        if kat:
            await c.execute("INSERT INTO item_categories (tenant_id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING", tid, kat["k"])
        await c.execute("INSERT INTO item_categories (tenant_id, name) VALUES ($1, $2)", tid, f"UJI P3 Kosong {st}")
    if kat:
        r = (await I.preview_hapus_kategori(req(tid), {"name": kat["k"]}))["data"]
        cek(f"{tid} H5a kategori terpakai: KATEGORI_DIPAKAI + jumlah == SQL", (not r["ok"]) and r["blocks"][0]["code"] == "KATEGORI_DIPAKAI"
            and r["rencana"]["jumlah_barang_terpakai"] == kat["n"], f"{kat['k']}={kat['n']}")
    r = (await I.preview_hapus_kategori(req(tid), {"name": f"UJI P3 Kosong {st}"}))["data"]
    cek(f"{tid} H5b kategori kosong: ok", r["ok"] and r["rencana"]["jumlah_barang_terpakai"] == 0)
    # ---- H6 (pratinjau saja): hitung sebelum/sesudah rangkaian pratinjau murni
    s1 = await hitung(pool, tid)
    for fn, args in ((I.preview_status_barang, (req(tid), __import__("uuid").UUID(src), {"is_active": False})),
                     (I.preview_duplikat_barang, (req(tid), __import__("uuid").UUID(src))),
                     (I.preview_hapus_barang, (req(tid), __import__("uuid").UUID(src))),
                     ):
        await fn(*args)
    from app.routers import bulk_items as BI2
    await BI2.bulk_status_preview(req(tid), BI2.BulkStatusRequest(ids=[src], is_active=False))
    cek(f"{tid} H6 pratinjau READ ONLY: hitung products/item_pricing/unit_conversions tak berubah", await hitung(pool, tid) == s1, f"{s1}")


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        db_pool = await c.fetchval("SELECT current_database()")
    lain = await I.get_db_connection()
    try:
        db_conn = await lain.fetchval("SELECT current_database()")
    finally:
        await lain.close()
    if not (db_pool.startswith("scratch_bgp3") and db_conn.startswith("scratch_bgp3")):
        print(f"TOLAK: bukan salinan (pool={db_pool} koneksi={db_conn})")
        return 2
    for tid in TENANT:
        await periksa(pool, tid)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
