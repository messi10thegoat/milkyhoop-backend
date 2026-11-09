"""identitas_text kontak -- uji di SALINAN (baca saja): detail SEMUA pelanggan + vendor kaos/nyawit dari handler pohon (LAMA atau BARU) di klon
yang SAMA -> JSON; banding-kontak-identitas.py: baru == lama + identitas_text, dan identitas_text == bagian dari medan detail itu sendiri."""
import asyncio
import json
import os
import sys

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import customers as cu, vendors as ve  # noqa: E402

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))


def req(tid):
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan-identitas"}
    return r


async def boleh(*a, **k):
    return True


async def utama():
    import app.services.dashboard_izin as DI
    DI.boleh_baca = boleh
    pool = await cu.get_pool()
    out = {}
    async with pool.acquire() as conn:
        db = await conn.fetchval("SELECT current_database()")
        if not db.startswith("scratch_identitas_"):
            sys.exit(f"BUKAN salinan: {db}")
        for tid in TENANT:
            o = out[tid] = {}
            for r in await conn.fetch("SELECT id FROM customers WHERE tenant_id = $1 AND deleted_at IS NULL ORDER BY id", tid):
                o[f"c:{r['id']}"] = (await cu.get_customer(req(tid), str(r["id"])))["data"]
            for r in await conn.fetch("SELECT id FROM vendors WHERE tenant_id = $1 ORDER BY id", tid):
                o[f"v:{r['id']}"] = (await ve.get_vendor(req(tid), r["id"]))["data"]
    json.dump(out, open(os.environ["KELUAR"], "w"), sort_keys=True, indent=1, default=str)
    print("[info] " + ", ".join(f"{t}: {len(out[t])} kontak" for t in TENANT), flush=True)

asyncio.run(utama())
