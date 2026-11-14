"""Teks Barang BUG-235/236 -- uji di SALINAN (klon milkydb); BACA SAJA (tanpa tulis). GUARD: pool harus scratch_bgt*.
H1 history: SEMUA movement tiap barang ber-ledger memuat jenis_text terisi, tanpa kode mentah (huruf besar beruntun/garis bawah), dan movement_type/source_type lama tetap ada.
H2 activity: query (JOIN "User" + user_profiles) berjalan; deskripsi tak berawalan "Item "; actor_name TAK berupa email bila pengguna punya nama tampil.
H3 kosakata: tiap (movement_type, source_type) yang ADA di ledger salinan terpetakan (bukan "Mutasi stok" generik) -- kosakata baru = kamus harus dilengkapi."""
import asyncio
import os
import re
import sys
import uuid

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import items as I  # noqa: E402
from app.services import barang_teks as BT  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402

HASIL = []
TENANT = tuple(x for x in os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(",") if x)


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid):
    r = Request({"type": "http", "method": "GET", "path": "/api/items", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-barang-teks"}
    return r


async def periksa(pool, tid):
    async with pool.acquire() as c:
        ids = [r["product_id"] for r in await c.fetch(
            "SELECT product_id FROM inventory_ledger WHERE tenant_id = $1 GROUP BY product_id ORDER BY count(*) DESC LIMIT 5", tid)]
        pasang = [(r["movement_type"], r["source_type"]) for r in await c.fetch(
            "SELECT DISTINCT movement_type, source_type FROM inventory_ledger WHERE tenant_id = $1", tid)]
        akt = [r["item_id"] for r in await c.fetch(
            "SELECT item_id FROM item_activities WHERE tenant_id = $1 GROUP BY item_id ORDER BY count(*) DESC LIMIT 5", tid)]
    # H1
    n = bad = 0
    for pid in ids:
        r = await I.get_item_history(req(tid), uuid.UUID(str(pid)), page=1, limit=200)
        for m in r["data"]:
            n += 1
            t = m.get("jenis_text")
            if not t or re.search(r"[A-Z]{3,}|_", t) or "movement_type" not in m or "source_type" not in m:
                bad += 1
    cek(f"{tid} H1 history: {n} movement semua ber-jenis_text tanpa kode mentah", n > 0 and bad == 0, f"n={n} bad={bad}")
    # H2
    total = item = email = 0
    for iid in akt:
        r = await I.get_item_activity(req(tid), uuid.UUID(str(iid)), status=None, limit=50, offset=0)
        for a in r.activities:
            total += 1
            if (a.description or "").startswith("Item "):
                item += 1
    async with pool.acquire() as c:
        # pengguna yang PUNYA nama tampil tak boleh muncul sebagai email di actor_name (dibaca lewat SQL yang sama)
        email = await c.fetchval("""SELECT count(*) FROM item_activities ia LEFT JOIN "User" u ON u.id::text = ia.actor_id::text
                                    LEFT JOIN user_profiles up ON up.user_id::text = ia.actor_id::text
                                    WHERE ia.tenant_id = $1 AND COALESCE(NULLIF(up.display_name, ''), NULLIF(u.name, '')) IS NOT NULL
                                      AND ia.actor_name ~ '@'""", tid)
    nama_ok = True
    for iid in akt:
        r = await I.get_item_activity(req(tid), uuid.UUID(str(iid)), status=None, limit=200, offset=0)
        async with pool.acquire() as c:
            punya = {str(x["id"]): x["actor_id"] for x in await c.fetch(
                "SELECT id, actor_id FROM item_activities WHERE item_id = $1::uuid AND tenant_id = $2", iid, tid)}
            nama = {str(x["id"]): x["nm"] for x in await c.fetch(
                """SELECT ia.id, COALESCE(NULLIF(up.display_name, ''), NULLIF(u.name, '')) AS nm FROM item_activities ia
                   LEFT JOIN "User" u ON u.id::text = ia.actor_id::text LEFT JOIN user_profiles up ON up.user_id::text = ia.actor_id::text
                   WHERE ia.item_id = $1::uuid AND ia.tenant_id = $2""", iid, tid)}
        for a in r.activities:
            if nama.get(a.id) and a.actor_name != nama[a.id]:
                nama_ok = False
    cek(f"{tid} H2 activity: {total} baris, deskripsi 'Item ...' = {item}, nama tampil dipakai bila ada (email tersimpan di {email} baris tak bocor)", total > 0 and item == 0 and nama_ok)
    # H3
    belum = [p for p in pasang if BT.jenis_text(*p) == BT.GENERIK]
    cek(f"{tid} H3 kosakata ledger: {len(pasang)} pasangan terpetakan", not belum, f"belum terpetakan: {belum}")


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
    if not db.startswith("scratch_bgt"):
        print(f"TOLAK: bukan salinan (pool={db})")
        return 2
    for tid in TENANT:
        await periksa(pool, tid)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
