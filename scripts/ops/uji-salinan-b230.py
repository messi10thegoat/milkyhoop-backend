"""BUG-230 uji DB di SALINAN (bukan prod): handler ASLI GET /warehouses/{id}/stock atas klon, tenant kaos + nyawit. Baca-saja.
A bawaan: total == hitungan SQL independen baris quantity <> 0, semua baris data quantity <> 0, len(data) == min(total, limit)
B ?hanya_berstok=false: total == hitungan SQL SEMUA baris warehouse_stock gudang itu (>= total bawaan)
C cari digabung: total == hitungan SQL (quantity <> 0 DAN kode/nama ILIKE) untuk kata dari barang ber-stok pertama"""
import asyncio
import sys

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import warehouses as WH  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, uid):
    r = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan", "role": "OWNER"}
    return r


async def stok(rq, wid, **kw):
    p = dict(skip=0, limit=200, search=None, hanya_berstok=True)
    p.update(kw)
    return await WH.get_warehouse_stock(rq, wid, **p)


async def main():
    pool = await WH.get_pool()
    async with pool.acquire() as c:
        for tid in (KA, NY):
            uid = await c.fetchval("/* tenant_id */ SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.tenant_id = $1 AND utr.status = 'ACTIVE' AND r.code = 'OWNER' ORDER BY utr.is_primary DESC LIMIT 1", tid)
            gud = await c.fetch("/* tenant_id */ SELECT id, code FROM warehouses WHERE tenant_id = $1 ORDER BY created_at", tid)
            cek(f"prasyarat {tid}: pemilik + >= 1 gudang", uid is not None and len(gud) >= 1)
            if uid is None or not gud:
                continue
            rq = req(tid, uid)
            ada_nol = False
            for g in gud:
                n_semua = await c.fetchval("/* tenant_id */ SELECT count(*) FROM warehouse_stock ws JOIN products i ON ws.item_id = i.id WHERE ws.tenant_id = $1 AND ws.warehouse_id = $2", tid, g["id"])
                n_stok = await c.fetchval("/* tenant_id */ SELECT count(*) FROM warehouse_stock ws JOIN products i ON ws.item_id = i.id WHERE ws.tenant_id = $1 AND ws.warehouse_id = $2 AND ws.quantity <> 0", tid, g["id"])
                ada_nol = ada_nol or n_semua > n_stok
                b = await stok(rq, g["id"])
                cek(f"A {tid} gudang {g['code']}: bawaan total {b.total} == SQL ber-stok {n_stok}, semua baris quantity <> 0, len(data) == min(total, 200)",
                    b.total == n_stok and all(r["quantity"] != 0 for r in b.data) and len(b.data) == min(n_stok, 200))
                s = await stok(rq, g["id"], hanya_berstok=False)
                cek(f"B {tid} gudang {g['code']}: hanya_berstok=false total {s.total} == SQL semua baris {n_semua} (>= bawaan)", s.total == n_semua and s.total >= b.total)
                if b.data:
                    kata = b.data[0]["item_code"][:3]
                    n_cari = await c.fetchval("/* tenant_id */ SELECT count(*) FROM warehouse_stock ws JOIN products i ON ws.item_id = i.id WHERE ws.tenant_id = $1 AND ws.warehouse_id = $2 AND ws.quantity <> 0 AND (i.item_code ILIKE $3 OR i.nama_produk ILIKE $3)", tid, g["id"], f"%{kata}%")
                    cr = await stok(rq, g["id"], search=kata)
                    cek(f"C {tid} gudang {g['code']}: cari '{kata}' digabung bawaan total {cr.total} == SQL {n_cari}", cr.total == n_cari and all(r["quantity"] != 0 for r in cr.data))
            print(f"CATATAN {tid}: ada gudang dengan baris stok 0 di klon: {ada_nol}", flush=True)
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
