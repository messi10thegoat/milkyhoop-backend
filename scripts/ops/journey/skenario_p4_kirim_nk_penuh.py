"""P4 (26 Sep 2026, BACKEND; permintaan MASTER sesudah cn312): kirim baris yang SUDAH dikreditkan penuh = DITOLAK.
Faktur diposting (policy delivery, barang belum dikirim) -> NK discount SEBESAR faktur (porsi tertunda penuh ->
allocated 0) -> /fulfill = 409 FULFILL_LINE_FULLY_CREDITED menyebut nomor NK, NOL tulisan (tak ada SJ, stok, HPP)
-> void NK (allocated pulih) -> /fulfill = 200 dengan pendapatan = nilai kontrak."""
import uuid
from decimal import Decimal as D

from journey_lib import BARANG, GUDANG, NAMA, PELANGGAN, T
from skenario_so_penuh import so_body


async def _jejak(J, inv):
    async with J.pool.acquire() as c:
        return {
            "sj": await c.fetchval("select count(*) from invoice_fulfillments where tenant_id=$1 and invoice_id=$2",
                                   T, uuid.UUID(inv)),
            "stok": await c.fetchval("select count(*) from inventory_ledger where tenant_id=$1 and source_id=$2",
                                     T, uuid.UUID(inv)),
            "jurnal_kirim": await c.fetchval(
                "select count(*) from journal_entries where tenant_id=$1 and source_id=$2 "
                "and source_type in ('INVOICE_FULFILLMENT','INVOICE_REVENUE')", T, uuid.UUID(inv)),
        }


async def _body_kirim(J, inv, awal):
    _, si = await J.langkah(f"{awal}_si", "GET", f"/api/sales-invoices/{inv}")
    items = ((si or {}).get("data") or {}).get("items") or []
    return {"warehouse_id": GUDANG, "recognize_revenue": True, "idempotency_key": f"journey-ff-{uuid.uuid4()}",
            "items": [{"invoice_item_id": str(b["id"]), "quantity": float(b.get("quantity") or b.get("qty"))}
                      for b in items]}


async def jalankan(J):
    async with J.pool.acquire() as c:
        pol = await c.fetchval("select revenue_recognition_policy from tenant_config where tenant_id=$1", T)
    if pol != "delivery":
        return J.gagal("prasyarat", f"policy={pol}, butuh 'delivery'")
    _, so = await J.langkah("01_so", "POST", "/api/sales-orders", so_body(J),
                            headers={"X-Idempotency-Key": f"journey-{uuid.uuid4()}"})
    so_id = so["data"]["id"]
    await J.langkah("01b_confirm", "POST", f"/api/sales-orders/{so_id}/confirm")
    _, det = await J.langkah("01c_detail", "GET", f"/api/sales-orders/{so_id}")
    await J.langkah("02_to_invoice", "POST", f"/api/sales-orders/{so_id}/to-invoice",
                    {"items": [{"so_item_id": det["data"]["items"][0]["id"], "quantity": 10}]})
    inv = (await J.faktur_so(so_id))[0]
    await J.langkah("02b_post", "POST", f"/api/sales-invoices/{inv}/post", {})

    _, cn = await J.langkah("03_nk_penuh", "POST", "/api/credit-notes", {
        "customer_id": PELANGGAN, "customer_name": NAMA, "credit_note_date": J.hari.isoformat(),
        "original_invoice_id": inv, "reason": "discount", "reason_detail": "journey P4",
        "items": [{"item_id": BARANG, "description": "Batal sebelum kirim", "quantity": "10", "unit_price": "50000"}]})
    cn_id = ((cn or {}).get("data") or {}).get("id")
    if not cn_id:
        return J.gagal("03_nk_id", f"NK tak dibuat: {str(cn)[:200]}")
    _, cnp = await J.langkah("03b_nk_post", "POST", f"/api/credit-notes/{cn_id}/post")
    async with J.pool.acquire() as c:
        nomor = await c.fetchval("select credit_note_number from credit_notes where id=$1 and tenant_id=$2",
                                 uuid.UUID(cn_id), T)
        alok = await c.fetchval("select coalesce(sum(allocated_amount),0) from sales_invoice_items where invoice_id=$1",
                                uuid.UUID(inv))
    if D(str(alok)) != 0:
        return J.gagal("03c_prasyarat", f"allocated sesudah NK penuh = {alok} (harap 0) — premis P4 tak terpenuhi")

    sebelum = await _jejak(J, inv)
    _, r = await J.langkah("04_kirim_ditolak", "POST", f"/api/sales-invoices/{inv}/fulfill",
                           await _body_kirim(J, inv, "04a"), harap=(409,))
    d = (r or {}).get("detail")
    if not isinstance(d, dict) or d.get("code") != "FULFILL_LINE_FULLY_CREDITED" or d.get("credit_notes") != [nomor]:
        J.gagal("04_kode", f"harap FULFILL_LINE_FULLY_CREDITED [{nomor}], dapat {str(r)[:300]}")
    if await _jejak(J, inv) != sebelum:
        J.gagal("04_nol_tulisan", f"penolakan meninggalkan tulisan: {sebelum} -> {await _jejak(J, inv)}")

    await J.langkah("05_nk_void", "POST", f"/api/credit-notes/{cn_id}/void", {"reason": "journey P4 pulih"})
    await J.langkah("06_kirim_sesudah_void_nk", "POST", f"/api/sales-invoices/{inv}/fulfill",
                    await _body_kirim(J, inv, "06a"))
    async with J.pool.acquire() as c:
        rec = await c.fetchval("select coalesce(sum(recognized_amount),0) from sales_invoice_items where invoice_id=$1",
                               uuid.UUID(inv))
    if D(str(rec)) != D("500000.00"):
        J.gagal("06_pendapatan", f"recognized sesudah kirim = {rec}, harap 500000.00")
    await J.potret("07_akhir", so_id, [inv])
