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
HASIL = []


def cek(nama, ok, **kv):
    HASIL.append(bool(ok))
    print(("[ok] " if ok else "[GAGAL] ") + nama + " :: " + " ".join(f"{k}={v}" for k, v in kv.items()), flush=True)


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


import hashlib


async def keadaan(conn, tid, batch_id):
    bws = await conn.fetch("SELECT quantity FROM batch_warehouse_stock WHERE tenant_id = $2 AND batch_id = $1", batch_id, tid)
    ib = await conn.fetchrow("SELECT current_quantity, status FROM item_batches WHERE id = $1 AND tenant_id = $2", batch_id, tid)
    return {"bws": [D(str(r["quantity"])) for r in bws], "current": D(str(ib["current_quantity"])) if ib else None, "status": ib["status"] if ib else None}


async def stempel(conn, tid, pid, doc_ids):
    """Sidik ledger barang + jurnal dokumen (tanpa id/waktu/nomor): HARUS sama sebelum/sesudah perbaikan (hanya saldo batch yang berubah)."""
    lg = [f"{r['movement_type']}|{r['quantity_in']}|{r['quantity_out']}|{r['unit_cost']}|{r['total_cost']}|{r['average_cost']}" for r in await conn.fetch(
        "SELECT movement_type, quantity_in, quantity_out, unit_cost, total_cost, average_cost FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2 ORDER BY created_at, id", tid, pid)]
    jl = []
    for d in doc_ids:
        jl += sorted(f"{r['account_code']}|{r['debit']}|{r['credit']}" for r in await conn.fetch(
            """SELECT c.account_code, l.debit, l.credit FROM journal_lines l JOIN journal_entries e ON e.id = l.journal_id AND e.tenant_id = $1
               JOIN chart_of_accounts c ON c.id = l.account_id AND c.tenant_id = $1 WHERE e.source_id = $2""", tid, d))
    return hashlib.sha1(json.dumps([lg, jl]).encode()).hexdigest()[:12], len(lg), len(jl)


