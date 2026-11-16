"""Daftar Harga DISKON % per baris (BUG-253) -- uji di SALINAN (klon milkydb); tulis HANYA di salinan. GUARD: pool harus scratch_dhdsk*.
V500 (berjangka) + V501 (min_price, cabang turunan 040) dipasang di salinan. Tanpa migrasi diskon (price_list_items.discount_percent sudah ada).
D1 resolve DB nyata: tingkat qty (>=1 null, >=10 5%, >=50 eksplisit 0%) -> null vs 0.0 vs 5.0; berjangka: sebelum/sesudah tanggal mulai = 5.0 / 8.0.
D2 tulis diskon 0 lewat penulis baris: DB menyimpan 0 (bukan null); detail GET memberi 0.0 (bukan null); resolve 0.0.
D3 impor 251 round trip kolom Diskon %: ekspor->baca->pratinjau semua 'tetap'; ubah diskon -> DB; kolom Diskon % tak ada di berkas = diskon utuh; kosong = dikosongkan.
D4 D3 massal berjangka: baris baru membawa diskon baris lama; harga baru; baris lama utuh."""
import asyncio
import os
import pathlib
import sys
import time
import uuid
from datetime import date, timedelta
from decimal import Decimal

from fastapi import HTTPException, Response
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import bulk_price_lists as BP  # noqa: E402
from app.routers import price_lists as PL  # noqa: E402
from app.schemas.price_lists import CreatePriceListRequest, PriceListItemCreate  # noqa: E402
from app.services import harga_jual as HJ  # noqa: E402
from app.services import impor_harga as IH  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402

HASIL = []
TENANT = tuple(x for x in os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(",") if x)
MIG = pathlib.Path("/app/backend/migrations")


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, kunci=None, method="POST"):
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": method, "path": "/api/price-lists", "headers": h})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-dh-dsk"}
    return r


async def diskon_db(pool, tid, pl):
    async with pool.acquire() as c:
        return [(r["min_quantity"], r["discount_percent"], r["start_date"], r["price"]) for r in await c.fetch(
            """SELECT pli.min_quantity, pli.discount_percent, pli.start_date, pli.price FROM price_list_items pli JOIN price_lists p ON p.id = pli.price_list_id
               WHERE p.tenant_id = $1 AND pli.price_list_id = $2::uuid ORDER BY pli.start_date NULLS FIRST, pli.min_quantity""", tid, pl)]


