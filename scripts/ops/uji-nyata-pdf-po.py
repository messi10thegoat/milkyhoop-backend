"""UJI NYATA PDF Pesanan Pembelian (22 Okt 2026, tiket PDF PO): BACA-SAJA. Mode: baru [out.json]
 A. SKEMA NYATA (berlaku walau PO nyata = 0 baris): ketiga kueri muat_pdf_po dijalankan pada DB sungguhan; baris PO sintetis
    menggantikan hasil kosong supaya kueri vendor + baris ikut berjalan (kolom hantu = galat di sini, bukan di layar pengguna).
    Render HTML + tulis PDF (WeasyPrint) dari konteks itu; stempel DRAF / DIBATALKAN sesuai status.
 B. NYATA (semua PO yang ada, maks 30): dokumen._render(kind=purchase_order) == nomor; baris HTML == baris DB; tenant lain -> 404.
 C. 404: id acak -> 404 lewat _render."""
import asyncio, os, sys, uuid
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
from datetime import date, datetime, timezone
from decimal import Decimal
import asyncpg
from fastapi import HTTPException
from app.routers import dokumen as DK
from app.services import po_cetak as PC
from app.services.pdf_service import get_pdf_service

gagal = []


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:220]}"))
    if not ok: gagal.append(nama)


class Paksa:
    """Proksi conn: kueri DIJALANKAN di DB nyata; hasil PO kosong diganti baris sintetis (status pilihan)."""
    def __init__(self, c, status, **ekstra):
        self.c, self.status, self.ekstra, self.jalan = c, status, ekstra, []

    async def fetchrow(self, sql, *a):
        r = await self.c.fetchrow(sql, *a); self.jalan.append(sql.split("FROM", 1)[1].split()[0])
        if r is None and "FROM purchase_orders" in sql:
            return {"id": uuid.uuid4(), "po_number": "PO-UJI-0001", "status": self.status, "po_date": date(2026, 10, 22),
                    "expected_date": None, "ref_no": None, "ship_to_address": None, "warehouse_name": None, "notes": None,
                    "cancelled_at": None, "cancelled_reason": None, "vendor_id": uuid.uuid4(), "vendor_name": "Vendor Uji",
                    "subtotal": Decimal("1000.50"), "discount_percent": Decimal("0"), "discount_amount": Decimal("0"),
                    "tax_rate": Decimal("0"), "tax_amount": Decimal("0"), "total_amount": Decimal("1000.50"), **self.ekstra}
        return r

    async def fetch(self, sql, *a):
        self.jalan.append(sql.split("FROM", 1)[1].split()[0]); return await self.c.fetch(sql, *a)


async def main():
    c = await asyncpg.connect(os.environ["URL"]); await c.execute("SET default_transaction_read_only = on")
    ps = get_pdf_service()
    tid = await c.fetchval('SELECT id FROM "Tenant" ORDER BY id LIMIT 1')
    # A
    for status, ekstra, harus, tak in (("draft", {}, "Belum dikirim ke vendor", "DIBATALKAN"),
                                       ("cancelled", {"cancelled_at": datetime(2026, 10, 22, 2, tzinfo=timezone.utc), "cancelled_reason": "Uji"}, "DIBATALKAN", "Belum dikirim"),
                                       ("sent", {}, "PO-UJI-0001", "DIBATALKAN")):
        p = Paksa(c, status, **ekstra)
        m = await PC.muat_pdf_po(p, {"tenant_id": tid}, uuid.uuid4())
        cek(f"A skema nyata: 4 kueri berjalan ({status})", p.jalan == ["purchase_orders", "vendors", "purchase_order_items", '"Tenant"'], p.jalan)
        r = ps.render_purchase_order(m["po_data"], m["tenant_info"])
        cek(f"A render {status}: ada '{harus}', tanpa '{tak}'", harus in r.html and tak not in r.html)
        cek(f"A sen tampil ({status})", m["po_data"]["has_cents"] and "1.000,50" in r.html)
        if status == "sent":
            pdf = await asyncio.get_running_loop().run_in_executor(None, ps.tulis_pdf, r)
            cek("A PDF WeasyPrint %PDF", pdf[:4] == b"%PDF" and len(pdf) > 1000, len(pdf))
    # B
    pos = await c.fetch("SELECT id, tenant_id, po_number FROM purchase_orders ORDER BY created_at DESC LIMIT 30")
    print(f"INFO PO nyata = {len(pos)}")
    for po in pos:
        ctx = {"tenant_id": po["tenant_id"], "user_id": None}
        r, nomor = await DK._render(c, ctx, None, "purchase_order", str(po["id"]))
        n = await c.fetchval("SELECT count(*) FROM purchase_order_items WHERE po_id = $1", po["id"])
        cek(f"B {po['po_number']}: nomor == DB", nomor == po["po_number"], nomor)
        cek(f"B {po['po_number']}: baris HTML == DB ({n})", r.html.count('<td class="r">Rp ') == 2 * n)
        try:
            await DK._render(c, {"tenant_id": po["tenant_id"] + "-lain", "user_id": None}, None, "purchase_order", str(po["id"]))
            cek(f"B {po['po_number']}: tenant lain 404", False, "tak 404")
        except HTTPException as e:
            cek(f"B {po['po_number']}: tenant lain 404", e.status_code == 404, e.status_code)
    # C
    try:
        await DK._render(c, {"tenant_id": tid, "user_id": None}, None, "purchase_order", str(uuid.uuid4()))
        cek("C id acak 404", False, "tak 404")
    except HTTPException as e:
        cek("C id acak 404", e.status_code == 404, e.status_code)
    cek("C jenis cetak vs bagikan", "purchase_order" in DK.JENIS_CETAK and "purchase_order" not in DK.JENIS)
    await c.close()
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
