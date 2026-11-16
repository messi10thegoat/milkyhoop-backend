"""Daftar Harga HARGA MINIMUM (BUG-040, V501) -- uji di SALINAN (klon milkydb); tulis HANYA di salinan. GUARD: pool harus scratch_dhmin*.
V500 + V501 dipasang di salinan (cabang turunan 245 + 251).
M0 V501 maju: kolom min_price ada, CHECK menolak negatif; ROLLBACK membuang kolom; maju lagi.
M1 tambah baris min_price <= harga OK; min > harga = 400 HARGA_MINIMUM_DI_ATAS_HARGA; detail GET membawa min_price.
M2 resolve harga_saran membawa harga_minimum baris yang menang.
M3 impor 251: kolom Harga minimum mengubah lantai di DB; ekspor memuatnya.
M4 peringatan_jual (DB nyata): harga < lantai = 1 peringatan JUAL_DI_BAWAH_MINIMUM butuh_konfirmasi; harga >= lantai = 0; harga SAMA dgn SO asal = 0.
M5 D3 massal: pratinjau harga baru < lantai = DH_DI_BAWAH_MINIMUM; tulis berjangka membawa lantai ke baris baru."""
import asyncio
import json
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
from app.services import harga_minimum as HMN  # noqa: E402
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
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-dh-min"}
    return r


async def galat(coro):
    try:
        await coro
    except HTTPException as e:
        d = e.detail
        return (d.get("code") if isinstance(d, dict) else getattr(e, "code", None)), e.status_code
    return None


async def kolom_ada(pool):
    async with pool.acquire() as c:
        return bool(await c.fetchval("SELECT count(*) FROM pg_catalog.pg_attribute WHERE attrelid = 'price_list_items'::regclass AND attname = 'min_price' AND NOT attisdropped"))


async def baris_db(pool, tid, pl):
    async with pool.acquire() as c:
        return [(r["price"], r["min_price"], r["start_date"]) for r in await c.fetch(
            """SELECT pli.price, pli.min_price, pli.start_date FROM price_list_items pli JOIN price_lists p ON p.id = pli.price_list_id
               WHERE p.tenant_id = $1 AND pli.price_list_id = $2::uuid ORDER BY pli.start_date NULLS FIRST, pli.price""", tid, pl)]


