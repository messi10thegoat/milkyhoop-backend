"""UJI NYATA AS-G2e (25 Okt 2026) -- PATCH SO DRAF LAMA vs BARU + pratinjau draf == PATCH, ROLLBACK-ONLY di kaos. argv: <keluaran.jsonl> [baru]
Skrip yang SAMA dijalankan atas pohon LAMA (kode terpasang) dan pohon BARU; runner membandingkan keluarannya (syarat MASTER: SEMUA SO draf kaos
x jenis badan -> respons + keadaan DB IDENTIK, riwayat SALES_ORDER_UPDATED sekali). Tiap kasus di transaksi SENDIRI lalu ROLLBACK.
Jenis badan: header, diskon, ongkir, dp, baris (hapus terakhir + tambah baru), nomor+pelanggan sama (no-op), judul+catatan.
Mode `baru`: + pratinjau draf (READ ONLY) sebelum PATCH -> after/lines/blocks dicatat untuk dibandingkan dgn hasil PATCH."""
import asyncio, json, os, sys, typing
from decimal import Decimal as D
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from fastapi import HTTPException
from starlette.requests import Request

T = "kaos-biru-konveksi"
KELUAR = sys.argv[1]
BARU = len(sys.argv) > 2 and sys.argv[2] == "baru"
ABAI_SO = {"updated_at", "search_text"}
KOLOM_ITEM = ("item_id", "description", "quantity", "unit", "unit_price", "discount_percent", "tax_id", "tax_rate", "tax_amount", "line_total",
              "warehouse_id", "sort_order", "dpp", "jumlah_baris")


def js(v):
    return json.loads(json.dumps(v, default=str))


async def main():
    from app.routers import sales_orders as SO
    from app.schemas.sales_orders import UpdateSalesOrderRequest
    c = await asyncpg.connect(os.environ["URL"])
    if not await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T):
        sys.exit(f"TENANT TAK ADA: {T}")
    uid = await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", T)

    class Kon:
        def __getattr__(s, n): return getattr(c, n)
        def transaction(s, **kw): return c.transaction()  # readonly pratinjau -> savepoint biasa dalam transaksi kasus (dijaga: pratinjau dulu, lalu rollback)

    class CMA:
        async def __aenter__(s): return Kon()
        async def __aexit__(s, *a): return False

    class PA:
        def acquire(s): return CMA()
    async def gpa(): return PA()
    SO.get_pool = gpa
    import app.services.db_pool as DBP
    DBP.get_db_pool = gpa

    def req():
        async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
        r = Request({"type": "http", "method": "PATCH", "path": "/", "headers": [], "query_string": b""}, receive)
        r.state.user = {"tenant_id": T, "user_id": str(uid), "role": "OWNER", "username": "TES E2E"}
        return r

    ann = UpdateSalesOrderRequest.model_fields["items"].annotation
    item_cls = next(a for a in typing.get_args(typing.get_args(ann)[0]) if hasattr(a, "model_fields")) if typing.get_args(ann) else None
    medan_item = set(item_cls.model_fields) if item_cls else set()

    async def snapshot(so_id):
        so = dict(await c.fetchrow("SELECT * FROM sales_orders WHERE id = $1", so_id))
        items = [dict(r) for r in await c.fetch(f"SELECT {', '.join(KOLOM_ITEM)} FROM sales_order_items WHERE sales_order_id = $1 "
                                                "ORDER BY sort_order, description, quantity", so_id)]
        riw = [dict(r) for r in await c.fetch("""SELECT "eventType", details FROM audit_logs WHERE tenant_id = $1 AND entity_id = $2
                                                 AND "eventType" = 'SALES_ORDER_UPDATED' ORDER BY 1, 2""", T, so_id)] \
            if await c.fetchval("SELECT 1 FROM information_schema.columns WHERE table_name = 'audit_logs' AND column_name = 'details'") else \
            [dict(r) for r in await c.fetch("""SELECT "eventType" FROM audit_logs WHERE tenant_id = $1 AND entity_id = $2
                                               AND "eventType" = 'SALES_ORDER_UPDATED'""", T, so_id)]
        return js({"so": {k: v for k, v in so.items() if k not in ABAI_SO}, "items": items, "riwayat": riw})

    drafts = await c.fetch("SELECT id, order_number, customer_id FROM sales_orders WHERE tenant_id = $1 AND status = 'draft' ORDER BY order_number", T)
    hasil = []
    for d in drafts:
        rows = [dict(r) for r in await c.fetch("SELECT * FROM sales_order_items WHERE sales_order_id = $1 ORDER BY sort_order, id", d["id"])]
        baris_lama = [{k: (str(v) if k in ("item_id", "tax_id", "warehouse_id") and v is not None else (float(v) if isinstance(v, D) else v))
                       for k, v in r.items() if k in medan_item and k != "id"} for r in rows]
        kasus = {
            "header": {"notes": "TES E2E G2e catatan", "reference": "TES-G2E"},
            "diskon": {"discount_amount": 1000},
            "ongkir": {"shipping_amount": 5000},
            "dp": {"dp_percent": 30},
            "baris": {"items": baris_lama[:-1] + [{"description": "TES E2E baris G2e", "quantity": 2, "unit_price": 1500}]},
            "noop": {"order_number": d["order_number"], **({"customer_id": str(d["customer_id"])} if d["customer_id"] else {})},
            "judul_catatan": {"order_title": "TES E2E judul G2e", "internal_notes": "TES E2E G2e"},
        }
        for nama, badan in kasus.items():
            catat = {"so": d["order_number"], "kasus": nama}
            tx = c.transaction()
            await tx.start()
            try:
                sebelum = await snapshot(d["id"])
                if BARU:
                    try:
                        pv = await SO.preview_edit_sales_order(req(), str(d["id"]), UpdateSalesOrderRequest(**badan))
                        catat["pratinjau"] = js(pv.data)
                    except HTTPException as e:
                        catat["pratinjau"] = {"galat": [e.status_code, js(e.detail)]}
                    catat["pratinjau_tanpa_tulis"] = (await snapshot(d["id"])) == sebelum
                try:
                    r = await SO.update_sales_order(req(), str(d["id"]), UpdateSalesOrderRequest(**badan), None)
                    catat["respons"] = js(r.model_dump() if hasattr(r, "model_dump") else r)
                except HTTPException as e:
                    catat["respons"] = {"galat": [e.status_code, js(e.detail)]}
                catat["sesudah"] = await snapshot(d["id"])
                catat["riwayat_baru"] = len(catat["sesudah"]["riwayat"]) - len(sebelum["riwayat"])
            finally:
                await tx.rollback()
            hasil.append(catat)
    with open(KELUAR, "w") as f:
        for h in hasil:
            f.write(json.dumps(h, sort_keys=True) + "\n")
    print(f"SELESAI kasus={len(hasil)} draf={len(drafts)}")
    await c.close()


asyncio.run(main())
