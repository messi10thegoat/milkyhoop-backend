"""BUG-286 -- UKUR (bukan perbaikan) di SALINAN: saldo batch_warehouse_stock bergeser SEKALI atau DUA KALI pada faktur pembelian (post_bill) dan faktur penjualan (post/fulfill) ber-batch.
Handler ASLI; tulis HANYA di salinan (nama DB dicek). Mencetak baris [ukur] -- angka mentah: diharapkan vs nyata."""
import asyncio
import json
import os
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal as D

from starlette.requests import Request
from starlette.responses import Response

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import bills as BR, items as IT, sales_invoices as SI  # noqa: E402
from app.schemas import sales_invoices as IS  # noqa: E402
from app.schemas.bills import CreateBillRequestV2 as CR  # noqa: E402
from app.schemas.items import CreateItemRequest  # noqa: E402
from app.services.bills_service import BillsService  # noqa: E402

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
UKUR = []


def lapor(nama, **kv):
    UKUR.append({"nama": nama, **{k: str(v) for k, v in kv.items()}})
    print("[ukur] " + nama + " :: " + " ".join(f"{k}={v}" for k, v in kv.items()), flush=True)


def req(tid, uid, kunci=None):
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": h, "query_string": b""})
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-ukur-286", "role": "OWNER"}
    return r


def pool_dari(conn):
    class CM:
        async def __aenter__(s): return conn
        async def __aexit__(s, *a): return False

    class Pool:
        def acquire(s): return CM()
    return Pool()


async def keadaan(conn, tid, batch_id):
    bws = await conn.fetch("SELECT warehouse_id, quantity FROM batch_warehouse_stock WHERE tenant_id = $2 AND batch_id = $1", batch_id, tid)
    ib = await conn.fetchrow("SELECT current_quantity, initial_quantity, status FROM item_batches WHERE id = $1 AND tenant_id = $2", batch_id, tid)
    lg = await conn.fetch("SELECT movement_type, quantity_in, quantity_out FROM inventory_ledger WHERE tenant_id = $1 AND batch_id = $2 ORDER BY created_at", tid, batch_id)
    return {"bws": [str(r["quantity"]) for r in bws], "item_batches.current": str(ib["current_quantity"]) if ib else None, "item_batches.status": ib["status"] if ib else None,
            "ledger": [f"{r['movement_type']}+{r['quantity_in']}-{r['quantity_out']}" for r in lg]}