async def periksa(pool, tid):
    st = int(time.time())
    async with pool.acquire() as c:
        brg = await c.fetchrow("""SELECT id, COALESCE(item_code, sku) AS kode FROM products p WHERE tenant_id = $1 AND status = 'active' AND COALESCE(for_sales, true)
                                  AND deleted_at IS NULL AND COALESCE(item_code, sku) IS NOT NULL
                                  AND (SELECT count(*) FROM products q WHERE q.tenant_id = $1 AND (lower(q.item_code) = lower(COALESCE(p.item_code, p.sku))
                                       OR lower(q.sku) = lower(COALESCE(p.item_code, p.sku)))) = 1 ORDER BY id LIMIT 1""", tid)
        cust = await c.fetchval("SELECT id FROM customers WHERE tenant_id = $1 ORDER BY id LIMIT 1", tid)
    if brg is None:
        print(f"INFO {tid}: tak ada barang berkode unik -- dilewati")
        return None
    r = await PL.create_price_list(req(tid, f"uji-min-buat-{st}"), CreatePriceListRequest(code=f"UJI-MIN-{st}", name="UJI minimum"))
    pl = uuid.UUID(str(r["data"]["id"]))
    # ---- M1
    ok = await PL.add_item_to_price_list(req(tid), pl, PriceListItemCreate(item_id=str(brg["id"]), price=10000, min_quantity=1, min_price=9500))
    ditolak = await galat(PL.add_item_to_price_list(req(tid), pl, PriceListItemCreate(item_id=str(brg["id"]), price=10000, min_quantity=5, min_price=10001)))
    detail = await PL.get_price_list(req(tid, method="GET"), pl)
    mp = [i["min_price"] for i in detail["data"]["items"]]
    cek(f"{tid} M1 min<=harga OK; min>harga 400 HARGA_MINIMUM_DI_ATAS_HARGA; detail membawa min_price",
        ok is not None and ditolak == ("HARGA_MINIMUM_DI_ATAS_HARGA", 400) and mp == [9500], f"{ditolak} {mp}")
    # ---- M2 resolve
    async def daftar_fake(conn, tid_, customer_id, tanggal):
        return {"id": pl, "code": "UJI", "name": "UJI", "asal": "bawaan"}
    asli = HJ.daftar_untuk
    HJ.daftar_untuk = daftar_fake
    try:
        async with pool.acquire() as c:
            x = await HJ.harga_saran(c, tid, None, date.today(), [{"key": "k", "item_id": str(brg["id"]), "unit": None, "quantity": 1}])
        ln = x["lines"][0]
        cek(f"{tid} M2 resolve membawa harga_minimum baris yang menang", ln["harga"] == 10000.0 and ln["harga_minimum"] == 9500.0, f"{ln['harga']} {ln['harga_minimum']}")
        # ---- M4 peringatan DB nyata (daftar bawaan palsu = daftar uji; tanpa pelanggan terikat)
        async with pool.acquire() as c:
            def lines(nilai, up=None):
                return [{"baris": 1, "item_id": str(brg["id"]), "quantity": 1, "unit": None, "nilai": Decimal(nilai), "unit_price": Decimal(up if up is not None else nilai)}]
            rendah = await HMN.hitung(c, tid, cust, date.today(), lines(9000))
            aman = await HMN.hitung(c, tid, cust, date.today(), lines(9500))
            sama_so = await HMN.hitung(c, tid, cust, date.today(), lines(9000), {str(brg["id"]): Decimal(9000)})
        cek(f"{tid} M4 peringatan_jual DB nyata: 9.000<9.500 = 1 JUAL_DI_BAWAH_MINIMUM butuh_konfirmasi; 9.500 = 0; harga sama SO asal = 0",
            len(rendah) == 1 and rendah[0]["code"] == "JUAL_DI_BAWAH_MINIMUM" and rendah[0]["butuh_konfirmasi"] is True and rendah[0]["selisih"] == 500.0
            and aman == [] and sama_so == [], f"{rendah} {aman} {sama_so}")
    finally:
        HJ.daftar_untuk = asli
    # ---- M3 impor 251
    ekspor = await PL.ekspor_harga(req(tid, method="GET"), pl, "xlsx")
    baca = IH.baca_xlsx(ekspor.body)
    ubah = [dict(b) for b in baca["baris"]]
    ubah[0]["min_harga"] = 9000
    p = (await PL.pratinjau_impor_harga(req(tid), pl, {"baris": ubah}))["data"]
    await PL.impor_harga(req(tid, f"uji-min-imp-{st}"), pl, p["payload"], Response())
    cek(f"{tid} M3 impor kolom Harga minimum mengubah lantai (9.500 -> 9.000) dan ekspor memuatnya",
        any(m == "min_harga" for m in [x["medan"] for x in baca["pemetaan"]]) and [b[1] for b in await baris_db(pool, tid, str(pl))] == [Decimal("9000")],
        f"{await baris_db(pool, tid, str(pl))}")
    # ---- M5 D3
    B = BP.BulkHargaRequest
    pr = await BP.bulk_price_preview(req(tid), pl, B(mode="persen", nilai=Decimal("-50")))
    cek(f"{tid} M5a D3 pratinjau -50%: harga baru di bawah lantai = peringatan DH_DI_BAWAH_MINIMUM", "DH_DI_BAWAH_MINIMUM" in json.dumps(pr, default=str))
    t2 = date.today() + timedelta(days=60)
    await BP.bulk_price(req(tid, f"uji-min-d3-{st}"), pl, B(mode="persen", nilai=Decimal("10"), berlaku_mulai=t2))
    bs = await baris_db(pool, tid, str(pl))
    cek(f"{tid} M5b D3 berjangka: baris baru membawa lantai baris lama", len(bs) == 2 and all(b[1] == Decimal("9000") for b in bs), f"{bs}")
    return pl


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
        if not db.startswith("scratch_dhmin"):
            print(f"TOLAK: bukan salinan (pool={db})")
            return 2
        await c.execute((MIG / "V500__daftar_harga_berjangka.sql").read_text())
        await c.execute((MIG / "V501__price_list_items_min_price.sql").read_text())
    cek("M0a V501 maju: kolom min_price ada", await kolom_ada(pool))
    daftar = []
    for tid in TENANT:
        daftar.append((tid, await periksa(pool, tid)))
    async with pool.acquire() as c:
        try:
            async with c.transaction():
                await c.execute("""INSERT INTO price_list_items (price_list_id, item_id, price, min_quantity, min_price)
                                   SELECT pli.price_list_id, pli.item_id, 1, 77, -1 FROM price_list_items pli JOIN price_lists p ON p.id = pli.price_list_id
                                   WHERE p.tenant_id = $1 LIMIT 1""", TENANT[0])
            neg = False
        except Exception as e:  # noqa: BLE001
            neg = "chk_price_list_items_min_price_nonneg" in str(e)
    cek("M0b CHECK menolak min_price negatif", neg)
    async with pool.acquire() as c:
        for tid, pl in daftar:
            if pl:
                await c.execute("DELETE FROM price_list_items WHERE price_list_id IN (SELECT id FROM price_lists WHERE id = $1::uuid AND tenant_id = $2)", str(pl), tid)
        await c.execute((MIG / "V501__price_list_items_min_price_ROLLBACK.sql").read_text())
    cek("M0c ROLLBACK V501: kolom hilang", not await kolom_ada(pool))
    async with pool.acquire() as c:
        await c.execute((MIG / "V501__price_list_items_min_price.sql").read_text())
    cek("M0d maju lagi: kolom kembali", await kolom_ada(pool))
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
