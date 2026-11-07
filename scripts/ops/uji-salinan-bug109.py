"""BUG-109 uji DB di SALINAN (bukan prod), nyawit SO-2611-0014 (5 karton; SJ-2611-0013 3 sudah ditagih INV-2611-0014):
K3 tanpa SJ belum ditagih -> bawaan = sisa 2 · A SJ baru 1 -> bawaan 1 + catatan TAGIH_SESUAI_KIRIM · B draf -> position_text menyebut SJ
· posting -> fulfilled 1, stok tak keluar ganda. Dijalankan juga di pohon DASAR = kontrol merah (A/B wajib FAIL)."""
import asyncio
import sys
import uuid
from decimal import Decimal as D

import asyncpg
from fastapi import Response
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import items as RI  # noqa: E402
from app.routers import sales_invoices as RSI  # noqa: E402
from app.routers import sales_orders as RSO  # noqa: E402
from app.routers import sj_so as RSJ  # noqa: E402
from app.services import sj_so as SJ  # noqa: E402

TID = "pt-kita-nyawit-bersama"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(ok)
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(uid, kunci=None):
    r = Request({"type": "http", "method": "POST", "path": "/api/x", "headers": [(b"x-idempotency-key", kunci.encode())] if kunci else []})
    r.state.user = {"tenant_id": TID, "user_id": str(uid), "username": "uji-salinan"}
    return r


def baris_pratinjau(d):
    return [(x.get("name"), D(str(x["qty"]))) for x in d["lines"]]


async def main():
    c = await asyncpg.connect(**RI.settings.get_db_config())
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY (role::text = \'OWNER\') DESC LIMIT 1', TID)
    so = await c.fetchrow("SELECT id FROM sales_orders WHERE tenant_id = $1 AND order_number = 'SO-2611-0014'", TID)
    soi = await c.fetchrow("SELECT id, quantity, quantity_invoiced, unit, item_id FROM sales_order_items WHERE sales_order_id = $1", so["id"])
    print("SO-0014 baris", soi["quantity"], "ditagih", soi["quantity_invoiced"], soi["unit"], flush=True)

    pv = (await RSO.preview_to_invoice(req(uid), str(so["id"]), RSO.ConvertToInvoiceRequest(apply_deposits=False)))["data"]
    cek("K3 tanpa SJ belum ditagih: bawaan = sisa", baris_pratinjau(pv)[0][1] == D(str(soi["quantity"])) - D(str(soi["quantity_invoiced"])),
        f"{baris_pratinjau(pv)} catatan {[n['code'] for n in pv.get('notes', [])]}")

    stok0 = D(str(await c.fetchval("SELECT COALESCE(SUM(quantity_in - quantity_out), 0) FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2", TID, soi["item_id"])))
    sj = await RSJ.create_sj_so(req(uid, f"k109sj-{uuid.uuid4()}"), Response(), str(so["id"]),
                                SJ.SJSOIn(items=[SJ.BarisIn(sales_order_item_id=soi["id"], quantity=D("1"))]))
    nomor_sj = await c.fetchval("SELECT fulfillment_number FROM invoice_fulfillments WHERE tenant_id = $1 AND sales_order_id = $2 AND asal = 'so' ORDER BY created_at DESC LIMIT 1", TID, so["id"])
    stok1 = D(str(await c.fetchval("SELECT COALESCE(SUM(quantity_in - quantity_out), 0) FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2", TID, soi["item_id"])))
    print("SJ baru", nomor_sj, "stok", stok0, "->", stok1, flush=True)

    pv = (await RSO.preview_to_invoice(req(uid), str(so["id"]), RSO.ConvertToInvoiceRequest(apply_deposits=False)))["data"]
    kode = [n["code"] for n in pv.get("notes", [])]
    cek("A SJ 1 belum ditagih: bawaan = 1 + catatan TAGIH_SESUAI_KIRIM", baris_pratinjau(pv)[0][1] == D("1") and "TAGIH_SESUAI_KIRIM" in kode,
        f"{baris_pratinjau(pv)} catatan {kode}")

    h = await RSO.convert_to_invoice(req(uid, f"k109f-{uuid.uuid4()}"), Response(), str(so["id"]), RSO.ConvertToInvoiceRequest(apply_deposits=False))
    hd = h if isinstance(h, dict) else h.model_dump()
    inv_id = (hd.get("data") or hd)["invoice_id"]
    det = await RSI.get_invoice(req(uid), uuid.UUID(str(inv_id)))
    dt = det if isinstance(det, dict) else det.model_dump()
    dd = dt.get("data") or dt
    dd = dd if isinstance(dd, dict) else dd.model_dump()
    pt = dd.get("position_text") or ""
    q_draf = D(str(await c.fetchval("SELECT SUM(quantity) FROM sales_invoice_items WHERE invoice_id = $1", uuid.UUID(str(inv_id)))))
    cek("B draf: qty 1 + position_text menyebut SJ baru", q_draf == D("1") and (nomor_sj or "?") in pt and "ditautkan saat terbit" in pt, f"qty {q_draf} pos '{pt}'")

    await RSI.post_invoice(req(uid, f"k109p-{uuid.uuid4()}"), uuid.UUID(str(inv_id)))
    ful = D(str(await c.fetchval("SELECT SUM(COALESCE(fulfilled_qty, 0)) FROM sales_invoice_items WHERE invoice_id = $1", uuid.UUID(str(inv_id)))))
    stok2 = D(str(await c.fetchval("SELECT COALESCE(SUM(quantity_in - quantity_out), 0) FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2", TID, soi["item_id"])))
    cek("Posting: SJ ditautkan (fulfilled 1), stok tak keluar ganda", ful == D("1") and stok2 == stok1, f"fulfilled {ful} stok {stok1} -> {stok2}")
    await c.close()
    print(f"RINGKAS {sum(HASIL)}/{len(HASIL)} PASS", flush=True)


asyncio.run(main())
