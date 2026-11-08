"""BUG-138 / ROW (t) -- uji di SALINAN (klon milkydb): handler pratinjau ASLI NK/ND, keluaran stock[] ditulis JSON (satu berkas per pohon).
Bentuk: ND post/preview (ND draf), ND /preview dari faktur pembelian (retur, <=3 tagihan terbaru ber-barang stok),
NK /preview dari faktur penjualan (retur, <=3 faktur terbaru ber-barang stok). Pembanding (uji-salinan-bug138.sh) memeriksa: pohon BARU
== pohon LAMA + medan `unit` saja; unit == satuan dasar barang (COALESCE(NULLIF(base_unit,''), satuan))."""
import asyncio
import json
import os
import sys
from decimal import Decimal

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import credit_notes as RC, vendor_credits as RV  # noqa: E402

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
KELUAR = os.environ["KELUAR"]
STOK = "EXISTS (SELECT 1 FROM {t} x JOIN products p ON p.id = x.{kol} AND p.tenant_id = $1 AND p.track_inventory WHERE x.{fk} = d.id)"


def req(tid):
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan-bug138"}
    return r


def bersih(v):
    return json.loads(json.dumps(v, default=lambda o: str(o) if not isinstance(o, Decimal) else float(o)))


async def coba(f):
    try:
        r = await f
    except HTTPException as e:
        return {"galat": e.status_code}
    d = r.get("data", r) if isinstance(r, dict) else r
    return {"stock": bersih(d.get("stock")), "can_proceed": d.get("can_proceed", d.get("can_save"))}


async def utama():
    pool = await RC.get_pool() if hasattr(RC, "get_pool") else await RV.get_pool()
    out = {}
    async with pool.acquire() as conn:
        db = await conn.fetchval("SELECT current_database()")
        if not db.startswith("scratch_b138_"):
            sys.exit(f"BUKAN salinan: {db}")
        for tid in TENANT:
            nd = await conn.fetch("SELECT d.id FROM vendor_credits d WHERE d.tenant_id = $1 AND d.status = 'draft' ORDER BY d.id", tid)
            tg = await conn.fetch("SELECT d.id FROM bills d WHERE d.tenant_id = $1 AND COALESCE(d.status_v2, d.status) NOT IN ('draft', 'void') AND "
                                  + STOK.format(t="bill_items", fk="bill_id", kol="product_id") + " ORDER BY d.created_at DESC, d.id LIMIT 3", tid)
            fj = await conn.fetch("SELECT d.id FROM sales_invoices d WHERE d.tenant_id = $1 AND d.status NOT IN ('draft', 'void') AND "
                                  + STOK.format(t="sales_invoice_items", fk="invoice_id", kol="item_id") + " ORDER BY d.created_at DESC, d.id LIMIT 3", tid)
            dasar = {str(r["id"]): r["dasar"] for r in await conn.fetch(
                "SELECT id, COALESCE(NULLIF(base_unit, ''), satuan) AS dasar FROM products WHERE tenant_id = $1", tid)}
            out[tid] = {"dasar": dasar, "pratinjau": {}}
            for r in nd:
                out[tid]["pratinjau"][f"nd_post:{r['id']}"] = await coba(RV.preview_post_vendor_credit(req(tid), r["id"]))
            for r in tg:
                b = RV.VendorCreditPreviewRequest(original_bill_id=str(r["id"]), reason="return")
                out[tid]["pratinjau"][f"nd_buat:{r['id']}"] = await coba(RV.preview_create_vendor_credit(req(tid), b))
            for r in fj:
                b = RC.CreditNotePreviewRequest(original_invoice_id=str(r["id"]), reason="return")
                out[tid]["pratinjau"][f"nk_buat:{r['id']}"] = await coba(RC.preview_credit_note(req(tid), b))
    json.dump(out, open(KELUAR, "w"), sort_keys=True, indent=1)
    print(f"[info] ditulis {KELUAR}: " + ", ".join(f"{t}={len(v['pratinjau'])}" for t, v in out.items()), flush=True)

asyncio.run(utama())
