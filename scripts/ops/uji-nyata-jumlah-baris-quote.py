"""Uji NYATA JUMLAH-BARIS-QUOTE (V427): Penawaran 26 pcs line_amount 6.150.000 (+diskon fixed +PPN 11%) -> total ==
konversi SO == konversi Faktur; edit mewarisi; duplikat membawa. Scratch, ROLLBACK, kaos saja."""
import asyncio, os, sys, uuid
from datetime import date, timedelta
from decimal import Decimal as D
import asyncpg
os.environ.setdefault("OPENAI_API_KEY", "boneka")
from fastapi import HTTPException, Response
from starlette.requests import Request
from backend.api_gateway.app.routers import quotes as QU
from backend.api_gateway.app.schemas.quotes import CreateQuoteRequest as C, UpdateQuoteRequest as U

TID = "kaos-biru-konveksi"
gagal = []


def cek(nama, ok, rinci=""):
    print(("  ok    " if ok else "  GAGAL ") + nama, "" if ok else rinci)
    if not ok:
        gagal.append(nama)


def req(uid):
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": TID, "user_id": uid}
    return r


def dump(x):
    return x if isinstance(x, dict) else x.model_dump(mode="json")


async def main():
    conn = await asyncpg.connect(os.environ["URL"])
    tr = conn.transaction()
    await tr.start()
    try:
        async def _gp():
            class CM:
                async def __aenter__(s): return conn
                async def __aexit__(s, *a): return False
            class P:
                def acquire(s): return CM()
            return P()
        QU.get_pool = _gp
        await conn.execute('UPDATE "Tenant" SET is_pkp = true WHERE id = $1', TID)  # scratch (ROLLBACK): uji PPN 11%
        uid = str(await conn.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", TID))
        cust = await conn.fetchrow("SELECT id, nama FROM customers WHERE tenant_id=$1 LIMIT 1", TID)

        async def buat(jumlah="6150000", harga="236538.46", qty=26, diskon=150000, ppn=11):
            r = dump(await QU.create_quote(req(uid), C(
                quote_date=date(2026, 10, 15), expiry_date=date(2026, 12, 31), customer_id=str(cust["id"]), customer_name=cust["nama"],
                discount_type="fixed", discount_value=diskon,
                items=[{"description": "UJI JUMLAH BARIS QUOTE", "quantity": qty, "unit_price": D(harga),
                        "line_amount": D(jumlah) if jumlah else None, "tax_rate": ppn}]), Response()))
            return (r.get("data") or r)["id"]

        # 1. Penawaran 26 pcs 6.150.000 - diskon 150.000 + PPN 11%
        q1 = await buat()
        t1 = await conn.fetchval("SELECT total_amount FROM quotes WHERE id=$1::uuid", q1)
        jb1 = await conn.fetchval("SELECT jumlah_baris FROM quote_items WHERE quote_id=$1::uuid", q1)
        cek("Penawaran jumlah_baris tersimpan 6.150.000", jb1 == D("6150000"), jb1)
        print("   total Penawaran:", t1)
        cek("Penawaran total == (6.150.000-150.000) x 1,11 = 6.660.000", t1 == D("6660000"), t1)
        # tanpa line_amount = lama (qty x harga)
        qlama = await buat(jumlah=None)
        tl = await conn.fetchval("SELECT total_amount FROM quotes WHERE id=$1::uuid", qlama)
        cek("tanpa line_amount = qty x harga lama (qty x harga 6.149.999,96)", tl == D("6659999.96"), tl)
        print("   total Penawaran tanpa jumlah:", tl)
        # di luar toleransi 422
        try:
            await buat(jumlah="6149988")
            cek("jumlah di luar toleransi 422", False, "lolos")
        except HTTPException as e:
            cek("jumlah di luar toleransi 422", e.status_code == 422, e.status_code)
        # 2. edit tanpa line_amount (kirim ulang form apa adanya, tanpa id) mewarisi
        sid = await conn.fetchval("SELECT id FROM quote_items WHERE quote_id=$1::uuid", q1)
        await QU.update_quote(req(uid), q1, U(items=[{"id": str(sid), "description": "UJI JUMLAH BARIS QUOTE", "quantity": 26,
                                                      "unit_price": D("236538.46"), "tax_rate": 11}]))
        t2 = await conn.fetchval("SELECT total_amount FROM quotes WHERE id=$1::uuid", q1)
        jb2 = await conn.fetchval("SELECT jumlah_baris FROM quote_items WHERE quote_id=$1::uuid", q1)
        cek("PATCH kirim-ulang (id) mewarisi jumlah_baris, total tetap", jb2 == D("6150000") and t2 == t1, (jb2, t2))
        await QU.update_quote(req(uid), q1, U(items=[{"description": "UJI JUMLAH BARIS QUOTE", "quantity": 26,
                                                      "unit_price": D("236538.46"), "tax_rate": 11}]))
        jb2b = await conn.fetchval("SELECT jumlah_baris FROM quote_items WHERE quote_id=$1::uuid", q1)
        cek("PATCH kirim-ulang TANPA id mewarisi", jb2b == D("6150000"), jb2b)
        # qty berubah tanpa line_amount -> warisan dilepas
        sid = await conn.fetchval("SELECT id FROM quote_items WHERE quote_id=$1::uuid", q1)
        await QU.update_quote(req(uid), q1, U(items=[{"id": str(sid), "description": "UJI JUMLAH BARIS QUOTE", "quantity": 20,
                                                      "unit_price": D("236538.46"), "tax_rate": 11}]))
        jb3 = await conn.fetchval("SELECT jumlah_baris FROM quote_items WHERE quote_id=$1::uuid", q1)
        cek("qty berubah tanpa line_amount -> jumlah dilepas", jb3 is None, jb3)
        # kembalikan (butuh line_amount lagi)
        sid = await conn.fetchval("SELECT id FROM quote_items WHERE quote_id=$1::uuid", q1)
        await QU.update_quote(req(uid), q1, U(items=[{"id": str(sid), "description": "UJI JUMLAH BARIS QUOTE", "quantity": 26,
                                                      "unit_price": D("236538.46"), "line_amount": D("6150000"), "tax_rate": 11}]))
        # 3. duplikat membawa
        dup = dump(await QU.duplicate_quote(req(uid), q1))
        qd = (dup.get("data") or dup)["id"]
        jbd = await conn.fetchval("SELECT jumlah_baris FROM quote_items WHERE quote_id=$1::uuid", qd)
        td = await conn.fetchval("SELECT total_amount FROM quotes WHERE id=$1::uuid", qd)
        cek("duplikat membawa jumlah_baris", jbd == D("6150000") and td == t1, (jbd, td))
        await conn.execute("UPDATE quotes SET status='sent', sent_at=now() WHERE id = ANY($1::uuid[])", [uuid.UUID(q1), uuid.UUID(qd)])
        # 4. konversi -> SO
        rso = dump(await QU.convert_to_sales_order(req(uid), q1))
        so = await conn.fetchrow("SELECT id, total_amount, subtotal, tax_amount FROM sales_orders WHERE tenant_id=$1 AND quote_id=$2::uuid", TID, q1)
        cek("konversi -> SO total == Penawaran", so is not None and so["total_amount"] == t1, (so and so["total_amount"], t1))
        jbs = await conn.fetchval("SELECT jumlah_baris FROM sales_order_items WHERE sales_order_id=$1", so["id"]) if so else None
        cek("SO membawa jumlah_baris", jbs == D("6150000"), jbs)
        # 5. konversi -> Faktur (dari duplikat qd yang identik)
        rinv = dump(await QU.convert_to_invoice(req(uid), qd))
        inv = await conn.fetchrow("SELECT id, total_amount, subtotal, tax_amount FROM sales_invoices WHERE tenant_id=$1 AND quote_id=$2::uuid", TID, qd)
        cek("konversi -> Faktur total == Penawaran", inv is not None and inv["total_amount"] == t1, (inv and inv["total_amount"], t1))
        jbi = await conn.fetchval("SELECT jumlah_baris FROM sales_invoice_items WHERE invoice_id=$1", inv["id"]) if inv else None
        cek("Faktur membawa jumlah_baris", jbi == D("6150000"), jbi)
        cek("SO == Faktur == Penawaran (total)", so and inv and so["total_amount"] == inv["total_amount"] == t1)
    finally:
        await tr.rollback()
        await conn.close()
    print("RINGKAS: GAGAL=%d %s" % (len(gagal), gagal))
    sys.exit(1 if gagal else 0)

asyncio.run(main())
