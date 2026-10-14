"""Harness NYATA JUMLAH-BARIS (V427): handler asli di atas scratch DB (dump + V427), tx ROLLBACK, tenant kaos saja."""
import asyncio, os, sys
from datetime import date
from decimal import Decimal as D
import asyncpg
os.environ.setdefault("OPENAI_API_KEY", "boneka")
from fastapi import HTTPException, Response
from starlette.requests import Request
from backend.api_gateway.app.routers import sales_orders as SO, sales_invoices as SI, credit_notes as CN, customer_deposits as CD
from backend.api_gateway.app.schemas.customer_deposits import CreateCustomerDepositRequest as CDR
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

        # 6. KASUS PEMILIK (SO-2610-0014): DP 6.150.000 -> ubah SO terkonfirmasi ke 26 pcs line_amount 6.150.000 -> faktur
        #    penuh -> posting -> DP diterapkan lewat jalur resmi -> sisa piutang 0, DP terpakai penuh, sisa DP 0.
        CD.get_pool = _gp
        akun = await conn.fetchval("""SELECT c.id FROM chart_of_accounts c WHERE c.tenant_id=$1 AND c.account_type='ASSET'
            AND c.is_header IS NOT TRUE AND c.account_code LIKE '1-102%' ORDER BY c.account_code LIMIT 1""", TID)
        if akun is None:
            akun = await conn.fetchval("SELECT id FROM chart_of_accounts WHERE tenant_id=$1 AND is_cash=true LIMIT 1", TID)
        r6 = await SO.create_sales_order(req(uid), body(qty=26, harga="240000", jumlah=None), Response())
        o6 = r6.data["id"]
        await SO.confirm_sales_order(req(uid), o6)
        dep = await CD.create_customer_deposit(req(uid), CDR(
            customer_id=str(cust["id"]), customer_name=cust["nama"], amount=6150000, deposit_date=date(2026, 10, 14),
            payment_method="cash", account_id=str(akun), sales_order_id=str(o6), auto_post=True))
        _dd_ = dep if isinstance(dep, dict) else dep.model_dump()
        dep_id = (_dd_.get("data") or _dd_)["id"]
        # (a) 6.149.988 (harga bulat 236.538 x 26) harus DITOLAK penjaga (total < DP)
        sp = conn.transaction(); await sp.start()
        try:
            await SO.update_sales_order(req(uid), o6, U(items=[{"id": str(await conn.fetchval("SELECT id FROM sales_order_items WHERE sales_order_id=$1::uuid", o6)),
                                                              "quantity": 26, "unit_price": D("236538")}]), Response())
            cek("ubah SO ke 6.149.988 (< DP 6.150.000) DITOLAK", False, "lolos")
        except HTTPException as e:
            d_ = e.detail if isinstance(e.detail, dict) else {"code": str(e.detail)}
            cek("ubah SO ke 6.149.988 (< DP 6.150.000) DITOLAK", e.status_code == 409 and "BELOW" in str(d_.get("code")), (e.status_code, d_))
        await sp.rollback()
        # (b) 6.150.000 via line_amount harus LOLOS
        sid6 = await conn.fetchval("SELECT id FROM sales_order_items WHERE sales_order_id=$1::uuid", o6)
        try:
            await SO.update_sales_order(req(uid), o6, U(items=[{"id": str(sid6), "quantity": 26, "unit_price": D("236538.46"),
                                                              "line_amount": D("6150000")}]), Response())
            t6 = await conn.fetchval("SELECT total_amount FROM sales_orders WHERE id=$1::uuid", o6)
            jb6 = await conn.fetchval("SELECT jumlah_baris FROM sales_order_items WHERE id=$1", sid6)
            cek("ubah SO terkonfirmasi ke line_amount 6.150.000 LOLOS, total 6.150.000", t6 == D("6150000") and jb6 == D("6150000"), (t6, jb6))
        except HTTPException as e:
            cek("ubah SO terkonfirmasi ke line_amount 6.150.000 LOLOS", False, (e.status_code, e.detail))
            raise
        out6 = await SO._to_invoice_dalam_tx(conn, {"tenant_id": TID, "user_id": uid}, o6, CI(items=[{"so_item_id": str(sid6), "quantity": 26}]))
        inv6 = out6.get("id") or out6.get("invoice_id") or (out6.get("data") or {}).get("id")
        try:
            await SI.post_invoice(req(uid), __import__("uuid").UUID(str(inv6)))
        except HTTPException as e:
            cek("post faktur (jalur resmi)", False, (e.status_code, e.detail))
            raise
        tot6 = await conn.fetchval("SELECT total_amount FROM sales_invoices WHERE id=$1::uuid", str(inv6))
        sisa6 = await conn.fetchval("SELECT COALESCE((SELECT outstanding FROM compute_ar_outstanding($1) WHERE invoice_id=$2::uuid),0)", TID, str(inv6))
        dep_sisa = await CD.compute_deposit_remaining(conn, TID, __import__("uuid").UUID(str(dep_id)))
        pakai = await conn.fetchval("SELECT COALESCE(SUM(amount_applied),0) FROM customer_deposit_applications WHERE deposit_id=$1::uuid AND invoice_id=$2::uuid", str(dep_id), str(inv6)) \
            if await conn.fetchval("SELECT to_regclass('customer_deposit_applications') IS NOT NULL") else None
        cek("faktur DP-kasus pemilik total == 6.150.000", tot6 == D("6150000"), tot6)
        cek("sisa piutang faktur == 0 sesudah DP diterapkan", sisa6 == 0, sisa6)
        cek("sisa DP == 0", dep_sisa == 0, dep_sisa)
        if pakai is not None:
            cek("DP terpakai == 6.150.000", pakai == D("6150000"), pakai)
        seimbang = await conn.fetchval("""SELECT COUNT(*) FROM journal_entries je WHERE je.tenant_id=$1 AND je.status='POSTED'
            AND je.source_id = ANY($2::uuid[]) AND ABS(je.total_debit - je.total_credit) > 0.005""", TID, [__import__("uuid").UUID(str(inv6)), __import__("uuid").UUID(str(dep_id))])
        cek("jurnal faktur+DP seimbang (debit==kredit)", seimbang == 0, seimbang)
    finally:
        await tr.rollback()
        await conn.close()
    print("RINGKAS: GAGAL=%d %s" % (len(gagal), gagal))
    sys.exit(1 if gagal else 0)

asyncio.run(main())
