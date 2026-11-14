"""Daftar Harga hapus menyebut NAMA pelanggan (D2 lanjutan) -- uji di SALINAN (klon milkydb); BACA SAJA. GUARD: pool harus scratch_dhnm*.
H1 daftar dipakai pelanggan: blok DH_DIPAKAI_PELANGGAN; jumlah_pelanggan == SQL; nama_pelanggan (maks 5) subset nama sebenarnya (customers.nama); pesan memuat nama; baris_terhapus == SQL.
H2 daftar tanpa pelanggan: ok, rencana.baris_terhapus == SQL.
H3 pratinjau READ ONLY: jumlah price_lists/price_list_items/customers tak berubah."""
import asyncio
import os
import sys

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import price_lists as PL  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402

HASIL = []
TENANT = tuple(x for x in os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(",") if x)


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid):
    r = Request({"type": "http", "method": "POST", "path": "/api/price-lists", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-dh-nama"}
    return r


async def hitung(pool, tid):
    async with pool.acquire() as c:
        return (await c.fetchval("SELECT count(*) FROM price_lists WHERE tenant_id = $1", tid),
                await c.fetchval("SELECT count(*) FROM price_list_items pli JOIN price_lists p ON p.id = pli.price_list_id WHERE p.tenant_id = $1", tid),
                await c.fetchval("SELECT count(*) FROM customers WHERE tenant_id = $1", tid))


async def periksa(pool, tid):
    awal = await hitung(pool, tid)
    async with pool.acquire() as c:
        dipakai = await c.fetch("""SELECT pl.id, pl.name, count(cu.id) n FROM price_lists pl JOIN customers cu ON cu.price_list_id = pl.id AND cu.tenant_id = pl.tenant_id
                                   WHERE pl.tenant_id = $1 GROUP BY pl.id, pl.name""", tid)
        bebas = await c.fetch("""SELECT pl.id FROM price_lists pl WHERE pl.tenant_id = $1 AND NOT EXISTS
                                 (SELECT 1 FROM customers cu WHERE cu.tenant_id = pl.tenant_id AND cu.price_list_id = pl.id) LIMIT 3""", tid)
    bad = []
    for d in dipakai:
        x = (await PL.preview_hapus_daftar(req(tid), d["id"], None))
        x = x.get("data", x) if isinstance(x, dict) else x
        b = x["blocks"][0]
        async with pool.acquire() as c:
            nama = {r["nama"] for r in await c.fetch("SELECT nama FROM customers WHERE tenant_id = $1 AND price_list_id = $2", tid, d["id"])}
            baris = await c.fetchval("SELECT count(*) FROM price_list_items pli JOIN price_lists p ON p.id = pli.price_list_id WHERE p.tenant_id = $1 AND pli.price_list_id = $2", tid, d["id"])
        ok = (b["code"] == "DH_DIPAKAI_PELANGGAN" and b["jumlah_pelanggan"] == d["n"] and 0 < len(b["nama_pelanggan"]) <= 5
              and set(b["nama_pelanggan"]) <= nama and b["nama_pelanggan"][0] in b["message"] and b["baris_terhapus"] == baris
              and b["sisa_pelanggan"] == max(0, d["n"] - len(b["nama_pelanggan"])))
        if not ok:
            bad.append((d["name"], b))
    cek(f"{tid} H1 hapus daftar dipakai ({len(dipakai)} daftar): nama + jumlah + baris_terhapus sesuai SQL", not bad, f"salah={bad[:1]}")
    bad = []
    for d in bebas:
        x = await PL.preview_hapus_daftar(req(tid), d["id"], None)
        x = x.get("data", x) if isinstance(x, dict) else x
        async with pool.acquire() as c:
            baris = await c.fetchval("SELECT count(*) FROM price_list_items pli JOIN price_lists p ON p.id = pli.price_list_id WHERE p.tenant_id = $1 AND pli.price_list_id = $2", tid, d["id"])
        if not (x["ok"] and x["rencana"]["baris_terhapus"] == baris):
            bad.append(x)
    cek(f"{tid} H2 hapus daftar bebas ({len(bebas)} daftar): ok + baris_terhapus sesuai SQL", not bad)
    cek(f"{tid} H3 pratinjau read-only: hitungan tak berubah", await hitung(pool, tid) == awal, f"{awal}")


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
    if not db.startswith("scratch_dhnm"):
        print(f"TOLAK: bukan salinan (pool={db})")
        return 2
    for tid in TENANT:
        await periksa(pool, tid)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