async def satu(conn, tid, pool):
    uid = await conn.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', tid)
    vendor = await conn.fetchval("SELECT id FROM vendors WHERE tenant_id = $1 AND COALESCE(is_active, true) LIMIT 1", tid)
    cust = await conn.fetchval("SELECT nama FROM customers WHERE tenant_id = $1 AND deleted_at IS NULL AND btrim(COALESCE(nama, '')) <> '' ORDER BY created_at LIMIT 1", tid)
    if not (uid and vendor and cust):
        print(f"[info] {tid}: tanpa user/vendor -> dilewati", flush=True)
        return
    r = req(tid, uid)
    hari = date.today()
    BR.get_pool = lambda: asyncio.sleep(0, result=pool)
    # barang ber-batch UJI
    kode = uuid.uuid4().hex[:6]
    it = await IT.create_item(r, CreateItemRequest(name=f"UJI286 {kode}", base_unit="Pcs", item_type="goods", track_inventory=True, track_batches=True, purchase_price=1000, sales_price=2000))
    pid = uuid.UUID(str(it.data["id"]))
    for tag, qty_beli, qty_jual in (("A", 10, 3), ("B", 10, 6)):
        batch_no = f"UJI286-{kode}-{tag}"
        bn = f"UJI286-{kode}-{tag}"
        # --- Faktur PEMBELIAN ber-batch
        try:
            await BR.create_bill_v2(r, CR(vendor_id=vendor, invoice_number=bn, due_date=hari + timedelta(days=30), notes="UJI 286",
                                           items=[{"product_id": pid, "product_name": f"UJI286 {kode}", "qty": qty_beli, "price": 1000, "batch_no": batch_no, "exp_date": "2030-12"}]))
            bid = await conn.fetchval("SELECT id FROM bills WHERE tenant_id = $1 AND invoice_number = $2", tid, bn)
            await BillsService(pool).post_bill(tid, bid, uid)
            batch_id = await conn.fetchval("SELECT id FROM item_batches WHERE tenant_id = $1 AND item_id = $2 AND batch_number = $3", tid, pid, batch_no)
            k = await keadaan(conn, tid, batch_id) if batch_id else {"batch": "TIDAK TERBENTUK"}
            lapor(f"{tid} BELI-{tag} qty={qty_beli}: diharapkan bws=[{qty_beli}] (sekali)", **k)
        except Exception as e:  # noqa: BLE001
            lapor(f"{tid} BELI-{tag} GALAT", galat=f"{type(e).__name__}: {str(e)[:300]}")
            continue
        # --- Faktur PENJUALAN: kirim sebagian dari batch itu
        try:
            si = await SI.create_invoice(req(tid, uid), IS.CreateInvoiceRequest(customer_name=cust, invoice_date=hari, items=[
                IS.InvoiceItemCreate(description="uji286", item_id=str(pid), quantity=D(qty_jual), unit="Pcs", unit_price=D("2000"))]), Response())
            d = si["data"] if isinstance(si, dict) else si.data
            sid = uuid.UUID(str(d["id"] if isinstance(d, dict) else d.id))
            sebelum = await keadaan(conn, tid, batch_id)
            try:
                p = await SI.post_invoice(req(tid, uid), sid, IS.PostInvoiceRequest(), Response())
                lapor(f"{tid} JUAL-{tag} post_invoice OK", sukses=getattr(p, "success", p if isinstance(p, dict) else "?"))
            except Exception as e:  # noqa: BLE001
                lapor(f"{tid} JUAL-{tag} post_invoice GALAT", galat=f"{type(e).__name__}: {str(getattr(e, 'detail', e))[:300]}")
            sesudah = await keadaan(conn, tid, batch_id)
            lapor(f"{tid} JUAL-{tag} qty={qty_jual}: diharapkan bws=[{qty_beli - qty_jual}] (sekali)", sebelum=sebelum["bws"], sesudah=sesudah["bws"], ledger=sesudah["ledger"],
                  **{"item_batches.current": sesudah["item_batches.current"]})
            if sesudah["ledger"] == sebelum["ledger"]:   # posting tak mengirim barang -> /fulfill (pengiriman) lewat handler asli
                wid = await conn.fetchval("SELECT warehouse_id FROM batch_warehouse_stock WHERE tenant_id = $1 AND batch_id = $2 LIMIT 1", tid, batch_id)
                iid = await conn.fetchval("SELECT i.id FROM sales_invoice_items i JOIN sales_invoices s ON s.id = i.invoice_id AND s.tenant_id = $2 WHERE i.invoice_id = $1 LIMIT 1", sid, tid)
                isi = {"warehouse_id": str(wid), "items": [{"invoice_item_id": str(iid), "quantity": str(qty_jual)}]}

                class Req(Request):
                    async def json(s): return isi
                rq = Req({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""})
                rq.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-ukur-286", "role": "OWNER"}
                try:
                    await SI.fulfill_invoice(rq, sid)
                except Exception as e:  # noqa: BLE001
                    lapor(f"{tid} JUAL-{tag} fulfill GALAT", galat=f"{type(e).__name__}: {str(getattr(e, 'detail', e))[:300]}")
                akhir = await keadaan(conn, tid, batch_id)
                lapor(f"{tid} JUAL-{tag} FULFILL qty={qty_jual}: diharapkan bws=[{qty_beli - qty_jual}] (sekali; beli juga sekali)", sebelum=sebelum["bws"], sesudah=akhir["bws"], ledger=akhir["ledger"],
                      **{"item_batches.current": akhir["item_batches.current"], "item_batches.status": akhir["item_batches.status"]})
        except Exception as e:  # noqa: BLE001
            lapor(f"{tid} JUAL-{tag} GALAT", galat=f"{type(e).__name__}: {str(getattr(e, 'detail', e))[:300]}")


async def utama():
    pool = await IT.get_pool()
    async with pool.acquire() as conn:
        db = await conn.fetchval("SELECT current_database()")
        if not db.startswith("scratch_b286_"):
            sys.exit(f"BUKAN salinan: {db}")
        for tid in TENANT:
            await satu(conn, tid, pool_dari(conn))
    json.dump({"UKUR": UKUR}, open(os.environ["KELUAR"], "w"), indent=1)

asyncio.run(utama())
