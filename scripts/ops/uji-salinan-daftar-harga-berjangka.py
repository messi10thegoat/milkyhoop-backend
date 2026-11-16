"""Daftar Harga BERJANGKA (BUG-245, V500) -- uji di SALINAN (klon milkydb); tulis HANYA di salinan. GUARD: pool harus scratch_dhbj*.
H0 V500 maju dipasang di salinan: indeks uq_price_list_items_berjangka ada, uq_price_list_items_satuan tak ada.
H1 kunci sama + tanggal mulai beda = boleh; kunci + tanggal sama = HARGA_GANDA 409; kunci sama tanpa tanggal dua kali = HARGA_GANDA.
H2 resolve harga_saran: sebelum tanggal mulai = harga lama; pada/sesudah = harga baru (mulai terbaru <= tanggal dokumen).
H3 D3 massal tanggal masa depan: baris BERTAMBAH (berjangka), harga lama tak berubah, baris baru harga hasil + start_date; ulang tanggal sama = tak menambah baris.
H4 pratinjau D3 READ ONLY: jumlah baris + harga tak berubah.
H5 ROLLBACK V500 (setelah baris berjangka uji dihapus) mengembalikan indeks lama; maju lagi memasang indeks baru."""
import asyncio
import os
import pathlib
import sys
import time
import uuid
from datetime import date, timedelta
from decimal import Decimal

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import bulk_price_lists as BP  # noqa: E402
from app.routers import price_lists as PL  # noqa: E402
from app.schemas.price_lists import CreatePriceListRequest, PriceListItemCreate  # noqa: E402
from app.services import harga_jual as HJ  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402

HASIL = []
TENANT = tuple(x for x in os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(",") if x)
MIG = pathlib.Path("/app/backend/migrations")


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, kunci=None):
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": "POST", "path": "/api/price-lists", "headers": h})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-dh-bj"}
    return r


async def galat(coro):
    try:
        await coro
    except HTTPException as e:
        d = e.detail
        return (d.get("code") if isinstance(d, dict) else getattr(e, "code", None)), e.status_code
    return None


async def baris(pool, tid, pl):
    async with pool.acquire() as c:
        return [(r["price"], r["start_date"]) for r in await c.fetch(
            """SELECT pli.price, pli.start_date FROM price_list_items pli JOIN price_lists p ON p.id = pli.price_list_id
               WHERE p.tenant_id = $1 AND pli.price_list_id = $2::uuid ORDER BY pli.start_date NULLS FIRST, pli.price""", tid, pl)]


async def indeks(pool):
    async with pool.acquire() as c:
        return {r["indexname"] for r in await c.fetch("SELECT indexname FROM pg_catalog.pg_indexes WHERE tablename = 'price_list_items'")}


