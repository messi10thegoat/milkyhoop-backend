"""UJI NYATA ALUR PO pratinjau -> tulis (kontrak PO tahap 2, 22 Okt 2026) -- DB SCRATCH (dump terbaru; dibuang sesudahnya).
Kode router ASLI; pemicu DB nyata. Di tiap langkah: pratinjau, lalu `payload`-nya dikirim APA ADANYA ke endpoint tulis (cara FE), lalu
hasil tulis == janji pratinjau:
  calculate -> create : total tersimpan (header + per baris) == calculate
  send/preview -> send : status == status_sesudah
  receive/preview (sebagian, lalu bawaan sisa) -> receive : amount_received & status == pratinjau
  to-bill/preview -> to-bill : bill grand_total/subtotal/tax + tiap bill_items == pratinjau; status PO == turunan sinkron_tagih
  close/preview -> close ; PO kedua: cancel/preview -> cancel ; PO ketiga: delete/preview -> delete
  + tulis yang DITOLAK pratinjau (send PO closed) memang ditolak dengan pesan blok pertama."""
import asyncio, os, sys, uuid
sys.path.insert(0, "/repo/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
from datetime import date
from decimal import Decimal
import asyncpg
from fastapi import HTTPException
from starlette.requests import Request

from app.routers import purchase_orders as PO
from app.schemas import purchase_orders as PS

gagal = []
T = "kaos-biru-konveksi"


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:260]}"))
    if not ok: gagal.append(nama)


