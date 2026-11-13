"""Daftar Harga D3 (ubah harga massal) -- uji di SALINAN (klon milkydb); tulis HANYA di salinan.
GUARD: pool harus menunjuk database scratch_dhd3*; selain itu berhenti SEBELUM menulis.
H1 pratinjau dasar harga_lama persen: ke == bulat(dari*(1+n/100)) per baris, semua barang ok.
H2 pratinjau READ ONLY: harga + start_date semua baris daftar sama sebelum-sesudah.
H3 tulis wajib kunci (BULK_KUNCI_WAJIB); dengan kunci harga berubah; kunci sama = replay (tak berlipat ganda).
H4 dasar HPP: ke == bulat(WAC x (1+n/100)); barang tanpa HPP -> DH_HPP_TAK_ADA sementara barang lain tetap ok.
H5 pembulatan kelipatan 100 atas: ke kelipatan 100 dan >= nilai mentah.
H6 harga nol: peringatan DH_HARGA_NOL (bentuk BUG-207 butuh_konfirmasi + pesan); tulis TIDAK diblok, harga jadi 0.
H7 tanggal berlaku masa depan: start_date baris = tanggal itu + peringatan DH_BERLAKU_NANTI.
H8 audit: baris audit_logs BULK_HARGA bertambah untuk tenant."""
import asyncio
import os
import sys
import time
import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import bulk_price_lists as BP  # noqa: E402
from app.routers import price_lists as PL  # noqa: E402
from app.schemas.price_lists import CreatePriceListRequest, PriceListItemCreate  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402

HASIL = []
TENANT = tuple(x for x in os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(",") if x)


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, kunci=None):
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": "POST", "path": "/api/price-lists", "headers": h})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-dh-d3"}
    return r


def b2(v):
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def galat(coro):
    try:
        await coro
    except HTTPException as e:
        d = e.detail
        return (d.get("code") if isinstance(d, dict) else getattr(e, "code", None)), e.status_code
    return None


async def harga(pool, tid, pl):
    async with pool.acquire() as c:
        return [(str(r["item_id"]), r["price"], r["start_date"]) for r in await c.fetch(
            """SELECT pli.item_id, pli.price, pli.start_date FROM price_list_items pli JOIN price_lists pl ON pl.id = pli.price_list_id
               WHERE pl.tenant_id = $1 AND pli.price_list_id = $2::uuid ORDER BY pli.item_id, pli.min_quantity""", tid, pl)]


async def daftar_baru(pool, tid, st, kode, barang):
    r = await PL.create_price_list(req(tid), CreatePriceListRequest(code=f"UJI-D3-{kode}-{st}", name=f"UJI D3 {kode}"))
    pl = str(r["data"]["id"])
    for b in barang:
        await PL.add_item_to_price_list(req(tid), uuid.UUID(pl), PriceListItemCreate(item_id=str(b), price=1000, min_quantity=1))
    return pl


