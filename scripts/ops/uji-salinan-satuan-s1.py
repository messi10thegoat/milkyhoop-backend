"""Satuan S-1 + bawaan_berlaku -- uji di SALINAN (klon milkydb); BACA SAJA. GUARD: pool harus scratch_bs1*.
H1 summary == jumlah baris tiap filter ?aksi= (SATU penentu) dan sama dengan total daftar.
H2 pemakai: jumlah_barang total == barang aktif ber-satuan (hitung SQL mandiri); tiap satuan bawaan tak bisa dinonaktifkan.
H3 bawaan_berlaku: tiap barang -> semua butir ada; sumber hanya barang|bawaan_usaha; barang berstok akun_pembelian == akun_persediaan bila kolom beli kosong/sama."""
import asyncio
import os
import sys
import uuid

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import items as I  # noqa: E402
from app.routers import units as U  # noqa: E402
from app.services import bawaan_berlaku as BB  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402

HASIL = []
TENANT = tuple(x for x in os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(",") if x)


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid):
    r = Request({"type": "http", "method": "GET", "path": "/api/units", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-satuan-s1"}
    return r


async def periksa(pool, tid):
    ringkas = (await U.units_summary(req(tid)))["data"]
    ok = True
    for f in ("semua", "terpakai", "tak_terpakai", "duplikat"):
        r = await U.list_units(req(tid), search=None, is_active=None, aksi=f, limit=500)
        ok = ok and r["total"] == ringkas[f]
    semua = await U.list_units(req(tid), search=None, is_active=None, aksi=None, limit=500)
    cek(f"{tid} H1 summary == filter ({ringkas})", ok and semua["total"] == ringkas["semua"]
        and ringkas["terpakai"] + ringkas["tak_terpakai"] == ringkas["semua"])
    async with pool.acquire() as c:
        n = await c.fetchval("""SELECT count(*) FROM products WHERE tenant_id = $1 AND deleted_at IS NULL
                                AND NULLIF(btrim(COALESCE(NULLIF(btrim(base_unit), ''), satuan)), '') IS NOT NULL""", tid)
    jml = sum(x["jumlah_barang"] for x in semua["items"])
    bawaan_nonaktif = [x["id"] for x in semua["items"] if x["is_system"] and x["aksi_tersedia"]["nonaktifkan"]["tersedia"]]
    cek(f"{tid} H2 pemakai: {jml} == {n} barang ber-satuan; bawaan tak bisa dinonaktifkan", jml == n and not bawaan_nonaktif,
        f"selisih={n - jml} salah={bawaan_nonaktif[:3]}")
    async with pool.acquire() as c:
        ids = [str(r["id"]) for r in await c.fetch("SELECT id FROM products WHERE tenant_id = $1 AND deleted_at IS NULL", tid)]
    bad, stok = [], 0
    for iid in ids:
        d = (await I.get_item(req(tid), uuid.UUID(iid))).data
        b = d["bawaan_berlaku"]
        if set(b) != set(BB.BUTIR) | {"teks"}:
            bad.append((iid, "butir"))
        for k in BB.BUTIR:
            if b[k] is not None and b[k]["sumber"] not in ("barang", "bawaan_usaha"):
                bad.append((iid, k))
            if b[k] is None and k not in b["teks"]:
                bad.append((iid, k + " tanpa teks"))
        if d.get("item_type", "goods") == "goods" and d.get("track_inventory") and b["akun_pembelian"] and b["akun_pembelian"]["sumber"] == "bawaan_usaha":
            stok += 1
            if b["akun_pembelian"]["id"] != (b["akun_persediaan"] or {}).get("id") and b["akun_persediaan"]:
                bad.append((iid, "pembelian != persediaan"))
    cek(f"{tid} H3 bawaan_berlaku: {len(ids)} barang utuh ({stok} berstok ikut persediaan)", not bad, f"salah={bad[:3]}")


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
    if not db.startswith("scratch_bs1"):
        print(f"TOLAK: bukan salinan (pool={db})")
        return 2
    for tid in TENANT:
        await periksa(pool, tid)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
