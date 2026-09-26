"""Skenario void faktur + Surat Jalan -> riwayat SO BERAKTOR (26 Sep 2026, BACKEND; permintaan MASTER:
bukti di salinan prod, BUKAN void di prod).
SO: buat -> confirm -> to-invoice penuh -> post -> kirim (SJ) -> void faktur beralasan
-> audit_logs: tepat 1 SALES_INVOICE_VOIDED + N FULFILLMENT_VOIDED (N = SJ yang di-void), userId = OWNER,
   tenant = kaos, satu transaksi dengan void (status faktur 'void' + SJ 'voided' + baris audit ada bersama)
-> GET /history (lengan B, middleware nyata): kedua kejadian tampil SEKALI dengan aktor, TANPA kembaran
   baris-kolom tanpa aktor (FAKTUR_DIBATALKAN / SURAT_JALAN_DIBATALKAN).
Void ulang = ditolak dan TIDAK menambah baris audit."""
import uuid

from journey_lib import OWNER, T
from skenario_so_penuh import kirim, so_body

DIKENAL = {}


async def _audit(J, inv, sj_ids):
    async with J.pool.acquire() as c:
        return await c.fetch(
            """select "eventType" ev, entity_type, entity_id::text eid, "userId" uid, tenant_id, metadata->>'reason' alasan
               from audit_logs where tenant_id=$1 and "eventType" in ('SALES_INVOICE_VOIDED','FULFILLMENT_VOIDED')
                 and entity_id::text = any($2::text[])""", T, [inv] + sj_ids)


async def jalankan(J):
    _, so = await J.langkah("01_buat_so", "POST", "/api/sales-orders", so_body(J),
                            headers={"X-Idempotency-Key": f"journey-{uuid.uuid4()}"})
    so_id = ((so or {}).get("data") or {}).get("id")
    if not so_id:
        return J.gagal("01_id", "SO gagal dibuat")
    await J.langkah("02_confirm", "POST", f"/api/sales-orders/{so_id}/confirm")
    _, det = await J.langkah("03_detail", "GET", f"/api/sales-orders/{so_id}")
    soi = det["data"]["items"][0]["id"]
    await J.langkah("04_to_invoice", "POST", f"/api/sales-orders/{so_id}/to-invoice",
                    {"items": [{"so_item_id": soi, "quantity": 10}]})
    inv = await J.faktur_so(so_id)
    if not inv:
        return J.gagal("04_faktur", "to-invoice tak menghasilkan faktur")
    inv = inv[0]
    await J.langkah("05_post", "POST", f"/api/sales-invoices/{inv}/post", {})
    await kirim(J, inv, "06", kenal=None)
    async with J.pool.acquire() as c:
        sj = [str(r["id"]) for r in await c.fetch(
            "select id from invoice_fulfillments where tenant_id=$1 and invoice_id=$2 and status='posted'",
            T, uuid.UUID(inv))]
    if not sj:
        return J.gagal("06_sj", "tak ada Surat Jalan posted untuk diuji")
    if await _audit(J, inv, sj):
        return J.gagal("06_audit_awal", "audit void sudah ada SEBELUM void (alat/salinan kotor)")

    await J.langkah("07_void", "POST", f"/api/sales-invoices/{inv}/void", {"reason": "journey salah harga"})
    rows = await _audit(J, inv, sj)
    si_rows = [r for r in rows if r["ev"] == "SALES_INVOICE_VOIDED"]
    sj_rows = [r for r in rows if r["ev"] == "FULFILLMENT_VOIDED"]
    if len(si_rows) != 1 or si_rows[0]["eid"] != inv or si_rows[0]["entity_type"] != "sales_invoices":
        J.gagal("08_audit_si", f"harap 1 SALES_INVOICE_VOIDED untuk faktur, dapat {[dict(r) for r in si_rows]}")
    if sorted(r["eid"] for r in sj_rows) != sorted(sj) or any(r["entity_type"] != "invoice_fulfillments" for r in sj_rows):
        J.gagal("08_audit_sj", f"harap FULFILLMENT_VOIDED per SJ {sj}, dapat {[dict(r) for r in sj_rows]}")
    if any(r["uid"] != OWNER or r["tenant_id"] != T or r["alasan"] != "journey salah harga" for r in rows):
        J.gagal("08_audit_aktor", f"aktor/tenant/alasan salah: {[dict(r) for r in rows]}")
    async with J.pool.acquire() as c:
        st = await c.fetchval("select status from sales_invoices where id=$1 and tenant_id=$2", uuid.UUID(inv), T)
        sjst = {r["status"] for r in await c.fetch(
            "select status from invoice_fulfillments where id = any($1::uuid[])", [uuid.UUID(x) for x in sj])}
    if st != "void" or sjst != {"voided"}:
        J.gagal("08_status", f"faktur {st}, SJ {sjst}")

    await J.langkah("09_void_ulang_ditolak", "POST", f"/api/sales-invoices/{inv}/void", {"reason": "x"},
                    harap=(400, 409))
    if len(await _audit(J, inv, sj)) != len(rows):
        J.gagal("09_audit_ulang", "void yang ditolak tetap menambah baris audit")

    if J.lengan == "B":
        _, r = await J.langkah("10_riwayat", "GET", f"/api/sales-orders/{so_id}/history")
        ev = ((r or {}).get("data") or {}).get("events") or []
        v_si = [e for e in ev if e["jenis"] == "SALES_INVOICE_VOIDED"]
        v_sj = [e for e in ev if e["jenis"] == "FULFILLMENT_VOIDED"]
        if len(v_si) != 1 or len(v_sj) != len(sj):
            J.gagal("10_isi", f"harap 1 + {len(sj)} kejadian void, dapat {[e['jenis'] for e in ev]}")
        if any((e.get("aktor") or {}).get("id") != OWNER for e in v_si + v_sj):
            J.gagal("10_aktor", f"kejadian void tanpa aktor OWNER: {v_si + v_sj}")
        kembar = [e["jenis"] for e in ev if e["jenis"] in ("FAKTUR_DIBATALKAN", "SURAT_JALAN_DIBATALKAN")]
        if kembar:
            J.gagal("10_kembar", f"baris-kolom tanpa aktor masih tampil: {kembar}")
        if not any("journey salah harga" in e["ringkas"] for e in v_si):
            J.gagal("10_alasan", "alasan void tak tampil di riwayat")
