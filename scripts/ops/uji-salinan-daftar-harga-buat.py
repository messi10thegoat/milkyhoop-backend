"""Daftar Harga BUAT dengan X-Idempotency-Key (BUG-250) -- uji di SALINAN (klon milkydb); tulis HANYA di salinan. GUARD: pool harus scratch_dhby*.
Request MENIRU FE: kunci idempotensi ada (harness lama memanggil tanpa kunci sehingga 500 uuid lolos).
H1 buat dengan kunci: 201/ok, id UUID, baris idempotency_keys tercatat dengan result_id == id daftar.
H2 replay kunci sama + isi sama: id SAMA, jumlah daftar berkode itu tetap 1.
H3 kunci sama + isi beda: 409 IDEMPOTENCY_KEY_REUSED (bukan 500).
H4 tanpa kunci tetap berfungsi."""
import asyncio
import os
import sys
import time
import uuid

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import price_lists as PL  # noqa: E402
from app.schemas.price_lists import CreatePriceListRequest  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402

HASIL = []
TENANT = tuple(x for x in os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(",") if x)


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, kunci=None):
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": "POST", "path": "/api/price-lists", "headers": h})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-dh-buat"}
    return r


async def galat(coro):
    try:
        return await coro
    except HTTPException as e:
        d = e.detail
        return (d.get("code") if isinstance(d, dict) else None, e.status_code)


async def periksa(pool, tid):
    st = int(time.time())
    kode, kunci = f"UJI-BY-{st}", f"uji-buat-{st}-{tid[:4]}"
    r1 = await PL.create_price_list(req(tid, kunci), CreatePriceListRequest(code=kode, name="UJI buat kunci"))
    pid = r1["data"]["id"]
    async with pool.acquire() as c:
        n_kunci = await c.fetchval("SELECT count(*) FROM idempotency_keys WHERE tenant_id = $1 AND key LIKE $2 AND result_id = $3::uuid", tid, f"DH_BUAT:%:{kunci}", pid)
    cek(f"{tid} H1 buat dengan kunci: id UUID + idempotency_keys.result_id == id", bool(uuid.UUID(pid)) and n_kunci == 1, f"n_kunci={n_kunci}")
    r2 = await PL.create_price_list(req(tid, kunci), CreatePriceListRequest(code=kode, name="UJI buat kunci"))
    async with pool.acquire() as c:
        n_daftar = await c.fetchval("SELECT count(*) FROM price_lists WHERE tenant_id = $1 AND code = $2", tid, kode)
    cek(f"{tid} H2 replay kunci sama: id sama, daftar tetap 1", r2["data"]["id"] == pid and n_daftar == 1, f"n_daftar={n_daftar}")
    g = await galat(PL.create_price_list(req(tid, kunci), CreatePriceListRequest(code=kode + "X", name="UJI isi beda")))
    cek(f"{tid} H3 kunci sama + isi beda: 409 IDEMPOTENCY_KEY_REUSED", g == ("IDEMPOTENCY_KEY_REUSED", 409), f"{g}")
    r4 = await PL.create_price_list(req(tid), CreatePriceListRequest(code=kode + "N", name="UJI tanpa kunci"))
    cek(f"{tid} H4 tanpa kunci tetap berfungsi", bool(uuid.UUID(r4["data"]["id"])))


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
    if not db.startswith("scratch_dhby"):
        print(f"TOLAK: bukan salinan (pool={db})")
        return 2
    for tid in TENANT:
        await periksa(pool, tid)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
