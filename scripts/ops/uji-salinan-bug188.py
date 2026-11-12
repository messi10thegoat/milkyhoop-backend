"""BUG-188 uji DB di SALINAN (bukan prod): handler ASLI GET /api/bom/{id} dan /cost-breakdown atas klon (kaos + busana). Mutasi HANYA di klon.
1 BOM AKTIF: GET detail + rincian biaya -> baris bill_of_materials IDENTIK sebelum/sesudah (biaya + updated_at) · 2 BOM AKTIF sengaja 'basi' (total_cost diubah di klon): GET TIDAK menyembuhkan (tetap basi, updated_at sama)
3 BOM DRAF: GET tak mengubah baris · 4 jalur tulis tetap menyegarkan: recalculate DRAF basi -> total benar + updated_at maju · 5 lintas tenant: GET BOM tenant lain 404."""
import asyncio
import sys

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import bom as B  # noqa: E402

KA, BS = "kaos-biru-konveksi", "pt-busana-indah-semesta"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


async def req(c, tid):
    r = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY (role::text = \'OWNER\') DESC LIMIT 1', tid)
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan"}
    return r


async def baris(c, tid, bid):
    return dict(await c.fetchrow("SELECT standard_cost, labor_cost, overhead_cost, total_cost, status, updated_at FROM bill_of_materials WHERE id = $1 AND tenant_id = $2", bid, tid))


async def galat(coro):
    try:
        return await coro
    except HTTPException as e:
        return e


async def main():
    pool = await B.get_pool()
    async with pool.acquire() as c:
        for tid in (KA, BS):
            aktif = await c.fetchval("SELECT id FROM bill_of_materials WHERE tenant_id = $1 AND status = 'active' ORDER BY created_at LIMIT 1", tid)
            draf = await c.fetchval("SELECT id FROM bill_of_materials WHERE tenant_id = $1 AND status = 'draft' ORDER BY created_at LIMIT 1", tid)
            cek(f"prasyarat {tid}: ada BOM aktif", aktif is not None)
            if aktif is None:
                continue
            rq = await req(c, tid)
            s0 = await baris(c, tid, aktif)
            await B.get_bom(rq, aktif)
            await B.get_cost_breakdown(rq, aktif)
            cek(f"1 {tid} GET detail + rincian biaya BOM AKTIF: baris identik (biaya + updated_at)", await baris(c, tid, aktif) == s0)
            await c.execute("UPDATE bill_of_materials SET total_cost = total_cost + 777 WHERE id = $1 AND tenant_id = $2", aktif, tid)
            basi = await baris(c, tid, aktif)
            await B.get_bom(rq, aktif)
            await B.get_cost_breakdown(rq, aktif)
            cek(f"2 {tid} BOM AKTIF basi: GET TIDAK menyembuhkan (total tetap basi, updated_at sama)", await baris(c, tid, aktif) == basi and basi["total_cost"] != s0["total_cost"])
            if draf:
                d0 = await baris(c, tid, draf)
                await B.get_bom(rq, draf)
                await B.get_cost_breakdown(rq, draf)
                cek(f"3 {tid} GET BOM DRAF: baris tak berubah", await baris(c, tid, draf) == d0)
                await c.execute("UPDATE bill_of_materials SET total_cost = total_cost + 555 WHERE id = $1 AND tenant_id = $2", draf, tid)
                basi_d = await baris(c, tid, draf)
                await B.recalculate_cost(rq, draf)
                after = await baris(c, tid, draf)
                cek(f"4 {tid} recalculate DRAF basi menyegarkan: total kembali benar", after["total_cost"] == d0["total_cost"] and after["total_cost"] != basi_d["total_cost"], f"{d0['total_cost']} {basi_d['total_cost']} {after['total_cost']}")
            lain = BS if tid == KA else KA
            g = await galat(B.get_bom(await req(c, lain), aktif))
            cek(f"5 {tid} GET BOM milik {tid} oleh tenant {lain}: 404", isinstance(g, HTTPException) and g.status_code == 404, str(g)[:100])
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
