"""Daftar Harga D1+D2 -- uji di SALINAN (klon milkydb); tulis HANYA di salinan.
GUARD: koneksi pool harus menunjuk database scratch_dhd2*; selain itu berhenti SEBELUM menulis.
H1 summary == ?aksi= (tiap kunci: {k}_count == total daftar ?aksi=k) + urutan stabil (priority, nama, id).
H2 hapus daftar dipakai pelanggan: pratinjau + penulis DITOLAK (DH_DIPAKAI_PELANGGAN, "1 pelanggan"); nonaktifkan = peringatan lalu BERHASIL;
   pelanggan dilepas -> hapus berhasil, daftar + barisnya hilang.
H3 jadikan bawaan: bawaan lama dicabut, tepat SATU bawaan aktif per tenant.
H4 tambah baris: unit_text = satuan dasar + berlaku_text "Ikut daftar"; barang tidak dijual -> DH_BARANG_TIDAK_DIJUAL (pratinjau + penulis).
H5 idempotensi: kunci sama = replay, kunci sama isi beda = ditolak.
H6 semua pratinjau READ ONLY: hitung price_lists/price_list_items/customers sebelum-sesudah sama.
H7 hapus baris: pratinjau menyebut barang + harga; penulis menghapus tepat satu baris.
H8 detail: aksi_tersedia.hapus tak tersedia + pelanggan_count bila dipakai; baris detail memuat unit_text/berlaku_text."""
import asyncio
import os
import sys
import time

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import price_lists as PL  # noqa: E402
from app.schemas.price_lists import CreatePriceListRequest, PriceListItemCreate  # noqa: E402
from app.services import harga_daftar as HD  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402

HASIL = []
TENANT = tuple(x for x in os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(",") if x)


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, kunci=None, metode="POST", path="/api/price-lists"):
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": metode, "path": path, "headers": h})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-dh-d2"}
    return r


async def galat(coro):
    try:
        await coro
    except HTTPException as e:
        return getattr(e, "code", None), str(e.detail), e.status_code
    return None


async def daftar(tid, aksi=None):
    return await PL.list_price_lists(req(tid, metode="GET"), skip=0, limit=100, search=None, is_active=None, aksi=aksi)


async def hitung(pool, tid):
    async with pool.acquire() as c:
        return (await c.fetchval("SELECT count(*) FROM price_lists WHERE tenant_id = $1", tid),
                await c.fetchval("SELECT count(*) FROM price_list_items pli JOIN price_lists pl ON pl.id = pli.price_list_id WHERE pl.tenant_id = $1", tid),
                await c.fetchval("SELECT count(*) FROM customers WHERE tenant_id = $1", tid))


async def buat_daftar(tid, kode, **kw):
    r = await PL.create_price_list(req(tid), CreatePriceListRequest(code=kode, name=f"UJI {kode}", **kw))
    return str(r["data"]["id"])


