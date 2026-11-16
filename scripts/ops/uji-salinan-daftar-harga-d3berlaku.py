"""Daftar Harga D3 BERLAKU (BUG-278) -- uji di SALINAN (klon milkydb); tulis HANYA di salinan. GUARD: pool harus scratch_dhd3b*.
V500 + V501 dipasang di salinan. Skenario: barang dengan baris A (qty 1, 10.000, tanpa tanggal), B (qty 1, 11.000, mulai T1 = hari ini + 10), C (qty 10, 9.000).
G1 pratinjau D3 +10% berlaku T2 (hari ini + 40): rencana memuat B (-> 12.100) dan C (-> 9.900), TIDAK memuat A; urutan pasti.
G2 tulis: tepat DUA baris baru bertanggal T2 (qty 1 = 12.100 dari B, qty 10 = 9.900); A dan B tak berubah; ulang pratinjau = hasil sama (deterministik).
G3 ubah LANGSUNG +10% (tanpa tanggal): hanya baris yang berlaku hari ini (A -> 11.000, C -> 9.900); B (belum berlaku) dan baris T2 tak disentuh.
G4 daftar yang semua barisnya belum berlaku pada tanggal itu: blok DH_TAK_BERLAKU_PADA_TANGGAL di pratinjau item."""
import asyncio
import os
import pathlib
import sys
import time
import uuid
from datetime import date, timedelta
from decimal import Decimal

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import bulk_price_lists as BP  # noqa: E402
from app.routers import price_lists as PL  # noqa: E402
from app.schemas.price_lists import CreatePriceListRequest, PriceListItemCreate  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402

HASIL = []
TENANT = tuple(x for x in os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(",") if x)
MIG = pathlib.Path("/app/backend/migrations")


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, uid, kunci=None):
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": "POST", "path": "/api/price-lists", "headers": h})
    r.state.user = {"tenant_id": tid, "user_id": uid, "username": "uji-dh-d3b"}
    return r


async def baris(pool, tid, pl):
    async with pool.acquire() as c:
        return sorted([(float(r["min_quantity"]), str(r["start_date"]), float(r["price"])) for r in await c.fetch(
            """SELECT pli.min_quantity, pli.start_date, pli.price FROM price_list_items pli JOIN price_lists p ON p.id = pli.price_list_id
               WHERE p.tenant_id = $1 AND pli.price_list_id = $2::uuid""", tid, str(pl))])


async def periksa(pool, tid):
    st = int(time.time())
    async with pool.acquire() as c:
        brg = await c.fetchrow("""SELECT id FROM products WHERE tenant_id = $1 AND status = 'active' AND COALESCE(for_sales, true) AND deleted_at IS NULL ORDER BY id LIMIT 1""", tid)
        user = await c.fetchrow('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY id LIMIT 1', tid)
    if brg is None or user is None:
        print(f"INFO {tid}: tak ada barang / pengguna -- dilewati")
        return
    uid = user["id"]
    hari = date.today()
    t1, t2 = hari + timedelta(days=10), hari + timedelta(days=40)
    r = await PL.create_price_list(req(tid, uid, f"uji-d3b-buat-{st}"), CreatePriceListRequest(code=f"UJI-D3B-{st}", name="UJI D3 berlaku"))
    pl = uuid.UUID(str(r["data"]["id"]))
    for kw in ({"price": 10000, "min_quantity": 1}, {"price": 11000, "min_quantity": 1, "start_date": t1}, {"price": 9000, "min_quantity": 10}):
        await PL.add_item_to_price_list(req(tid, uid), pl, PriceListItemCreate(item_id=str(brg["id"]), **kw))
    awal = await baris(pool, tid, pl)
    B = BP.BulkHargaRequest
    # ---- G1 pratinjau
    body = B(mode="persen", nilai=Decimal("10"), berlaku_mulai=t2, item_ids=[str(brg["id"])])
    p1 = (await BP.bulk_price_preview(req(tid, uid), pl, body))["data"]
    p1b = (await BP.bulk_price_preview(req(tid, uid), pl, body))["data"]
    rb = [(x["min_quantity"], float(x["dari"]), float(x["ke"])) for x in p1["items"][0]["preview"]["baris"]]
    cek(f"{tid} G1 pratinjau +10% berlaku T2: 2 baris sumber (qty1 dari B 11000 -> 12100, qty10 9000 -> 9900), A tak ikut; ulang = hasil sama",
        rb == [(1.0, 11000.0, 12100.0), (10.0, 9000.0, 9900.0)] and p1["items"][0]["preview"] == p1b["items"][0]["preview"], f"{rb}")
    # ---- G2 tulis
    await BP.bulk_price(req(tid, uid, f"uji-d3b-1-{st}"), pl, body)
    sesudah = await baris(pool, tid, pl)
    baru = [x for x in sesudah if x[1] == str(t2)]
    cek(f"{tid} G2 tulis: tepat 2 baris baru bertanggal T2 (qty1 12100, qty10 9900); A dan B tak berubah",
        len(baru) == 2 and sorted(baru) == [(1.0, str(t2), 12100.0), (10.0, str(t2), 9900.0)] and all(x in sesudah for x in awal), f"{baru}")
    # ---- G3 langsung
    await BP.bulk_price(req(tid, uid, f"uji-d3b-2-{st}"), pl, B(mode="persen", nilai=Decimal("10"), item_ids=[str(brg["id"])]))
    akhir = await baris(pool, tid, pl)
    cek(f"{tid} G3 ubah langsung +10%: hanya yang berlaku hari ini (A 10000->11000, C 9000->9900); B (mulai T1) dan baris T2 tak berubah",
        (1.0, "None", 11000.0) in akhir and (10.0, "None", 9900.0) in akhir and (1.0, str(t1), 11000.0) in akhir and sorted(x for x in akhir if x[1] == str(t2)) == sorted(baru), f"{akhir}")
    # ---- G4 tak berlaku
    r2 = await PL.create_price_list(req(tid, uid, f"uji-d3b-buat2-{st}"), CreatePriceListRequest(code=f"UJI-D3B2-{st}", name="UJI D3 tak berlaku"))
    pl2 = uuid.UUID(str(r2["data"]["id"]))
    await PL.add_item_to_price_list(req(tid, uid), pl2, PriceListItemCreate(item_id=str(brg["id"]), price=5000, min_quantity=1, start_date=t2 + timedelta(days=30)))
    p4 = (await BP.bulk_price_preview(req(tid, uid), pl2, B(mode="persen", nilai=Decimal("10"), item_ids=[str(brg["id"])])))["data"]
    cek(f"{tid} G4 semua baris belum berlaku pada tanggal itu: blok DH_TAK_BERLAKU_PADA_TANGGAL", not p4["items"][0]["ok"] and p4["items"][0]["blocks"][0]["code"] == "DH_TAK_BERLAKU_PADA_TANGGAL", f"{p4['items'][0]['blocks']}")


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
        if not db.startswith("scratch_dhd3b"):
            print(f"TOLAK: bukan salinan (pool={db})")
            return 2
        await c.execute((MIG / "V500__daftar_harga_berjangka.sql").read_text())
        await c.execute((MIG / "V501__price_list_items_min_price.sql").read_text())
    for tid in TENANT:
        await periksa(pool, tid)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
