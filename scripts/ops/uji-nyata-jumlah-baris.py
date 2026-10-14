"""Harness NYATA JUMLAH-BARIS (V427): handler asli di atas scratch DB (dump + V427), tx ROLLBACK, tenant kaos saja."""
import asyncio, os, sys
from datetime import date
from decimal import Decimal as D
import asyncpg
os.environ.setdefault("OPENAI_API_KEY", "boneka")
from fastapi import HTTPException, Response
from starlette.requests import Request
from backend.api_gateway.app.routers import sales_orders as SO, sales_invoices as SI, credit_notes as CN
from backend.api_gateway.app.schemas.credit_notes import CreateCreditNoteRequest
CreditNotePreviewRequest = CN.CreditNotePreviewRequest
from backend.api_gateway.app.schemas.sales_orders import CreateSalesOrderRequest as C, UpdateSalesOrderRequest as U, ConvertToInvoiceRequest as CI

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
        SO.get_pool = _gp
        uid = str(await conn.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", TID))
        cust = await conn.fetchrow("SELECT id, nama FROM customers WHERE tenant_id=$1 LIMIT 1", TID)
        col = await conn.fetchval("SELECT count(*) FROM information_schema.columns WHERE column_name='jumlah_baris' AND table_name IN ('sales_order_items','sales_invoice_items','quote_items')")
        cek("V427: 3 kolom jumlah_baris ada", col == 3, col)

        def body(qty=26, harga="236538.46", jumlah="6150000", ppn=0):
            return C(order_date=date(2026, 10, 14), customer_id=str(cust["id"]), customer_name=cust["nama"],
                     items=[{"description": "UJI JUMLAH BARIS", "quantity": qty, "unit_price": D(harga),
                             "line_amount": D(jumlah) if jumlah is not None else None, "tax_rate": ppn}])

        # 1. kasus pemilik: 26 pcs, Rp6.150.000
        r = await SO.create_sales_order(req(uid), body(), Response())
        oid = r.data["id"]
        row = await conn.fetchrow("SELECT subtotal, tax_amount, total_amount FROM sales_orders WHERE id=$1::uuid", oid)
        it = await conn.fetchrow("SELECT unit_price, line_total, jumlah_baris FROM sales_order_items WHERE sales_order_id=$1::uuid", oid)
        cek("SO total == 6.150.000 (bukan 6.149.999,96)", row["total_amount"] == D("6150000"), row["total_amount"])
        cek("harga 236.538,46 tersimpan 2dp", it["unit_price"] == D("236538.46"), it["unit_price"])
        cek("jumlah_baris tersimpan", it["jumlah_baris"] == D("6150000"), it["jumlah_baris"])
        # 2. tanpa line_amount: perilaku lama
        r0 = await SO.create_sales_order(req(uid), body(jumlah=None), Response())
        t0 = await conn.fetchval("SELECT total_amount FROM sales_orders WHERE id=$1::uuid", r0.data["id"])
        cek("tanpa line_amount = qty x harga lama", t0 == D("6149999.96"), t0)
        # 3. di luar toleransi -> 422
        try:
            await SO.create_sales_order(req(uid), body(jumlah="6149988"), Response())
            cek("jumlah di luar toleransi ditolak 422", False, "lolos")
        except HTTPException as e:
            cek("jumlah di luar toleransi ditolak 422", e.status_code == 422, e.status_code)
        # 4. konfirmasi + tagih parsial 10 + 16 (dan 1+1+24 pada SO kedua)
        for pecah in ([10, 16], [1, 1, 24]):
            rr = await SO.create_sales_order(req(uid), body(), Response())
            o = rr.data["id"]
            await SO.confirm_sales_order(req(uid), o)
            sid = await conn.fetchval("SELECT id FROM sales_order_items WHERE sales_order_id=$1::uuid", o)
            ctx = {"tenant_id": TID, "user_id": uid}
            ids = []
            for q in pecah:
                out = await SO._to_invoice_dalam_tx(conn, ctx, o, CI(items=[{"so_item_id": str(sid), "quantity": q}]))
                ids.append(out.get("id") or out.get("invoice_id") or (out.get("data") or {}).get("id"))
            tot = await conn.fetchval("SELECT COALESCE(SUM(total_amount),0) FROM sales_invoices WHERE sales_order_id=$1::uuid AND status<>'void'", o)
            sub = await conn.fetchval("SELECT COALESCE(SUM(sii.subtotal),0) FROM sales_invoice_items sii JOIN sales_invoices si ON si.id=sii.invoice_id WHERE si.sales_order_id=$1::uuid", o)
            cek(f"parsial {pecah}: Σ total faktur == total SO, sisa 0", tot == D("6150000") and sub == D("6150000"), (tot, sub))

        # 5. JURNAL (Law 1/16): faktur penuh dari SO 26 pcs -> posting -> jurnal AR == total faktur == SO; alokasi PSAK-72;
        #    NK penuh atas faktur itu -> sisa piutang faktur 0 (tanpa selisih 0,04).
        SI.get_pool = _gp
        import backend.api_gateway.app.routers.credit_notes as _cnmod
        _cnmod.get_pool = _gp
        rr = await SO.create_sales_order(req(uid), body(), Response())
        o = rr.data["id"]
        await SO.confirm_sales_order(req(uid), o)
        sid = await conn.fetchval("SELECT id FROM sales_order_items WHERE sales_order_id=$1::uuid", o)
        ctx = {"tenant_id": TID, "user_id": uid}
        out = await SO._to_invoice_dalam_tx(conn, ctx, o, CI(items=[{"so_item_id": str(sid), "quantity": 26}]))
        inv_id = out.get("id") or out.get("invoice_id") or (out.get("data") or {}).get("id")
        inv = await conn.fetchrow("SELECT id, invoice_number, total_amount, subtotal FROM sales_invoices WHERE id=$1::uuid", str(inv_id))
        cek("faktur penuh total == 6.150.000", inv["total_amount"] == D("6150000"), inv["total_amount"])
        try:
            await SI._internal_post_invoice(conn, ctx, inv["id"], inv["invoice_number"], inv["total_amount"])
            ar = await conn.fetchval("""SELECT COALESCE(SUM(jl.debit),0) FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id
                JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.source_id=$1::uuid AND je.tenant_id=$2
                AND je.source_type='INVOICE' AND c.account_type='RECEIVABLE'""", str(inv["id"]), TID)
            cek("jurnal AR debit == total faktur == 6.150.000", ar == D("6150000"), ar)
            alok = await conn.fetchval("SELECT COALESCE(SUM(allocated_amount),0) FROM sales_invoice_items WHERE invoice_id=$1", inv["id"])
            cek("Σ allocated_amount (PSAK-72) == subtotal faktur", alok == inv["subtotal"], (alok, inv["subtotal"]))
            sisa = await conn.fetchval("SELECT outstanding FROM compute_ar_outstanding($1) WHERE invoice_id=$2::uuid", TID, str(inv["id"]))
            cek("sisa piutang faktur == 6.150.000 sebelum NK", sisa == D("6150000"), sisa)
        except Exception as e:
            cek("posting faktur -> jurnal", False, repr(e)[:300])
            raise
        try:
            rk = await CN.rakit_dari_faktur(conn, TID, CreditNotePreviewRequest(original_invoice_id=str(inv["id"]), reason="pricing_error"))
            cek("NK penuh: line_amount = 6.150.000", rk["create"] and D(str(rk["create"]["items"][0].get("line_amount"))) == D("6150000"),
                rk.get("blocks"))
            rk["create"]["credit_note_date"] = date(2026, 10, 14)
            nk = await CN.buat_nota_kredit(conn, ctx, CreateCreditNoteRequest(**rk["create"]))
            nk_id = nk["data"]["id"]
            tn = await conn.fetchval("SELECT total_amount FROM credit_notes WHERE id=$1::uuid", nk_id)
            cek("NK penuh total == 6.150.000", tn == D("6150000"), tn)
            from uuid import UUID as _U
            await CN.posting_nota_kredit(conn, ctx, _U(str(nk_id)))
            sisa2 = await conn.fetchval("SELECT COALESCE((SELECT outstanding FROM compute_ar_outstanding($1) WHERE invoice_id=$2::uuid),0)", TID, str(inv["id"]))
            cek("sisa piutang faktur sesudah NK penuh == 0", sisa2 == 0, sisa2)
        except Exception as e:
            cek("NK penuh -> piutang 0", False, repr(e)[:400])
    finally:
        await tr.rollback()
        await conn.close()
    print("RINGKAS: GAGAL=%d %s" % (len(gagal), gagal))
    sys.exit(1 if gagal else 0)

asyncio.run(main())
