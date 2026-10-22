"""UJI NYATA G5 (22 Okt 2026): hapus/void bill hasil PO /to-bill memulihkan quantity_billed + status PO (kait BACKEND2 services/po_daftar).
Dijalankan TERHADAP DB SALINAN (pg_dump penuh milkydb), handler NYATA, tenant kaos. Produksi tak disentuh.
  K1 PO 2 qty -> kirim -> /to-bill (draf) -> qty_tagih 2 -> HAPUS bill draf -> qty_tagih 0, status PO kembali 'sent'
  K2 PO -> kirim -> /to-bill -> posting bill -> VOID bill -> qty_tagih 0, status PO kembali
  K3 KONTROL (hook dimatikan: po_daftar.pulihkan_tagih = no-op) -> hapus bill -> qty_tagih TETAP 2 (perilaku lama; alat bisa merah)
MERAH=1 -> harapan K1 dibalik."""
import asyncio, os, sys, uuid
from datetime import date
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

T = "kaos-biru-konveksi"
MERAH = os.environ.get("MERAH") == "1"
gagal = []


def cek(ok, l, i=""):
    if MERAH and l.startswith("K1"):
        ok = not ok
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


def req(uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid)}; return r


async def main():
    from app.routers import purchase_orders as PO
    from app.schemas import purchase_orders as SPO
    from app.services import bills_service as BS
    from app.services import po_daftar as PD
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    async def gp(): return pool
    PO.get_pool = gp
    svc = BS.BillsService(pool)
    c = await pool.acquire()
    try:
        uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId"=$1 ORDER BY "createdAt" LIMIT 1', T)
        v = await c.fetchrow("SELECT id, name FROM vendors WHERE tenant_id=$1 ORDER BY created_at LIMIT 1", T)
        r = req(uid)

        async def po_tertagih():
            body = SPO.CreatePurchaseOrderRequest(vendor_id=str(v["id"]), vendor_name=v["name"], po_date=date(2026, 10, 22), notes="TES E2E G5",
                                                  items=[SPO.PurchaseOrderItemCreate(description="Kain TES", quantity=Decimal("2"), unit_price=Decimal("50000"))])
            d = await PO.create_purchase_order(r, body)
            d = d if isinstance(d, dict) else d.model_dump()
            pid = uuid.UUID(d["data"]["id"])
            await PO.send_purchase_order(r, pid)
            st0 = await c.fetchval("SELECT status FROM purchase_orders WHERE id=$1", pid)
            t = await PO.convert_to_bill(r, pid, SPO.ConvertToBillRequest(bill_date=date(2026, 10, 22)))
            t = t if isinstance(t, dict) else t.model_dump()
            bid = uuid.UUID(t["data"]["bill_id"])
            return pid, bid, st0

        async def keadaan(pid):
            return (await c.fetchval("SELECT COALESCE(SUM(quantity_billed),0) FROM purchase_order_items WHERE po_id=$1", pid),
                    await c.fetchval("SELECT status FROM purchase_orders WHERE id=$1", pid))

        # K1 hapus draf
        pid, bid, st0 = await po_tertagih()
        q1, s1 = await keadaan(pid)
        h = await svc.delete_bill(T, bid, str(uid))
        q2, s2 = await keadaan(pid)
        cek(h.get("success") and q1 == 2 and q2 == 0 and s2 == st0, "K1 hapus bill draf -> qty tertagih PO pulih, status kembali",
            f"sebelum {q1}/{s1} sesudah {q2}/{s2} awal {st0} ({h.get('message')})")
        # K2 posting lalu void
        pid, bid, st0 = await po_tertagih()
        hp = await svc.post_bill(T, bid, uid)
        q1, s1 = await keadaan(pid)
        hv = await svc.void_bill(T, bid, {"reason": "TES G5 void"}, uid)
        q2, s2 = await keadaan(pid)
        cek(hp.get("success") and hv.get("success") and q1 == 2 and q2 == 0 and s2 == st0, "K2 posting lalu void bill -> qty tertagih PO pulih",
            f"post {hp.get('success')} void {hv.get('success')} {hv.get('message')} sebelum {q1}/{s1} sesudah {q2}/{s2}")
        # K3 kontrol: hook no-op -> perilaku lama
        asli = PD.pulihkan_tagih
        async def nol(*a, **k): return []
        PD.pulihkan_tagih = nol
        try:
            pid, bid, _ = await po_tertagih()
            await svc.delete_bill(T, bid, str(uid))
            q3, _ = await keadaan(pid)
        finally:
            PD.pulihkan_tagih = asli
        cek(q3 == 2, "K3 kontrol: tanpa hook qty tertagih TETAP 2 (alat bisa membedakan)", str(q3))
    finally:
        await pool.release(c); await pool.close()
    print("RINGKAS gagal=%d %s" % (len(gagal), gagal[:6])); sys.exit(1 if gagal else 0)

asyncio.run(main())