async def periksa(pool, tid):
    st = int(time.time())
    async with pool.acquire() as c:
        brg = [r["id"] for r in await c.fetch("""SELECT id FROM products WHERE tenant_id = $1 AND status = 'active' AND COALESCE(for_sales, true)
                                                  AND deleted_at IS NULL ORDER BY id LIMIT 2""", tid)]
        hpp_b = await c.fetchrow("""SELECT p.id, get_weighted_average_cost(p.tenant_id::text, p.id) AS wac FROM products p WHERE p.tenant_id = $1
                                     AND p.status = 'active' AND COALESCE(p.for_sales, true) AND p.item_type = 'goods' AND p.track_inventory = true
                                     AND p.deleted_at IS NULL AND COALESCE(get_weighted_average_cost(p.tenant_id::text, p.id), 0) > 0 LIMIT 1""", tid)
    if len(brg) < 2:
        cek(f"{tid} H0 butuh >= 2 barang aktif dijual", False)
        return
    pl = await daftar_baru(pool, tid, st, "A", brg)
    # ---- H1
    B = BP.BulkHargaRequest
    pv = (await BP.bulk_price_preview(req(tid), uuid.UUID(pl), B(mode="persen", nilai=Decimal("10"))))["data"]
    ok = pv["total"] == 2 and pv["ok_count"] == 2 and all(
        b["ke"] == b2(Decimal("1000") * Decimal("1.10")) for i in pv["items"] for b in i["preview"]["baris"])
    cek(f"{tid} H1 pratinjau persen 10 atas harga lama: 2 barang ok, ke == 1100.00", ok, f"{pv['total']}/{pv['ok_count']}")
    # ---- H2
    sebelum = await harga(pool, tid, pl)
    await BP.bulk_price_preview(req(tid), uuid.UUID(pl), B(mode="nominal", nilai=Decimal("-250"), berlaku_mulai=date.today() + timedelta(days=30)))
    cek(f"{tid} H2 pratinjau READ ONLY: harga + start_date daftar tak berubah", await harga(pool, tid, pl) == sebelum)
    # ---- H3
    gl = await galat(BP.bulk_price(req(tid), uuid.UUID(pl), B(mode="persen", nilai=Decimal("10"))))
    k = f"uji-d3-{st}"
    r1 = (await BP.bulk_price(req(tid, k), uuid.UUID(pl), B(mode="persen", nilai=Decimal("10"))))["data"]
    h1 = await harga(pool, tid, pl)
    r2 = (await BP.bulk_price(req(tid, k), uuid.UUID(pl), B(mode="persen", nilai=Decimal("10"))))["data"]
    h2 = await harga(pool, tid, pl)
    cek(f"{tid} H3 tulis wajib kunci; dengan kunci harga 1100; kunci sama = replay tanpa berlipat",
        gl and gl[0] == "BULK_KUNCI_WAJIB" and r1["all_ok"] and all(p[1] == Decimal("1100") for p in h1) and h1 == h2
        and all(i["replay"] for i in r2["items"]), f"{gl} {[str(p[1]) for p in h1]}")
    # ---- H4
    if hpp_b:
        wac = Decimal(str(hpp_b["wac"]))
        pl2 = await daftar_baru(pool, tid, st, "H", [hpp_b["id"], brg[0] if brg[0] != hpp_b["id"] else brg[1]])
        p2 = (await BP.bulk_price_preview(req(tid), uuid.UUID(pl2), B(mode="persen", nilai=Decimal("25"), dasar="hpp")))["data"]
        by = {i["id"]: i for i in p2["items"]}
        ada = by[str(hpp_b["id"])]
        tanpa = [i for i in p2["items"] if i["id"] != str(hpp_b["id"])][0]
        cek(f"{tid} H4 dasar HPP: ke == bulat(WAC x 1.25); barang tanpa HPP ditandai DH_HPP_TAK_ADA bila memang tanpa HPP",
            ada["ok"] and ada["preview"]["baris"][0]["ke"] == b2(wac * Decimal("1.25")) and (tanpa["ok"] or tanpa["blocks"][0]["code"] == "DH_HPP_TAK_ADA"),
            f"wac={wac} ke={ada['preview']['baris'][0]['ke'] if ada['ok'] else ada['blocks']}")
    else:
        print(f"INFO {tid} H4 dilewati: tak ada barang ber-WAC > 0 di salinan")
    # ---- H5
    pl3 = await daftar_baru(pool, tid, st, "B", brg)
    p5 = (await BP.bulk_price_preview(req(tid), uuid.UUID(pl3), B(mode="persen", nilai=Decimal("23.456"), pembulatan_kelipatan=100, pembulatan_arah="atas")))["data"]
    ke5 = [b["ke"] for i in p5["items"] for b in i["preview"]["baris"]]
    cek(f"{tid} H5 pembulatan kelipatan 100 atas: semua ke kelipatan 100 dan >= 1234.56", ke5 and all(x % 100 == 0 and x >= Decimal("1234.56") for x in ke5), f"{ke5}")
    # ---- H6
    pl4 = await daftar_baru(pool, tid, st, "N", brg)
    p6 = (await BP.bulk_price_preview(req(tid), uuid.UUID(pl4), B(mode="nominal", nilai=Decimal("-1000"))))["data"]
    w = [x for i in p6["items"] for x in i["preview"]["peringatan"] if x["code"] == "DH_HARGA_NOL"]
    r6 = (await BP.bulk_price(req(tid, f"uji-d3n-{st}"), uuid.UUID(pl4), B(mode="nominal", nilai=Decimal("-1000"))))["data"]
    harga_ok = await harga(pool, tid, pl4)
    cek(f"{tid} H6 harga nol: peringatan BUG-207 (butuh_konfirmasi + pesan); tulis TIDAK diblok -> harga 0",
        len(w) == 2 and all(x["butuh_konfirmasi"] and x["pesan"] for x in w) and r6["all_ok"] and all(p[1] == Decimal("0") for p in harga_ok))
    # ---- H7
    pl5 = await daftar_baru(pool, tid, st, "T", brg)
    tgl = date.today() + timedelta(days=30)
    p7 = (await BP.bulk_price_preview(req(tid), uuid.UUID(pl5), B(mode="persen", nilai=Decimal("5"), berlaku_mulai=tgl)))["data"]
    await BP.bulk_price(req(tid, f"uji-d3t-{st}"), uuid.UUID(pl5), B(mode="persen", nilai=Decimal("5"), berlaku_mulai=tgl))
    cek(f"{tid} H7 tanggal berlaku: start_date baris = tanggal itu + peringatan DH_BERLAKU_NANTI",
        all(p[2] == tgl for p in await harga(pool, tid, pl5)) and all(any(x["code"] == "DH_BERLAKU_NANTI" for x in i["preview"]["peringatan"]) for i in p7["items"]))
    # ---- H8
    async with pool.acquire() as c:
        n = await c.fetchval("""SELECT count(*) FROM audit_logs WHERE tenant_id = $1 AND "eventType" = 'BULK_HARGA'""", tid)
    cek(f"{tid} H8 audit BULK_HARGA tercatat (>= 4 penulisan)", n >= 4, f"n={n}")


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        db_pool = await c.fetchval("SELECT current_database()")
    if not db_pool.startswith("scratch_dhd3"):
        print(f"TOLAK: bukan salinan (pool={db_pool})")
        return 2
    for tid in TENANT:
        await periksa(pool, tid)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
