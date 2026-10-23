"""UJI NYATA teks Posisi anggota menunggu_pembayaran (23 Okt 2026, putusan MASTER): BACA-SAJA kaos + grapgrap. Mode: baru [merah]
Tiap baris daftar SO ?aksi=menunggu_pembayaran: position_text in {Menunggu DP, Menunggu pelunasan} atau "Menunggu termin N";
pengecualian SAH hanya teks fase lokasi (fase_lokasi SO terisi; fase didahulukan by design) -- dicetak.
Kontrol merah `merah`: aturan lama (PELUNASAN -> "Menunggu tagihan") WAJIB FAIL."""
import asyncio, inspect, os, sys
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from fastapi.params import Query as QP
from starlette.requests import Request
from app.routers import sales_orders as RSO
from app.services import so_posisi as SP

MERAH = len(sys.argv) > 2 and sys.argv[2] == "merah"
gagal = []


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:300]}"))
    if not ok:
        gagal.append(nama)


async def main():
    c = await asyncpg.connect(os.environ["URL"]); await c.execute("SET default_transaction_read_only = on")
    class Pool:
        def acquire(s):
            class CM:
                async def __aenter__(x): return c
                async def __aexit__(x, *a): return False
            return CM()
    async def gp(): return Pool()
    RSO.get_pool = gp
    import app.services.db_pool as DBP
    DBP.get_db_pool = gp
    if MERAH:
        asli = SP.posisi
        def lama(*a, **k):
            t, m = asli(*a, **k)
            return ("Menunggu tagihan" if t == "Menunggu pelunasan" and not a[3] else t), m
        SP.posisi = lama
    for t in ("kaos-biru-konveksi", "grapgrap-manado"):
        uid = str(await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", t))

        async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
        r = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}, receive)
        r.state.user = {"tenant_id": t, "user_id": uid, "role": "OWNER"}
        o = {}
        for n, p in inspect.signature(RSO.list_sales_orders).parameters.items():
            if n == "request": o[n] = r
            elif n == "aksi": o[n] = "menunggu_pembayaran"
            elif n == "limit": o[n] = 100
            elif isinstance(p.default, QP): o[n] = p.default.default
            elif p.default is not inspect._empty: o[n] = p.default
        x = await RSO.list_sales_orders(**o)
        x = x.model_dump(mode="json") if hasattr(x, "model_dump") else x
        rows = x.get("items") or x.get("data", {}).get("items") or x.get("data") or []
        fase = {r2["order_number"]: r2["fase_lokasi"] for r2 in await c.fetch(
            "SELECT order_number, fase_lokasi FROM sales_orders WHERE tenant_id = $1 AND order_number = ANY($2::text[])", t,
            [i["order_number"] for i in rows])}
        for i in rows:
            tx = i.get("position_text") or ""
            ok = tx in ("Menunggu DP", "Menunggu pelunasan") or tx.startswith("Menunggu termin")
            if not ok and fase.get(i["order_number"]):
                print(f"INFO {t} {i['order_number']}: teks fase lokasi '{tx}' (pengecualian sah)")
                continue
            cek(f"{t} {i['order_number']}: Posisi '{tx}' menyebut tagihan yang ditunggu", ok, tx)
        print(f"INFO {t} {len(rows)} baris: {[(i['order_number'], i.get('position_text')) for i in rows]}")
    await c.close()
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