async def periksa(pool, tid):
    st = int(time.time())
    async with pool.acquire() as c:
        brg = await c.fetchrow("""SELECT id, COALESCE(item_code, sku) AS kode FROM products p WHERE tenant_id = $1 AND status = 'active' AND COALESCE(for_sales, true)
                                  AND deleted_at IS NULL AND COALESCE(item_code, sku) IS NOT NULL
                                  AND (SELECT count(*) FROM products q WHERE q.tenant_id = $1 AND (lower(q.item_code) = lower(COALESCE(p.item_code, p.sku))
                                       OR lower(q.sku) = lower(COALESCE(p.item_code, p.sku)))) = 1 ORDER BY id LIMIT 1""", tid)
    if brg is None:
        print(f"INFO {tid}: tak ada barang berkode unik -- dilewati")
        return
    r = await PL.create_price_list(req(tid, f"uji-dsk-buat-{st}"), CreatePriceListRequest(code=f"UJI-DSK-{st}", name="UJI diskon"))
    pl = uuid.UUID(str(r["data"]["id"]))
    t1 = date.today() + timedelta(days=30)

    async def tambah(harga, qty, diskon, mulai=None):
        await PL.add_item_to_price_list(req(tid), pl, PriceListItemCreate(item_id=str(brg["id"]), price=harga, min_quantity=qty, discount_percent=diskon, start_date=mulai))
    await tambah(10000, 1, None)
    await tambah(10000, 10, 5)
    await tambah(10000, 50, 0)                                   # 0% EKSPLISIT
    await tambah(10000, 10, 8, t1)                               # berjangka
    # ---- D1 resolve DB nyata
    async def daftar_fake(conn, tid_, customer_id, tanggal):
        return {"id": pl, "code": "UJI", "name": "UJI", "asal": "bawaan"}
    asli = HJ.daftar_untuk
    HJ.daftar_untuk = daftar_fake
    try:
        async def saring(qty, tgl):
            async with pool.acquire() as c:
                x = await HJ.harga_saran(c, tid, None, tgl, [{"key": "k", "item_id": str(brg["id"]), "unit": None, "quantity": qty}])
            return x["lines"][0]["diskon_persen"]
        hari = date.today()
        h = (await saring(1, hari), await saring(9, hari), await saring(12, hari), await saring(60, hari), await saring(12, t1 - timedelta(days=1)), await saring(12, t1), await saring(60, t1))
    finally:
        HJ.daftar_untuk = asli
    cek(f"{tid} D1 resolve: qty1=null, qty9=null, qty12=5.0, qty60=0.0 (bukan null), sebelum berjangka 5.0, pada tanggal mulai 8.0, qty60 berjangka tetap 0.0",
        h == (None, None, 5.0, 0.0, 5.0, 8.0, 0.0), f"{h}")
    # ---- D2 nol eksplisit tersimpan + detail
    db = await diskon_db(pool, tid, str(pl))
    detail = await PL.get_price_list(req(tid, method="GET"), pl)
    ds = {(i["min_quantity"], i["start_date"]): i["discount_percent"] for i in detail["data"]["items"]}
    cek(f"{tid} D2 diskon 0 tersimpan 0 (bukan null) dan detail GET 0.0 vs tak diatur null",
        any(x[0] == 50 and x[1] == Decimal(0) for x in db) and ds[(50.0, None)] == 0.0 and ds[(1.0, None)] is None and ds[(10.0, None)] == 5.0, f"{ds}")
    # ---- D3 impor round trip
    ekspor = await PL.ekspor_harga(req(tid, method="GET"), pl, "xlsx")
    baca = IH.baca_xlsx(ekspor.body)
    p = (await PL.pratinjau_impor_harga(req(tid), pl, {"baris": baca["baris"]}))["data"]
    cek(f"{tid} D3a ekspor->baca->pratinjau: {len(baca['baris'])} baris semua 'tetap' (diskon ikut, nol vs null terjaga)",
        p["ok"] and all(x["aksi"] == "tetap" for x in p["baris"]) and any(m["medan"] == "diskon" for m in baca["pemetaan"]), f"{p['ringkas']}")
    ubah = [dict(x) for x in baca["baris"]]
    target = next(i for i, x in enumerate(ubah) if float(x["min_qty"]) == 1)
    ubah[target]["diskon"] = 3
    pu = (await PL.pratinjau_impor_harga(req(tid), pl, {"baris": ubah}))["data"]
    await PL.impor_harga(req(tid, f"uji-dsk-imp-{st}"), pl, pu["payload"], Response())
    d3 = await diskon_db(pool, tid, str(pl))
    sama_lain = [x[1] for x in d3 if not (x[0] == 1 and x[2] is None)]
    cek(f"{tid} D3b impor ubah diskon qty1 null -> 3 di DB; baris lain utuh", any(x[0] == 1 and x[1] == Decimal(3) for x in d3) and sorted(map(float, [v for v in sama_lain if v is not None])) == [0.0, 5.0, 8.0], f"{d3}")
    tanpa = [{k: v for k, v in x.items() if k != "diskon"} for x in ubah]
    pt = (await PL.pratinjau_impor_harga(req(tid), pl, {"baris": tanpa}))["data"]
    cek(f"{tid} D3c berkas tanpa kolom Diskon %: semua 'tetap' (diskon lama utuh)", all(x["aksi"] == "tetap" for x in pt["baris"]), f"{pt['ringkas']}")
    kosong = [dict(x) for x in ubah]
    for x in kosong:
        x["diskon"] = None
    pk = (await PL.pratinjau_impor_harga(req(tid), pl, {"baris": [{k: v for k, v in x.items() if k != "diskon"} | {"diskon": ""} for x in kosong]}))["data"]
    cek(f"{tid} D3d kolom Diskon % ada tapi sel kosong: baris berdiskon = 'ubah' (dikosongkan)", sum(1 for x in pk["baris"] if x["aksi"] == "ubah") == 3, f"{pk['ringkas']}")
    # ---- D4 D3 massal berjangka
    t2 = date.today() + timedelta(days=60)
    B = BP.BulkHargaRequest
    n0 = len(await diskon_db(pool, tid, str(pl)))
    await BP.bulk_price(req(tid, f"uji-dsk-d3-{st}"), pl, B(mode="persen", nilai=Decimal("10"), berlaku_mulai=t2))
    d4 = await diskon_db(pool, tid, str(pl))
    baru = [x for x in d4 if x[2] == t2]
    cek(f"{tid} D4 D3 massal berjangka: baris bertambah ({n0}->{len(d4)}), baris baru membawa diskon baris lama (3, 5, 0, 8) dan harga 11000",
        len(d4) > n0 and sorted(float(x[1]) for x in baru if x[1] is not None) == [0.0, 3.0, 5.0, 8.0] and all(x[3] == 11000 for x in baru), f"{baru}")


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
        if not db.startswith("scratch_dhdsk"):
            print(f"TOLAK: bukan salinan (pool={db})")
            return 2
        await c.execute((MIG / "V500__daftar_harga_berjangka.sql").read_text())
        await c.execute((MIG / "V501__price_list_items_min_price.sql").read_text())   # cabang turunan 040: penulis baris menulis min_price
    for tid in TENANT:
        await periksa(pool, tid)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