async def periksa(pool, tid):
    st = int(time.time())
    import uuid
    # ---- H1
    a = await buat_daftar(tid, f"UJI-A-{st}")
    b = await buat_daftar(tid, f"UJI-B-{st}", end_date="2020-01-01")
    ringkas = (await PL.price_lists_summary(req(tid, metode="GET")))["data"]
    ok = True
    info = []
    for k in HD.SEMUA_KUNCI:
        n = (await daftar(tid, k))["total"]
        info.append(f"{k}={n}/{ringkas[k + '_count']}")
        ok = ok and n == ringkas[k + "_count"]
    tot = (await daftar(tid))
    urut = [(x["name"], x["id"]) for x in tot["items"]]
    cek(f"{tid} H1 summary == ?aksi= tiap kunci, total_count == total, kedaluwarsa >= 1", ok and ringkas["total_count"] == tot["total"]
        and ringkas["kedaluwarsa_count"] >= 1, " ".join(info))
    cek(f"{tid} H1b urutan stabil dua kali sama", urut == [(x["name"], x["id"]) for x in (await daftar(tid))["items"]])
    sebelum = await hitung(pool, tid)
    # ---- H2
    async with pool.acquire() as c:
        cust = await c.fetchval("SELECT id FROM customers WHERE tenant_id = $1 LIMIT 1", tid)
    if cust:
        async with pool.acquire() as c:
            await c.execute("UPDATE customers SET price_list_id = $2::uuid WHERE id = $3 AND tenant_id = $1", tid, a, cust)
        pr = (await PL.preview_hapus_daftar(req(tid), uuid.UUID(a), {}))["data"]
        gl = await galat(PL.delete_price_list(req(tid, metode="DELETE"), uuid.UUID(a)))
        cek(f"{tid} H2 hapus dipakai pelanggan: pratinjau blok + penulis ditolak, menyebut 1 pelanggan",
            not pr["ok"] and pr["blocks"][0]["code"] == "DH_DIPAKAI_PELANGGAN" and "1 pelanggan" in pr["blocks"][0]["message"]
            and gl and gl[0] == "DH_DIPAKAI_PELANGGAN" and gl[2] == 409, f"{gl}")
        # H8 (daftar dipakai)
        det = (await PL.get_price_list(req(tid, metode="GET"), uuid.UUID(a)))["data"]
        cek(f"{tid} H8 detail: pelanggan_count=1 dan aksi_tersedia.hapus tak tersedia (alasan menyebut pelanggan)",
            det["pelanggan_count"] == 1 and det["aksi_tersedia"]["hapus"]["tersedia"] is False and "1 pelanggan" in det["aksi_tersedia"]["hapus"]["alasan"])
        sp = (await PL.preview_status_daftar(req(tid), uuid.UUID(a), {"aktif": False}))["data"]
        cek(f"{tid} H2b nonaktifkan = peringatan (bukan blok)", sp["ok"] and sp["peringatan"] and sp["peringatan"][0]["code"] == "DH_DIPAKAI_PELANGGAN")
        await PL.ubah_status_daftar(req(tid), uuid.UUID(a), {"aktif": False})
        async with pool.acquire() as c:
            na = await c.fetchval("SELECT is_active FROM price_lists WHERE id = $1::uuid AND tenant_id = $2", a, tid)
            await c.execute("UPDATE customers SET price_list_id = NULL WHERE id = $2 AND tenant_id = $1", tid, cust)
        await PL.delete_price_list(req(tid, metode="DELETE"), uuid.UUID(a))
        async with pool.acquire() as c:
            ada = await c.fetchval("SELECT count(*) FROM price_lists WHERE id = $1::uuid AND tenant_id = $2", a, tid)
        cek(f"{tid} H2c nonaktif berhasil (is_active=false), pelanggan dilepas -> hapus berhasil", na is False and ada == 0)
        a = await buat_daftar(tid, f"UJI-A2-{st}")
    # ---- H3
    await PL.jadikan_bawaan_daftar(req(tid), uuid.UUID(a))
    c2 = await buat_daftar(tid, f"UJI-C-{st}")
    pr = (await PL.preview_bawaan_daftar(req(tid), uuid.UUID(c2), {}))["data"]
    await PL.jadikan_bawaan_daftar(req(tid), uuid.UUID(c2))
    async with pool.acquire() as c:
        nb = await c.fetchval("SELECT count(*) FROM price_lists WHERE tenant_id = $1 AND is_default AND is_active", tid)
        wa = await c.fetchval("SELECT is_default FROM price_lists WHERE id = $1::uuid AND tenant_id = $2", a, tid)
    cek(f"{tid} H3 bawaan baru mencabut yang lama; tepat satu bawaan aktif; pratinjau menyebut bawaan lama",
        nb == 1 and wa is False and pr["ok"] and pr["rencana"]["bawaan_lama"] is not None, f"nb={nb} lama={pr['rencana']}")
    # ---- H4
    async with pool.acquire() as c:
        brg = await c.fetchrow("""SELECT id, COALESCE(NULLIF(base_unit, ''), satuan) AS dasar FROM products WHERE tenant_id = $1 AND status = 'active'
                                   AND COALESCE(for_sales, true) AND deleted_at IS NULL LIMIT 1""", tid)
    if brg:
        r1 = await PL.add_item_to_price_list(req(tid), uuid.UUID(a), PriceListItemCreate(item_id=str(brg["id"]), price=1000, min_quantity=1))
        lid = r1["data"]["id"]
        cek(f"{tid} H4 respons tambah baris: unit_text == satuan dasar, berlaku_text == Ikut daftar",
            r1["data"]["unit_text"] == HD.unit_text(None, brg["dasar"]) and r1["data"]["berlaku_text"] == "Ikut daftar", f"{r1['data']}")
        async with pool.acquire() as c:
            await c.execute("UPDATE products SET for_sales = false WHERE id = $2 AND tenant_id = $1", tid, brg["id"])
        item2 = PriceListItemCreate(item_id=str(brg["id"]), price=800, min_quantity=100)
        pv = (await PL.preview_add_price_list_item(req(tid), uuid.UUID(a), item2.model_dump(mode="json")))["data"]
        gl = await galat(PL.add_item_to_price_list(req(tid), uuid.UUID(a), item2))
        cek(f"{tid} H4b barang tidak dijual: pratinjau blok + penulis ditolak DH_BARANG_TIDAK_DIJUAL",
            not pv["ok"] and pv["blocks"][0]["code"] == "DH_BARANG_TIDAK_DIJUAL" and gl and gl[0] == "DH_BARANG_TIDAK_DIJUAL", f"{gl}")
        async with pool.acquire() as c:
            await c.execute("UPDATE products SET for_sales = true WHERE id = $2 AND tenant_id = $1", tid, brg["id"])
        # ---- H8b detail baris
        det = (await PL.get_price_list(req(tid, metode="GET"), uuid.UUID(a)))["data"]
        cek(f"{tid} H8b detail baris memuat unit_text + berlaku_text", det["items"] and all(i.get("unit_text") and i.get("berlaku_text") for i in det["items"]))
        # ---- H7
        ph = (await PL.preview_hapus_baris(req(tid), uuid.UUID(a), uuid.UUID(lid), {}))["data"]
        sblm = await hitung(pool, tid)
        await PL.remove_item_from_price_list(req(tid, metode="DELETE"), uuid.UUID(a), uuid.UUID(lid))
        ssdh = await hitung(pool, tid)
        cek(f"{tid} H7 hapus baris: pratinjau menyebut harga; penulis menghapus tepat satu baris",
            ph["ok"] and ph["rencana"]["min_quantity"] == 1.0 and ssdh[1] == sblm[1] - 1)
    # ---- H5
    k = f"uji-dh-{st}"
    r1 = await PL.ubah_status_daftar(req(tid, k), uuid.UUID(c2), {"aktif": False})
    r2 = await PL.ubah_status_daftar(req(tid, k), uuid.UUID(c2), {"aktif": False})
    gl = await galat(PL.ubah_status_daftar(req(tid, k), uuid.UUID(c2), {"aktif": True}))
    cek(f"{tid} H5 idempotensi: kunci sama = replay (respons sama), kunci sama isi beda = ditolak", r1 == r2 and gl is not None, f"{gl}")
    # ---- H6
    s0 = await hitung(pool, tid)
    for fn, args in [(PL.preview_status_daftar, (uuid.UUID(c2), {"aktif": True})), (PL.preview_hapus_daftar, (uuid.UUID(c2), {})),
                     (PL.preview_bawaan_daftar, (uuid.UUID(c2), {})), (PL.preview_ubah_daftar, (uuid.UUID(c2), {"name": "UJI ganti"}))]:
        await fn(req(tid), *args)
    cek(f"{tid} H6 semua pratinjau READ ONLY: hitung price_lists/price_list_items/customers tak berubah", await hitung(pool, tid) == s0, f"{s0}")


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        db_pool = await c.fetchval("SELECT current_database()")
    if not db_pool.startswith("scratch_dhd2"):
        print(f"TOLAK: bukan salinan (pool={db_pool})")
        return 2
    for tid in TENANT:
        await periksa(pool, tid)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
