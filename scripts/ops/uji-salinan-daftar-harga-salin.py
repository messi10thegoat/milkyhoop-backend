"""Daftar Harga SALIN (BUG-256) -- uji di SALINAN (klon milkydb); tulis HANYA di salinan. GUARD: pool harus scratch_dhsln*.
V500 + V501 dipasang di salinan (cabang turunan 245/040). audit_logs ASLI.
S1 salin: jumlah baris sama, SETIAP baris setara (harga, qty, diskon, minimum, mulai, berakhir, satuan), bukan bawaan, tak terikat pelanggan, kode/nama baru.
S2 pratinjau READ ONLY (jumlah daftar tak berubah) + hitungan benar; kode ganda = blok di pratinjau dan 409 di tulis.
S3 idempoten: kunci sama = respons asli, satu daftar saja; tanpa kunci 400 SALIN_KUNCI_WAJIB.
S4 riwayat 252: event BUAT 'Disalin dari ...' pada daftar BARU; daftar sumber tanpa event baru.
S5 sumber utuh sesudah salin; mengubah baris salinan tak mengubah sumber; tenant lain 404."""
import asyncio
import os
import pathlib
import sys
import time
import uuid
from datetime import date, timedelta

from fastapi import HTTPException, Response
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import bulk_price_lists as BP  # noqa: E402
from app.routers import price_lists as PL  # noqa: E402
from app.schemas.price_lists import CreatePriceListRequest, PriceListItemCreate, PriceListItemUpdate  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402

HASIL = []
TENANT = tuple(x for x in os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(",") if x)
MIG = pathlib.Path("/app/backend/migrations")


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, uid, kunci=None, method="POST"):
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": method, "path": "/api/price-lists", "headers": h})
    r.state.user = {"tenant_id": tid, "user_id": uid, "username": "uji-dh-sln"}
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
        return sorted([(str(r["item_id"]), r["unit"] or "", float(r["min_quantity"]), float(r["price"]), float(r["min_price"]) if r["min_price"] is not None else None,
                        float(r["discount_percent"]) if r["discount_percent"] is not None else None, str(r["start_date"]), str(r["end_date"])) for r in await c.fetch(
            """SELECT pli.item_id, pli.unit, pli.min_quantity, pli.price, pli.min_price, pli.discount_percent, pli.start_date, pli.end_date FROM price_list_items pli
               JOIN price_lists p ON p.id = pli.price_list_id WHERE p.tenant_id = $1 AND pli.price_list_id = $2::uuid""", tid, str(pl))])


async def n_daftar(pool, tid):
    async with pool.acquire() as c:
        return await c.fetchval("SELECT count(*) FROM price_lists WHERE tenant_id = $1", tid)


