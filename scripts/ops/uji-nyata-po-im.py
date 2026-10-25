"""UJI NYATA PO-IM (25 Okt 2026) -- PATCH PO If-Match sesudah replay & kunci, ROLLBACK-ONLY di kaos (penanda TES E2E). argv: <keluaran.json> <lama|baru>
Skrip SAMA atas pohon LAMA (kode terpasang) dan BARU. Satu transaksi luar, SELALU ROLLBACK; tiap panggilan handler = savepoint (transaksi bersarang)."""
import asyncio, json, os, sys, uuid
from datetime import date
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from fastapi import HTTPException, Response
from starlette.requests import Request

T = "kaos-biru-konveksi"
KELUAR, MODE = sys.argv[1], sys.argv[2]
BASI = "2000-01-01T00:00:00+00:00"


def js(v):
    return json.loads(json.dumps(v, default=str))


async def main():
    from app.routers import purchase_orders as RP
    from app.schemas.purchase_orders import CreatePurchaseOrderRequest, UpdatePurchaseOrderRequest
    c = await asyncpg.connect(os.environ["URL"])
    if not await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T):
        sys.exit(f"TENANT TAK ADA: {T}")
    uid = await c.fetchval("SELECT created_by FROM purchase_orders WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", T) \
        or await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", T)

    class Kon:
        def __getattr__(s, n): return getattr(c, n)

    class CMA:
        async def __aenter__(s): return Kon()
        async def __aexit__(s, *a): return False

    class PA:
        def acquire(s): return CMA()
    async def gpa(): return PA()
    RP.get_pool = gpa
    import app.services.db_pool as DBP
    DBP.get_db_pool = gpa

    def req(kunci=None, if_match=None):
        h = []
        if kunci: h.append((b"x-idempotency-key", kunci.encode()))
        if if_match: h.append((b"if-match", if_match.encode()))
        async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
        r = Request({"type": "http", "method": "PATCH", "path": "/", "headers": h, "query_string": b""}, receive)
        r.state.user = {"tenant_id": T, "user_id": str(uid), "role": "OWNER", "username": "TES E2E"}
        return r

    async def panggil(f):
        try:
            r = await f()
            return {"ok": js(r.model_dump() if hasattr(r, "model_dump") else r)}
        except HTTPException as e:
            return {"galat": [e.status_code, js(e.detail)]}

    async def keadaan(po_id):
        return js({"notes": await c.fetchval("SELECT notes FROM purchase_orders WHERE id = $1", po_id),
                   "riwayat": await c.fetchval("""SELECT count(*) FROM audit_logs WHERE tenant_id = $1 AND entity_id = $2
                                                  AND "eventType" = 'PO_UPDATED'""", T, str(po_id))})

    hasil = {"mode": MODE}
    tx = c.transaction(); await tx.start()
    try:
        s = uuid.uuid4().hex[:8]
        buat = await panggil(lambda: RP.create_purchase_order(req(), CreatePurchaseOrderRequest(
            vendor_name=f"TES E2E Vendor PO-IM {s}", po_date=date.today(), notes="TES E2E PO-IM awal",
            items=[{"description": "TES E2E barang PO-IM", "quantity": 1, "unit_price": 1000}]), Response()))
        po_id = uuid.UUID(str(buat["ok"]["data"]["id"]))
        u0 = (await c.fetchval("SELECT updated_at FROM purchase_orders WHERE id = $1", po_id)).isoformat()
        hasil["buat"] = "ok" if "ok" in buat else buat
        U = lambda n: UpdatePurchaseOrderRequest(notes=n)
        hasil["B_basi"] = await panggil(lambda: RP.update_purchase_order(req(f"poim-b-{s}", BASI), po_id, U("TES E2E B"), Response()))
        hasil["B_keadaan"] = await keadaan(po_id)
        hasil["C_cocok"] = await panggil(lambda: RP.update_purchase_order(req(f"poim-c-{s}", u0), po_id, U("TES E2E C"), Response()))
        hasil["C_keadaan"] = await keadaan(po_id)
        hasil["D_ulang_basi"] = await panggil(lambda: RP.update_purchase_order(req(f"poim-c-{s}", BASI), po_id, U("TES E2E C"), Response()))
        hasil["D_sama_C"] = hasil["D_ulang_basi"] == hasil["C_cocok"]
        hasil["D_keadaan"] = await keadaan(po_id)
        hasil["E_tanpa"] = await panggil(lambda: RP.update_purchase_order(req(f"poim-e-{s}"), po_id, U("TES E2E E"), Response()))
        hasil["E_keadaan"] = await keadaan(po_id)
        hasil["F_tak_ada"] = await panggil(lambda: RP.update_purchase_order(req(f"poim-f-{s}", BASI), uuid.uuid4(), U("TES E2E F"), Response()))
    finally:
        await tx.rollback()
    hasil["sisa_sesudah_rollback"] = await c.fetchval("SELECT count(*) FROM purchase_orders WHERE tenant_id = $1 AND vendor_name LIKE 'TES E2E Vendor PO-IM%'", T)
    await c.close()
    open(KELUAR, "w").write(json.dumps(hasil, sort_keys=True, indent=1))
    print("SELESAI", json.dumps({k: (v if not isinstance(v, dict) or "galat" in v else "ok") for k, v in hasil.items() if not k.endswith("keadaan")}))


asyncio.run(main())