async def satu(conn, tid, pool):
    uid = await conn.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', tid)
    vendor = await conn.fetchval("SELECT id FROM vendors WHERE tenant_id = $1 AND COALESCE(is_active, true) LIMIT 1", tid)
    cust = await conn.fetchval("SELECT nama FROM customers WHERE tenant_id = $1 AND deleted_at IS NULL AND btrim(COALESCE(nama, '')) <> '' ORDER BY created_at LIMIT 1", tid)
    if not (uid and vendor and cust):
        print(f"[info] {tid}: tanpa user/vendor/pelanggan -> dilewati", flush=True)
        return
    r = req(tid, uid)
    hari = date.today()
    BR.get_pool = lambda: asyncio.sleep(0, result=pool)

    async def barang(tag):
        kode = uuid.uuid4().hex[:6]
        it = await IT.create_item(r, CreateItemRequest(name=f"UJI286 {tag} {kode}", base_unit="Pcs", item_type="goods", track_inventory=True, track_batches=True, purchase_price=1000, sales_price=2000))
        return uuid.UUID(str(it.data["id"])), kode

    async def beli(pid, kode, tag, qty):
        no = f"UJI286-{kode}-{tag}"
        await BR.create_bill_v2(r, CR(vendor_id=vendor, invoice_number=no, due_date=hari + timedelta(days=30), notes="UJI 286",
                                       items=[{"product_id": pid, "product_name": f"UJI286 {tag}", "qty": qty, "price": 1000, "batch_no": no, "exp_date": "2030-12"}]))
        bid = await conn.fetchval("SELECT id FROM bills WHERE tenant_id = $1 AND invoice_number = $2", tid, no)
        await BillsService(pool).post_bill(tid, bid, uid)
        return bid, await conn.fetchval("SELECT id FROM item_batches WHERE tenant_id = $1 AND item_id = $2 AND batch_number = $3", tid, pid, no)

    async def jual(pid, batch_id, qty):
        si = await SI.create_invoice(req(tid, uid), IS.CreateInvoiceRequest(customer_name=cust, invoice_date=hari, items=[
            IS.InvoiceItemCreate(description="uji286", item_id=str(pid), quantity=D(qty), unit="Pcs", unit_price=D("2000"))]), Response())
        d = si["data"] if isinstance(si, dict) else si.data
        sid = uuid.UUID(str(d["id"] if isinstance(d, dict) else d.id))
        p = await SI.post_invoice(req(tid, uid), sid, IS.PostInvoiceRequest(), Response())
        if (p["data"] if isinstance(p, dict) else p.data)["fulfillment_status"] != "fulfilled":
            wid = await conn.fetchval("SELECT warehouse_id FROM batch_warehouse_stock WHERE tenant_id = $1 AND batch_id = $2 LIMIT 1", tid, batch_id)
            iid = await conn.fetchval("SELECT i.id FROM sales_invoice_items i JOIN sales_invoices s ON s.id = i.invoice_id AND s.tenant_id = $2 WHERE i.invoice_id = $1 LIMIT 1", sid, tid)
            isi = {"warehouse_id": str(wid), "items": [{"invoice_item_id": str(iid), "quantity": str(qty)}]}

            class Req(Request):
                async def json(s): return isi
            rq = Req({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""})
            rq.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-ukur-286", "role": "OWNER"}
            await SI.fulfill_invoice(rq, sid)
        return sid

    # S1: beli 10 -> BWS 10; void bill -> 0
    try:
        pid, kode = await barang("S1")
        bid, bat = await beli(pid, kode, "S1", 10)
        k = await keadaan(conn, tid, bat)
        cek(f"{tid} S1 beli 10 -> bws 10 (sekali) & current 10", k["bws"] == [D(10)] and k["current"] == D(10), nyata=k)
        s_beli = await stempel(conn, tid, pid, [bid])
        print(f"[stempel] {tid} S1-beli {s_beli}", flush=True)
        await BR.void_bill(r, bid, __import__("app.schemas.bills", fromlist=["x"]).VoidBillRequest(reason="UJI 286"), Response())
        k2 = await keadaan(conn, tid, bat)
        cek(f"{tid} S1 void bill -> bws 0 (satu penggerak)", k2["bws"] == [D(0)], nyata=k2)
        print(f"[stempel] {tid} S1-void {await stempel(conn, tid, pid, [bid])}", flush=True)
    except Exception as e:  # noqa: BLE001
        cek(f"{tid} S1 GALAT", False, galat=f"{type(e).__name__}: {str(getattr(e, 'detail', e))[:300]}")
    # S2: beli 10, jual 3 -> 7; void faktur jual -> 10
    try:
        pid, kode = await barang("S2")
        bid, bat = await beli(pid, kode, "S2", 10)
        sid = await jual(pid, bat, 3)
        k = await keadaan(conn, tid, bat)
        cek(f"{tid} S2 beli 10 jual 3 -> bws 7", k["bws"] == [D(7)] and k["current"] == D(7), nyata=k)
        print(f"[stempel] {tid} S2-jual {await stempel(conn, tid, pid, [bid, sid])}", flush=True)
        await SI.void_invoice(req(tid, uid), sid, IS.VoidInvoiceRequest(reason="UJI 286"), Response())
        k2 = await keadaan(conn, tid, bat)
        cek(f"{tid} S2 void faktur jual -> bws kembali 10", k2["bws"] == [D(10)], nyata=k2)
        print(f"[stempel] {tid} S2-void {await stempel(conn, tid, pid, [bid, sid])}", flush=True)
    except Exception as e:  # noqa: BLE001
        cek(f"{tid} S2 GALAT", False, galat=f"{type(e).__name__}: {str(getattr(e, 'detail', e))[:300]}")
    # S3: beli 10, jual 6 -> 4 (tak boleh terblok palsu); jual 5 lagi -> ditolak (tersedia 4)
    try:
        pid, kode = await barang("S3")
        bid, bat = await beli(pid, kode, "S3", 10)
        sid = await jual(pid, bat, 6)
        k = await keadaan(conn, tid, bat)
        cek(f"{tid} S3 beli 10 jual 6 -> bws 4 (tanpa blok palsu)", k["bws"] == [D(4)], nyata=k)
        print(f"[stempel] {tid} S3-jual {await stempel(conn, tid, pid, [bid, sid])}", flush=True)
        try:
            await jual(pid, bat, 5)
            cek(f"{tid} S3 jual 5 dari sisa 4 ditolak", False, nyata="lolos")
        except Exception as e:  # noqa: BLE001
            cek(f"{tid} S3 jual 5 dari sisa 4 ditolak (stok tidak cukup)", any(k in str(getattr(e, "detail", e)).lower() for k in ("tidak cukup", "hanya")), galat=str(getattr(e, "detail", e))[:200])
    except Exception as e:  # noqa: BLE001
        cek(f"{tid} S3 GALAT", False, galat=f"{type(e).__name__}: {str(getattr(e, 'detail', e))[:300]}")


async def utama():
    pool = await IT.get_pool()
    async with pool.acquire() as conn:
        db = await conn.fetchval("SELECT current_database()")
        if not db.startswith("scratch_b286_"):
            sys.exit(f"BUKAN salinan: {db}")
        for tid in TENANT:
            await satu(conn, tid, pool_dari(conn))
    print(f"[info] {len(HASIL)} cek, {HASIL.count(False)} gagal", flush=True)
    sys.exit(1 if (HASIL.count(False) and os.environ.get("TEGAS") == "1") or not HASIL else 0)

asyncio.run(utama())
