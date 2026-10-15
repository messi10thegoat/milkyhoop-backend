"""Harness NYATA LAMPIRAN-SO-SJ-PABRIK (15 Okt 2026), BACA saja: kontrak LAMA == BARU.
Modul lama (sales_order_lampiran / delivery_lampiran dari ref sebelum pindah, /lama_so.py & /lama_sj.py) dimuat sebagai modul
paket yang sama (impor relatif jalan), lalu untuk SETIAP SO & SJ berlampiran di grapgrap + kaos: GET .../attachments lama == baru
(JSON utuh), dan 404 induk tak ada: status + detail sama. SJ: tenant tanpa flag pengiriman_serah -> 409 yang sama."""
import asyncio, importlib.util, json, os, sys, types, uuid
sys.path.insert(0, "/app"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from fastapi import HTTPException
from types import SimpleNamespace
from backend.api_gateway.app.routers import sales_order_lampiran as SL, delivery_lampiran as DL

gagal = 0
def cek(ok, label, info=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + label + (f" | {info}" if info else "")); gagal += 0 if ok else 1

def muat_lama(path, nama):
    spec = importlib.util.spec_from_file_location(f"backend.api_gateway.app.routers.{nama}", path)
    m = importlib.util.module_from_spec(spec); m.__package__ = "backend.api_gateway.app.routers"
    sys.modules[spec.name] = m; spec.loader.exec_module(m); return m

async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    tx = c.transaction(readonly=True); await tx.start()  # transaksi rute = savepoint di dalamnya
    class Pool:
        def acquire(self_):
            class A:
                async def __aenter__(s): return c
                async def __aexit__(s, *a): return False
            return A()
    async def gp(): return Pool()
    lama_so, lama_sj = muat_lama("/lama_so.py", "_lama_so_lampiran"), muat_lama("/lama_sj.py", "_lama_sj_lampiran")
    for m in (SL, DL, lama_so, lama_sj):
        m.get_pool = gp
    async def panggil(fn, *a):
        try:
            return ("ok", json.dumps(await fn(*a), sort_keys=True, default=str))
        except HTTPException as e:
            return ("galat", e.status_code, json.dumps(e.detail, sort_keys=True, default=str))
    try:
        for t in ("grapgrap-manado", "kaos-biru-konveksi"):
            uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId"=$1 ORDER BY "createdAt" LIMIT 1', t)
            req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": t, "user_id": str(uid)}))
            for ent, lama, baru, tabel in (("sales_order", lama_so.daftar_lampiran_so, SL.daftar_lampiran_so, "sales_orders"),
                                          ("delivery", lama_sj.daftar_lampiran_sj, DL.daftar_lampiran_sj, "invoice_fulfillments")):
                ids = [r["entity_id"] for r in await c.fetch(
                    "SELECT DISTINCT entity_id FROM document_attachments WHERE tenant_id=$1 AND entity_type=$2", t, ent)]
                ids += [r["id"] for r in await c.fetch(f"SELECT id FROM {tabel} WHERE tenant_id=$1 LIMIT 3", t)]  # tanpa lampiran
                beda = 0
                for i in ids:
                    a, b = await panggil(lama, req, i), await panggil(baru, req, i)
                    if a != b:
                        beda += 1; print("JSON beda", t, ent, i, a[:2], b[:2])
                cek(beda == 0, f"{t} {ent}: daftar lama == baru untuk {len(ids)} dokumen", f"beda {beda}")
                x = uuid.uuid4()
                a, b = await panggil(lama, req, x), await panggil(baru, req, x)
                cek(a == b and a[0] == "galat", f"{t} {ent}: induk tak ada -> galat lama == baru", str(b))
    finally:
        await tx.rollback(); await c.close()
    print("RINGKAS gagal=%d" % gagal)
asyncio.run(main())
