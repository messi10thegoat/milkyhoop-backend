"""UJI NYATA pratinjau PO (kontrak PO tahap 2, 22 Okt 2026): BACA-SAJA di produksi. Mode: baru [merah]
Koneksi default_transaction_read_only = on: pratinjau WAJIB lulus di sana (tanpa FOR UPDATE / advisory lock, transaksi readonly).
Tiap PO nyata (kaos + grapgrap): calculate (badan dari PO itu sendiri) == angka tersimpan; edit/send/receive/close/cancel/delete/to-bill
preview -> 200, bentuk kontrak, ok == (blocks kosong), blok sesuai status (penentu yang sama dengan tulis).
Mode `merah`: penentu terima/tagih dipaksa kunci=True -> pratinjau di read-only HARUS gagal (kontrol merah koreksi MASTER)."""
import asyncio, os, sys, uuid
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
from decimal import Decimal
import asyncpg
from starlette.requests import Request

from app.routers import purchase_orders as PO
from app.schemas import purchase_orders as PS
from app.services import po_rencana as PR

gagal = []
MERAH = len(sys.argv) > 1 and sys.argv[1] == "merah"


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:240]}"))
    if not ok: gagal.append(nama)


def req(t, uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": t, "user_id": uid}
    return r


HARAP = {  # status -> aksi yang BOLEH (blok kosong); selain itu wajib ber-blok
    "draft": {"send", "cancel", "delete", "edit"},
    "sent": {"cancel", "close", "edit", "receive"},
    "partial_received": {"close", "receive"},
    "received": {"close"},
    "partial_billed": {"close"},
    "billed": {"close"},
    "closed": set(), "cancelled": set(),
}


async def main():
    c = await asyncpg.connect(os.environ["URL"])
    await c.execute("SET default_transaction_read_only = on")
    class Pool:
        def acquire(s):
            class CM:
                async def __aenter__(x): return c
                async def __aexit__(x, *a): return False
            return CM()
    async def gp(): return Pool()
    PO.get_pool = gp
    if MERAH:
        asli_t, asli_g = PR.rencana_terima, PR.rencana_tagih
        async def t(conn, po, d, items, hari, kunci=True): return await asli_t(conn, po, d, items, hari, kunci=True)
        async def g(conn, po, items, kunci=True): return await asli_g(conn, po, items, kunci=True)
        PR.rencana_terima, PR.rencana_tagih = t, g

    pos = await c.fetch("""SELECT id, tenant_id, po_number, status FROM purchase_orders
                           WHERE tenant_id IN ('kaos-biru-konveksi', 'grapgrap-manado') ORDER BY created_at DESC LIMIT 30""")
    print(f"INFO PO nyata = {len(pos)}")
    cek("ada PO nyata untuk diuji", bool(pos))
    for p in pos:
        uid = str(await c.fetchval("SELECT created_by FROM purchase_orders WHERE id = $1", p["id"]) or uuid.uuid4())
        r, n = req(p["tenant_id"], uid), p["po_number"]
        # calculate dari badan PO tersimpan == angka tersimpan (PO pasca-V435; sebelum itu trigger lama menimpa)
        h = await c.fetchrow("SELECT * FROM purchase_orders WHERE id = $1", p["id"])
        brs = await c.fetch("SELECT * FROM purchase_order_items WHERE po_id = $1 ORDER BY line_number", p["id"])
        if brs:
            body = PS.CreatePurchaseOrderRequest(
                vendor_id=str(h["vendor_id"]) if h["vendor_id"] else None, vendor_name=h["vendor_name"] or "-", po_date=h["po_date"],
                items=[PS.PurchaseOrderItemCreate(description=b["description"], quantity=b["quantity"], unit_price=b["unit_price"],
                                                  discount_percent=float(b["discount_percent"] or 0), discount_amount=b["discount_amount"] or 0,
                                                  tax_rate=float(b["tax_rate"] or 0)) for b in brs],
                discount_percent=float(h["discount_percent"] or 0), discount_amount=h["discount_amount"] or 0, tax_rate=float(h["tax_rate"] or 0))
            d = (await PO.calculate_purchase_order(r, body))["data"]
            cek(f"{n}: calculate total == tersimpan", Decimal(str(d["total_amount"])) == Decimal(str(h["total_amount"])), (d["total_amount"], h["total_amount"]))
            cek(f"{n}: calculate vendor ok", d["ok"] is True, d["blocks"])
        aksi = {
            "edit": lambda: PO.preview_edit_purchase_order(r, p["id"], PS.UpdatePurchaseOrderRequest(notes="uji pratinjau")),
            "send": lambda: PO.preview_send_purchase_order(r, p["id"]),
            "receive": lambda: PO.preview_receive_goods(r, p["id"], None),
            "close": lambda: PO.preview_close_purchase_order(r, p["id"]),
            "cancel": lambda: PO.preview_cancel_purchase_order(r, p["id"], PS.CancelPreviewRequest(reason="uji")),
            "delete": lambda: PO.preview_delete_purchase_order(r, p["id"]),
            "to-bill": lambda: PO.preview_convert_to_bill(r, p["id"], None),
        }
        for k, f in aksi.items():
            try:
                d = (await f())["data"]
            except Exception as e:
                cek(f"{n} [{p['status']}] {k}/preview lulus di read-only", False, f"{type(e).__name__}: {e}")
                continue
            bentuk = {"po_id", "po_number", "status", "position_text", "ok", "blocks", "payload"} <= set(d)
            cek(f"{n} [{p['status']}] {k}/preview bentuk kontrak", bentuk and d["ok"] == (not d["blocks"])
                and all(set(b) == {"code", "message"} for b in d["blocks"]), sorted(d))
            if k in ("edit", "send", "close", "cancel", "delete", "receive"):
                boleh = k in HARAP.get(p["status"], set())
                cek(f"{n} [{p['status']}] {k}: ok={d['ok']} sesuai aturan status", d["ok"] == boleh or (k == "receive" and not d["ok"]),
                    d["blocks"])
            if k in ("close", "cancel"):
                cek(f"{n} {k}: sisa_ditagih_sesudah 0", d["sisa_ditagih_sesudah"] == 0)
    await c.close()
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
