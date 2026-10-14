import asyncio, json, os, sys
sys.path.insert(0, "/app")
import asyncpg
from backend.api_gateway.app.services import so_aksi
from backend.api_gateway.app.utils.tanggal_tenant import tanggal_dokumen
async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"]); await c.execute("BEGIN READ ONLY")
    out = {}
    for t in ("kaos-biru-konveksi", "grapgrap-manado"):
        f = await so_aksi.fakta(c, t, await tanggal_dokumen(c, t))
        no = {r["id"]: r["order_number"] for r in await c.fetch("SELECT id, order_number FROM sales_orders WHERE tenant_id=$1", t)}
        out[t] = {no[i]: so_aksi.aksi_utama(v["aksi"]) for i, v in f.items()}
    await c.execute("ROLLBACK")
    json.dump(out, open("/out/lama.json", "w"))
asyncio.run(main())