async def periksa(pool, tid):
    st = int(time.time())
    async with pool.acquire() as c:
        brg = await c.fetchval("""SELECT id FROM products WHERE tenant_id = $1 AND status = 'active' AND COALESCE(for_sales, true)
                                  AND deleted_at IS NULL ORDER BY id LIMIT 1""", tid)
    r = await PL.create_price_list(req(tid, f"uji-bj-buat-{st}"), CreatePriceListRequest(code=f"UJI-BJ-{st}", name="UJI berjangka"))  # dengan kunci seperti FE (BUG-250)
    pl = str(r["data"]["id"])
    t1, t2 = date.today() + timedelta(days=30), date.today() + timedelta(days=60)

    def item(harga, mulai):
        return PriceListItemCreate(item_id=str(brg), price=harga, min_quantity=1, start_date=mulai)
    await PL.add_item_to_price_list(req(tid), uuid.UUID(pl), item(1000, None))
    ok_baru = await galat(PL.add_item_to_price_list(req(tid), uuid.UUID(pl), item(1200, t1)))
    ganda_tgl = await galat(PL.add_item_to_price_list(req(tid), uuid.UUID(pl), item(1300, t1)))
    ganda_nol = await galat(PL.add_item_to_price_list(req(tid), uuid.UUID(pl), item(1400, None)))
    cek(f"{tid} H1 berjangka: tanggal beda boleh; kunci+tanggal sama dan tanpa-tanggal dua kali = HARGA_GANDA",
        ok_baru is None and ganda_tgl == ("HARGA_GANDA", 409) and ganda_nol == ("HARGA_GANDA", 409), f"{ok_baru} {ganda_tgl} {ganda_nol}")
    # ---- H2
    async def daftar_fake(conn, tid_, customer_id, tanggal):
        return {"id": uuid.UUID(pl), "code": "UJI", "name": "UJI", "asal": "bawaan"}
    asli = HJ.daftar_untuk
    HJ.daftar_untuk = daftar_fake
    try:
        async def saran(tgl):
            async with pool.acquire() as c:
                x = await HJ.harga_saran(c, tid, None, tgl, [{"key": "k", "item_id": str(brg), "unit": None, "quantity": 1}])
            return x["lines"][0]["harga"]
        sebelum, pada, sesudah = await saran(t1 - timedelta(days=1)), await saran(t1), await saran(t2)
    finally:
        HJ.daftar_untuk = asli
    cek(f"{tid} H2 resolve: sebelum tanggal mulai 1000, pada/sesudah 1200 (mulai terbaru <= tanggal dokumen)",
        (sebelum, pada, sesudah) == (1000.0, 1200.0, 1200.0), f"{(sebelum, pada, sesudah)}")
    # ---- H3 + H4
    B = BP.BulkHargaRequest
    n0, h0 = len(await baris(pool, tid, pl)), await baris(pool, tid, pl)
    await BP.bulk_price_preview(req(tid), uuid.UUID(pl), B(mode="persen", nilai=Decimal("10"), berlaku_mulai=t2))
    cek(f"{tid} H4 pratinjau D3 read-only: baris + harga tak berubah", await baris(pool, tid, pl) == h0)
    await BP.bulk_price(req(tid, f"uji-bj-{st}"), uuid.UUID(pl), B(mode="persen", nilai=Decimal("10"), berlaku_mulai=t2))
    b1 = await baris(pool, tid, pl)
    lama_utuh = all(x in b1 for x in h0)
    baru = [x for x in b1 if x not in h0]
    cek(f"{tid} H3 D3 masa depan: baris bertambah ({n0}->{len(b1)}), harga lama utuh, baris baru start={t2}",
        len(b1) > n0 and lama_utuh and baru and all(x[1] == t2 for x in baru), f"baru={baru}")
    await BP.bulk_price(req(tid, f"uji-bj2-{st}"), uuid.UUID(pl), B(mode="persen", nilai=Decimal("5"), berlaku_mulai=t2))
    cek(f"{tid} H3b tanggal sama diulang = memperbarui baris itu, tak menambah", len(await baris(pool, tid, pl)) == len(b1))
    return pl


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
        if not db.startswith("scratch_dhbj"):
            print(f"TOLAK: bukan salinan (pool={db})")
            return 2
        await c.execute((MIG / "V500__daftar_harga_berjangka.sql").read_text())
        await c.execute((MIG / "V501__price_list_items_min_price.sql").read_text())   # puncak rantai: penulis baris/resolve membaca min_price
    ix = await indeks(pool)
    cek("H0 V500 maju: indeks berjangka ada, indeks lama tak ada", "uq_price_list_items_berjangka" in ix and "uq_price_list_items_satuan" not in ix)
    daftar = []
    for tid in TENANT:
        daftar.append((tid, await periksa(pool, tid)))
    async with pool.acquire() as c:
        for tid, pl in daftar:  # bersihkan baris uji: ROLLBACK hanya aman tanpa kunci ganda berjangka
            await c.execute("DELETE FROM price_list_items WHERE price_list_id IN (SELECT id FROM price_lists WHERE id = $1::uuid AND tenant_id = $2)", pl, tid)
        await c.execute((MIG / "V500__daftar_harga_berjangka_ROLLBACK.sql").read_text())
    ix = await indeks(pool)
    cek("H5 ROLLBACK: indeks lama kembali, indeks berjangka hilang", "uq_price_list_items_satuan" in ix and "uq_price_list_items_berjangka" not in ix)
    async with pool.acquire() as c:
        await c.execute((MIG / "V500__daftar_harga_berjangka.sql").read_text())
    ix = await indeks(pool)
    cek("H5b maju lagi: indeks berjangka kembali", "uq_price_list_items_berjangka" in ix and "uq_price_list_items_satuan" not in ix)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