def req(uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": uid}
    return r


async def main():
    c = await asyncpg.connect(os.environ["URL"])
    class Pool:
        def acquire(s):
            class CM:
                async def __aenter__(x): return c
                async def __aexit__(x, *a): return False
            return CM()
    async def gp(): return Pool()
    PO.get_pool = gp
    uid = str(await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", T))
    vendor = await c.fetchrow("SELECT id, name FROM vendors WHERE tenant_id = $1 ORDER BY created_at LIMIT 1", T)
    hari = await PO.tanggal_dokumen(c, T)
    r = req(uid)

    def badan():
        return PS.CreatePurchaseOrderRequest(
            vendor_id=str(vendor["id"]), vendor_name=vendor["name"], po_date=hari, ref_no="TES E2E pratinjau",
            items=[PS.PurchaseOrderItemCreate(description="TES E2E Kain", quantity="10", unit="m", unit_price="33.34", tax_rate=11, discount_percent=5),
                   PS.PurchaseOrderItemCreate(description="TES E2E Benang", quantity="3", unit_price="1000.50", discount_amount="10.25")],
            discount_percent=2.5, tax_rate=0)

    async def buat():
        calc = (await PO.calculate_purchase_order(r, badan()))["data"]
        po = await PO.create_purchase_order(r, PS.CreatePurchaseOrderRequest(**calc["payload"]))
        return calc, uuid.UUID(po["data"]["id"])

    # --- create == calculate
    calc, pid = await buat()
    h = await c.fetchrow("SELECT subtotal, discount_amount, tax_amount, total_amount, status FROM purchase_orders WHERE id = $1", pid)
    cek("create == calculate (header)", [float(h[k]) for k in ("subtotal", "discount_amount", "tax_amount", "total_amount")]
        == [calc[k] for k in ("subtotal", "discount_amount", "tax_amount", "total_amount")], (dict(h), calc))
    brs = await c.fetch("SELECT id, total FROM purchase_order_items WHERE po_id = $1 ORDER BY line_number", pid)
    cek("create == calculate (per baris)", [float(b["total"]) for b in brs] == [i["total"] for i in calc["items"]])

    async def langkah(nama, pratinjau, tulis_dari_payload):
        p = (await pratinjau())["data"]
        cek(f"{nama}/preview ok", p["ok"], p["blocks"])
        hasil = await tulis_dari_payload(p["payload"])
        return p, hasil

    async def status():
        return await c.fetchval("SELECT status FROM purchase_orders WHERE id = $1", pid)

    p, _ = await langkah("send", lambda: PO.preview_send_purchase_order(r, pid), lambda pl: PO.send_purchase_order(r, pid))
    cek("send: status == status_sesudah", await status() == p["status_sesudah"] == "sent")

    # terima sebagian (baris 1: 4 dari 10), lalu bawaan sisa
    b1 = str(brs[0]["id"])
    p, _ = await langkah("receive sebagian",
                         lambda: PO.preview_receive_goods(r, pid, PS.ReceivePreviewRequest(items=[PS.ReceiveItemRequest(po_item_id=b1, quantity_received="4")])),
                         lambda pl: PO.receive_goods(r, pid, PS.ReceiveGoodsRequest(**pl)))
    ar = await c.fetchval("SELECT amount_received FROM purchase_orders WHERE id = $1", pid)
    cek("receive sebagian: amount_received & status == pratinjau", float(ar) == p["amount_received_sesudah"] and await status() == p["status_sesudah"] == "partial_received",
        (ar, p["amount_received_sesudah"], await status()))
    p, _ = await langkah("receive sisa", lambda: PO.preview_receive_goods(r, pid, None), lambda pl: PO.receive_goods(r, pid, PS.ReceiveGoodsRequest(**pl)))
    ar = await c.fetchval("SELECT amount_received FROM purchase_orders WHERE id = $1", pid)
    cek("receive sisa: amount_received & status == pratinjau", float(ar) == p["amount_received_sesudah"] and await status() == p["status_sesudah"] == "received",
        (ar, p["amount_received_sesudah"], await status()))

    # tagih
    p, t = await langkah("to-bill", lambda: PO.preview_convert_to_bill(r, pid, None),
                         lambda pl: PO.convert_to_bill(r, pid, PS.ConvertToBillRequest(**pl)))
    bid = uuid.UUID(t["data"]["bill_id"])
    b = await c.fetchrow("SELECT grand_total, subtotal, tax_amount, status_v2 FROM bills WHERE id = $1", bid)
    cek("to-bill: bill total/subtotal/pajak == pratinjau", (float(b["grand_total"]), float(b["subtotal"]), float(b["tax_amount"]))
        == (p["bill_total"], p["bill_subtotal"], p["bill_tax"]) and b["status_v2"] == "draft", (dict(b), p["bill_total"], p["bill_subtotal"], p["bill_tax"]))
    bi = await c.fetch("SELECT quantity, subtotal, tax_amount, total FROM bill_items WHERE bill_id = $1 ORDER BY line_number", bid)
    cek("to-bill: tiap baris bill == pratinjau", [(float(x["quantity"]), float(x["subtotal"]), float(x["tax_amount"]), float(x["total"])) for x in bi]
        == [(i["quantity"], i["subtotal"], i["tax_amount"], i["total"]) for i in p["items"]], ([dict(x) for x in bi], p["items"]))
    cek("to-bill: status PO turunan (billed)", await status() == "billed", await status())
    p2 = (await PO.preview_convert_to_bill(r, pid, None))["data"]
    cek("to-bill kedua: pratinjau melaporkan SEMUA blok (status billed + tak ada sisa)",
        [x["code"] for x in p2["blocks"]] == ["PO_TAK_BISA_DITAGIH", "TAK_ADA_SISA_TAGIH"], p2["blocks"])

    p, _ = await langkah("close", lambda: PO.preview_close_purchase_order(r, pid), lambda pl: PO.close_purchase_order(r, pid))
    cek("close: status == status_sesudah", await status() == p["status_sesudah"] == "closed")
    ps = (await PO.preview_send_purchase_order(r, pid))["data"]
    try:
        await PO.send_purchase_order(r, pid)
        cek("send PO closed ditolak tulis", False, "lolos")
    except HTTPException as e:
        cek("send PO closed: tulis menolak dgn pesan blok pertama pratinjau", not ps["ok"] and e.detail == ps["blocks"][0]["message"], (e.detail, ps["blocks"]))

    # batal & hapus
    _, pid2 = await buat()
    await PO.send_purchase_order(r, pid2)
    p, _ = await langkah("cancel", lambda: PO.preview_cancel_purchase_order(r, pid2, PS.CancelPreviewRequest(reason="TES E2E")),
                         lambda pl: PO.cancel_purchase_order(r, pid2, PS.CancelPurchaseOrderRequest(**pl)))
    cek("cancel: status == status_sesudah", await c.fetchval("SELECT status FROM purchase_orders WHERE id = $1", pid2) == p["status_sesudah"] == "cancelled")
    _, pid3 = await buat()
    await langkah("delete", lambda: PO.preview_delete_purchase_order(r, pid3), lambda pl: PO.delete_purchase_order(r, pid3))
    cek("delete: PO hilang", await c.fetchval("SELECT count(*) FROM purchase_orders WHERE id = $1", pid3) == 0)
    await c.close()
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