async def periksa(pool, tid):
    st = int(time.time())
    async with pool.acquire() as c:
        brg = await c.fetchrow("""SELECT id, COALESCE(item_code, sku) AS kode FROM products p WHERE tenant_id = $1 AND status = 'active' AND COALESCE(for_sales, true)
                                  AND deleted_at IS NULL AND COALESCE(item_code, sku) IS NOT NULL ORDER BY id LIMIT 1""", tid)
        user = await c.fetchrow('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY id LIMIT 1', tid)
    if brg is None or user is None:
        print(f"INFO {tid}: tak ada barang berkode / pengguna -- dilewati")
        return
    uid = user["id"]
    r = await PL.create_price_list(req(tid, uid, f"uji-sln-buat-{st}"), CreatePriceListRequest(code=f"UJI-SLN-{st}", name="UJI sumber salin"))
    src = uuid.UUID(str(r["data"]["id"]))
    t1 = date.today() + timedelta(days=30)
    for kw in ({"price": 10000, "min_quantity": 1, "min_price": 9000, "discount_percent": 0}, {"price": 10000, "min_quantity": 10, "discount_percent": 5, "end_date": date.today() + timedelta(days=90)},
               {"price": 11000, "min_quantity": 10, "discount_percent": 8, "start_date": t1}):
        await PL.add_item_to_price_list(req(tid, uid), src, PriceListItemCreate(item_id=str(brg["id"]), **kw))
    sebelum = await baris(pool, tid, src)
    n0 = await n_daftar(pool, tid)
    # ---- S2 pratinjau
    kode = f"UJI-SLN2-{st}"
    p = (await BP.pratinjau_salin(req(tid, uid), src, {"code": kode, "name": " Salinan  UJI "}))["data"]
    ganda = (await BP.pratinjau_salin(req(tid, uid), src, {"code": f"UJI-SLN-{st}", "name": "x"}))["data"]
    cek(f"{tid} S2a pratinjau READ ONLY: daftar tak bertambah; hitungan 3 baris / 1 barang / 1 berjangka; kode+nama dirapikan",
        await n_daftar(pool, tid) == n0 and p["ok"] and p["rencana"]["jumlah_baris"] == 3 and p["rencana"]["jumlah_barang"] == 1 and p["rencana"]["jumlah_berjangka"] == 1 and p["payload"]["name"] == "Salinan UJI", f"{p['rencana']}")
    cek(f"{tid} S2b kode ganda: pratinjau blok KODE_DAFTAR_GANDA", not ganda["ok"] and ganda["blocks"][0]["code"] == "KODE_DAFTAR_GANDA")
    # ---- S1 salin + S3 idempoten
    k = f"uji-sln-1-{st}"
    s1 = await BP.salin_daftar(req(tid, uid, k), src, p["payload"], Response())
    baru = uuid.UUID(s1["data"]["id"])
    s1b = await BP.salin_daftar(req(tid, uid, k), src, p["payload"], Response())
    sesudah = await baris(pool, tid, baru)
    async with pool.acquire() as c:
        h = await c.fetchrow("SELECT code, name, is_default, is_active FROM price_lists WHERE id = $1 AND tenant_id = $2", baru, tid)
        pel = await c.fetchval("SELECT count(*) FROM customers WHERE tenant_id = $1 AND price_list_id = $2", tid, baru)
    cek(f"{tid} S1 salin: 3 baris setara persis (harga/qty/diskon 0 dan 5 dan 8/minimum/mulai/berakhir), kode+nama baru, bukan bawaan, tanpa pelanggan",
        sesudah == sebelum and len(sesudah) == 3 and h["code"] == kode and h["name"] == "Salinan UJI" and not h["is_default"] and pel == 0, f"{len(sesudah)} {dict(h)}")
    cek(f"{tid} S3a idempoten: kunci sama = respons asli, hanya satu daftar baru", s1b == s1 and await n_daftar(pool, tid) == n0 + 1)
    tanpa = await galat(BP.salin_daftar(req(tid, uid), src, {"code": f"UJI-SLN3-{st}", "name": "y"}, Response()))
    dobel = await galat(BP.salin_daftar(req(tid, uid, f"uji-sln-2-{st}"), src, {"code": kode, "name": "z"}, Response()))
    cek(f"{tid} S3b tanpa kunci = 400 SALIN_KUNCI_WAJIB; tulis kode ganda = 409 KODE_DAFTAR_GANDA", tanpa == ("SALIN_KUNCI_WAJIB", 400) and dobel == ("KODE_DAFTAR_GANDA", 409), f"{tanpa} {dobel}")
    # ---- S4 riwayat
    rb = await BP.riwayat_daftar(req(tid, uid, method="GET"), baru, limit=50, offset=0, aksi=None, dari=None, sampai=None)
    rs = await BP.riwayat_daftar(req(tid, uid, method="GET"), src, limit=50, offset=0, aksi=None, dari=None, sampai=None)
    cek(f"{tid} S4 riwayat: daftar baru punya BUAT 'Disalin dari' dengan 3 baris; sumber tanpa event salin",
        rb["data"]["items"][0]["aksi"] == "BUAT" and "Disalin dari" in rb["data"]["items"][0]["ringkas"] and len(rb["data"]["items"][0]["baris"]) == 3
        and not [i for i in rs["data"]["items"] if "Disalin" in (i["ringkas"] or "")], f"{rb['data']['items'][0]['ringkas']}")
    # ---- S5 sumber utuh + independen + tenant lain
    async with pool.acquire() as c:
        line = await c.fetchval("SELECT pli.id FROM price_list_items pli JOIN price_lists p ON p.id = pli.price_list_id WHERE p.tenant_id = $1 AND pli.price_list_id = $2 AND pli.min_quantity = 1", tid, baru)
    await PL.update_price_list_item(req(tid, uid), baru, line, PriceListItemUpdate(price=12345))
    cek(f"{tid} S5a mengubah baris salinan tak mengubah sumber", await baris(pool, tid, src) == sebelum and await baris(pool, tid, baru) != sebelum)
    ap = (await BP.pratinjau_salin(req("tenant-tak-ada", uid), src, {"code": "A", "name": "B"}))["data"]
    cek(f"{tid} S5b daftar tenant lain: pratinjau blok DH_TAK_ADA (tak bocor)", not ap["ok"] and ap["blocks"][0]["code"] == "DH_TAK_ADA", f"{ap['blocks']}")


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
        if not db.startswith("scratch_dhsln"):
            print(f"TOLAK: bukan salinan (pool={db})")
            return 2
        await c.execute((MIG / "V500__daftar_harga_berjangka.sql").read_text())
        await c.execute((MIG / "V501__price_list_items_min_price.sql").read_text())
    for tid in TENANT:
        await periksa(pool, tid)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
