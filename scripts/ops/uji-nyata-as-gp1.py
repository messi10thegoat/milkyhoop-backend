"""UJI NYATA AS-GP1 (24 Okt 2026): idem PATCH/DELETE/send/cancel/close PO lewat rute NYATA, ROLLBACK-ONLY di kaos (TES E2E). Mode: baru [merah_idem]
Tiap aksi 2x dengan kunci SAMA = respons SAMA + X-Idempotent-Replay + keadaan DB tertulis SEKALI (riwayat PO_UPDATED 1 baris, sent_at/
cancelled_at/closed_at tak bergeser, PO terhapus tetap 0 baris & ulang = 200 bukan 404); kunci sama isi beda = 409.
Kontrol merah (WAJIB FAIL): merah_idem = kunci dibuang -> ulang ditolak status (400/409) atau 404, bukan replay."""
import asyncio, os, sys
from decimal import Decimal as D
from uuid import UUID, uuid4
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

T = "kaos-biru-konveksi"
MERAH = sys.argv[2] if len(sys.argv) > 2 else ""
gagal = []


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:300]}"), flush=True)
    if not ok:
        gagal.append(nama)


async def main():
    from app.routers import purchase_orders as RPO
    from app.schemas.purchase_orders import CancelPurchaseOrderRequest, CreatePurchaseOrderRequest, UpdatePurchaseOrderRequest
    from app.services import idem_buat
    from app.utils.tanggal_tenant import tanggal_dokumen
    c = await asyncpg.connect(os.environ["URL"])
    if not await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T):
        sys.exit(f"TENANT TAK ADA: {T}")

    class Kon:
        def __getattr__(s, n): return getattr(c, n)
        def transaction(s, **kw): return c.transaction()
    k = Kon()

    class CM:
        async def __aenter__(s): return k
        async def __aexit__(s, *a): return False

    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    RPO.get_pool = gp
    import app.services.db_pool as DBP
    DBP.get_db_pool = gp
    if MERAH == "merah_idem":
        idem_buat.kunci_dari = lambda request, kunci_body=None: None
    uid = await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", T)

    def req(kunci=None):
        async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
        hdr = [(b"x-idempotency-key", kunci.encode())] if kunci else []
        r = Request({"type": "http", "method": "POST", "path": "/", "headers": hdr, "query_string": b""}, receive)
        r.state.user = {"tenant_id": T, "user_id": str(uid), "role": "OWNER"}
        return r

    async def galat(coro):
        try:
            return None, await coro
        except HTTPException as e:
            return e, None

    async def dua(nama, fn, *args, isi_beda=None):
        """Aksi 2x kunci sama -> (r1, r2, header2); lalu kunci sama isi beda (bila diberi) -> galat."""
        kunci = f"TES-E2E-{uuid4()}"
        h2 = Response()
        e1, r1 = await galat(fn(req(kunci), *args, response=Response()))
        e2, r2 = await galat(fn(req(kunci), *args, response=h2))
        beda = None
        if isi_beda is not None:
            beda, _ = await galat(fn(req(kunci), *isi_beda, response=Response()))
        return e1, r1, e2, r2, h2, beda

    luar = c.transaction()
    await luar.start()
    try:
        hari = await tanggal_dokumen(c, T)
        prod = await c.fetchrow("SELECT id, kode_produk FROM products WHERE tenant_id = $1 ORDER BY kode_produk LIMIT 1", T)
        vendor = await c.fetchrow("SELECT id, name FROM vendors WHERE tenant_id = $1 ORDER BY name LIMIT 1", T)

        async def po_baru():
            r = await RPO.create_purchase_order(req(), CreatePurchaseOrderRequest(
                vendor_id=str(vendor["id"]), vendor_name=vendor["name"], po_date=hari, ref_no="TES E2E AS-GP1", notes="TES E2E (rollback)", items=[
                    {"item_id": str(prod["id"]), "item_code": prod["kode_produk"], "description": "TES E2E barang", "quantity": D("3"), "unit_price": D("1000")}]))
            r = r.model_dump() if hasattr(r, "model_dump") else r
            return UUID(str(r["data"]["id"]))

        # PATCH
        p1 = await po_baru()
        e1, r1, e2, r2, h2, beda = await dua("PATCH", RPO.update_purchase_order, p1, UpdatePurchaseOrderRequest(notes="TES E2E ubah"),
                                             isi_beda=(p1, UpdatePurchaseOrderRequest(notes="TES E2E lain")))
        n_upd = await c.fetchval("""SELECT count(*) FROM audit_logs WHERE tenant_id = $1 AND entity_id = $2 AND "eventType" = 'PO_UPDATED'""", T, p1)
        cek("PATCH: kunci sama 2x = replay, riwayat PO_UPDATED SEKALI; isi beda = 409",
            r1 is not None and r2 == r1 and h2.headers.get("X-Idempotent-Replay") == "true" and n_upd == 1
            and beda is not None and beda.status_code == 409, (e1 and e1.detail, e2 and e2.detail, n_upd, beda and beda.detail))
        # SEND
        e1, r1, e2, r2, h2, _ = await dua("SEND", RPO.send_purchase_order, p1)
        sa = await c.fetchval("SELECT sent_at FROM purchase_orders WHERE id = $1", p1)
        cek("SEND: kunci sama 2x = replay (bukan 400 status), status sent sekali",
            r1 is not None and r2 == r1 and h2.headers.get("X-Idempotent-Replay") == "true" and sa is not None,
            (e1 and e1.detail, e2 and e2.detail))
        # CANCEL
        e1, r1, e2, r2, h2, beda = await dua("CANCEL", RPO.cancel_purchase_order, p1, CancelPurchaseOrderRequest(reason="TES E2E batal"),
                                             isi_beda=(p1, CancelPurchaseOrderRequest(reason="TES E2E alasan lain")))
        st = await c.fetchval("SELECT status FROM purchase_orders WHERE id = $1", p1)
        cek("CANCEL: kunci sama 2x = replay, status cancelled; alasan beda = 409",
            r1 is not None and r2 == r1 and h2.headers.get("X-Idempotent-Replay") == "true" and st == "cancelled"
            and beda is not None and beda.status_code == 409, (e1 and e1.detail, e2 and e2.detail, st, beda and beda.detail))
        # CLOSE
        p2 = await po_baru()
        await RPO.send_purchase_order(req(), p2)
        e1, r1, e2, r2, h2, _ = await dua("CLOSE", RPO.close_purchase_order, p2)
        cl = await c.fetchval("SELECT status FROM purchase_orders WHERE id = $1", p2)
        cek("CLOSE: kunci sama 2x = replay, status closed", r1 is not None and r2 == r1 and h2.headers.get("X-Idempotent-Replay") == "true"
            and cl == "closed", (e1 and e1.detail, e2 and e2.detail, cl))
        # DELETE
        p3 = await po_baru()
        e1, r1, e2, r2, h2, _ = await dua("DELETE", RPO.delete_purchase_order, p3)
        ada = await c.fetchval("SELECT count(*) FROM purchase_orders WHERE id = $1", p3)
        cek("DELETE: kunci sama 2x = replay 200 (bukan 404), PO 0 baris", r1 is not None and r2 == r1 and h2.headers.get("X-Idempotent-Replay") == "true"
            and ada == 0, (e1 and e1.detail, e2 and e2.detail, ada))
        # tanpa kunci = perilaku lama (status menolak ulang)
        p4 = await po_baru()
        await RPO.send_purchase_order(req(), p4)
        e, _ = await galat(RPO.send_purchase_order(req(), p4))
        cek("tanpa kunci: kirim ulang ditolak status seperti dulu", e is not None and e.status_code in (400, 409), e and e.detail)
    finally:
        await luar.rollback()
        sisa = await c.fetchval("SELECT count(*) FROM purchase_orders WHERE tenant_id = $1 AND ref_no = 'TES E2E AS-GP1'", T)
        cek("rollback: nol PO TES E2E", sisa == 0, sisa)
        await c.close()
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
