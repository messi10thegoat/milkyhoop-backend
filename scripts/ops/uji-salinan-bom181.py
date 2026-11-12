"""BUG-181 ukur di SALINAN (bukan prod): GET /api/items/autocomplete (handler ASLI, dipakai form BOM) atas klon, tenant pt-busana-indah-semesta (+ kaos sbg kontrol lintas tenant).
Mengukur: apakah BE menemukan teks ber-spasi/ber-strip. Hanya baca."""
import asyncio
import sys

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import items as IT  # noqa: E402

BS, KA = "pt-busana-indah-semesta", "kaos-biru-konveksi"


async def req(c, tid):
    r = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY (role::text = \'OWNER\') DESC LIMIT 1', tid)
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan"}
    return r


async def main():
    pool = await IT.get_pool()
    async with pool.acquire() as c:
        daftar = await c.fetch("SELECT item_code, nama_produk, status, item_type, deleted_at IS NOT NULL AS hapus FROM products WHERE tenant_id = $1 ORDER BY created_at, id LIMIT 12", BS)
        for r in daftar:
            print(f"BARANG {BS}: {r['item_code']} | {r['nama_produk']} | status={r['status']} tipe={r['item_type']} hapus={r['hapus']}", flush=True)
        for q in ("Kaos Polos", "kaos polos", "Kaos  Polos", "BHN-KAIN", "bhn-kain", "BRG", "Kain", "Kain Cotton", "Polos Kaos", "Cotton 30s", "BRG-0001", "BHN"):
            for tipe in (None, "goods"):
                out = await IT.autocomplete_items(await req(c, BS), q, 10, tipe)
                nama = [(x.get("code"), x.get("name")) for x in (out.get("items") or out.get("data") or [])]
                print(f"CARI q={q!r} item_type={tipe}: {len(nama)} hasil {nama[:3]}", flush=True)
    print("SELESAI ukur", flush=True)


asyncio.run(main())
