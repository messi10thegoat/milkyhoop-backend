"""UJI NYATA alur refund pelanggan (kasus pemilik grapgrap 031-09-26) di KAOS, SEMUA ROLLBACK. Handler/inti ASLI.
NK penuh atas faktur LUNAS lewat uang muka -> saldo kredit KRD -> refund KRD bertanggal lampau (periode open);
refund/apply DARI NK ditolak 409 berkode; NK bertaut tanpa KRD ditolak; NK bebas TETAP bisa direfund.
Kontrol merah: jalankan dengan kode master (refund NK lolos = bug)."""
import asyncio
import json
import os
import sys
import uuid
from datetime import date
from decimal import Decimal

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg
from fastapi import HTTPException
from starlette.requests import Request

T = "kaos-biru-konveksi"
USER = uuid.UUID("0bccdb25-fdf0-4e99-9024-b9a20846f76c")
INV = uuid.UUID("7bf051ea-27ed-408c-a7c6-5009344fd2b6")      # INV-2609-0079 lunas via LPS-2609-0078
NK_BERTAUT = uuid.UUID("20d25110-7cb1-454b-9819-7655a3c8930c")  # CN-2609-0028 bertaut, tanpa KRD
NK_BEBAS = uuid.UUID("a90554d5-6775-4b82-a1ab-d1a855ae3a16")    # CN-2609-0006 bebas 25.000
BANK = "56842900-0ae4-4c25-8ad5-d0726a36e21b"
COA_BANK = "15105bf6-183e-4f93-abcc-5689f22fa394"
TGL_REFUND = date(2026, 10, 1)
HASIL = []


def cek(n, ok, info=""):
    HASIL.append((n, bool(ok))); print(("PASS " if ok else "FAIL ") + n + (f" | {info}" if info else ""), flush=True)


class _Acq:
    def __init__(self, c): self.c = c
    async def __aenter__(self): return self.c
    async def __aexit__(self, *a): return False


class PoolSatu:
    def __init__(self, c): self.c = c
    def acquire(self): return _Acq(self.c)


class _Batal(Exception):
    pass


async def sp(conn, fn):
    out = {}
    try:
        async with conn.transaction():
            out["v"] = await fn()
            raise _Batal()
    except _Batal:
        pass
    return out["v"]


async def galat(coro):
    try:
        return {"ok": await coro}
    except HTTPException as e:
        return {"HTTP": e.status_code, "detail": e.detail}


def req():
    r = Request({"type": "http", "method": "GET", "headers": [], "path": "/", "query_string": b"", "state": {}})
    r.state.user = {"tenant_id": T, "user_id": str(USER), "role": "OWNER"}
    return r


async def jumlah(c):
    q = {"je": "SELECT count(*) FROM journal_entries WHERE tenant_id=$1",
         "audit": "SELECT count(*) FROM audit_logs WHERE tenant_id=$1",
         "bank_tx": "SELECT count(*) FROM bank_transactions WHERE tenant_id=$1",
         "cn": "SELECT md5(string_agg(id::text||status||amount_applied::text||amount_refunded::text, ',' ORDER BY id)) FROM credit_notes WHERE tenant_id=$1",
         "dep": "SELECT md5(string_agg(id::text||status||amount_refunded::text, ',' ORDER BY id)) FROM customer_deposits WHERE tenant_id=$1"}
    return {k: await c.fetchval(v, T) for k, v in q.items()}


async def jurnal(c, jid):
    return [(r["account_code"], r["account_type"], r["debit"], r["credit"]) for r in await c.fetch(
        """SELECT coa.account_code, coa.account_type, jl.debit, jl.credit FROM journal_lines jl
           JOIN chart_of_accounts coa ON coa.id = jl.account_id WHERE jl.journal_id = $1 ORDER BY jl.line_number""", jid)]


async def main():
    import app.routers.credit_notes as C
    import app.routers.customer_deposits as CD
    from app.schemas.credit_notes import (ApplyCreditNoteItem, ApplyCreditNoteRequest, CreateCreditNoteRequest,
                                          CreditNoteItemCreate, RefundCreditNoteRequest)
    from app.schemas.customer_deposits import RefundCustomerDepositRequest
    from app.services import policy_engine_client as PEC
    pe = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
    PEC.init_policy_engine(pe)
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    tr = c.transaction(); await tr.start()
    ctx = {"tenant_id": T, "user_id": USER}
    pool = PoolSatu(c)

    async def gp(): return pool
    C.get_pool = gp; CD.get_pool = gp
    sebelum = await jumlah(c)
    sum0 = (await C.get_credit_notes_summary(req()))
    sum0 = (sum0.get("data", sum0) if isinstance(sum0, dict) else sum0.model_dump())
    try:
        inv = await c.fetchrow("SELECT * FROM sales_invoices WHERE id=$1 AND tenant_id=$2", INV, T)
        items = await c.fetch("SELECT * FROM sales_invoice_items WHERE invoice_id=$1 ORDER BY line_number", INV)
        ar0 = await c.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ar_outstanding($1) WHERE invoice_id=$2", T, INV)

        async def alur():
            body = CreateCreditNoteRequest(
                customer_id=str(inv["customer_id"]), customer_name=inv["customer_name"], credit_note_date=TGL_REFUND,
                original_invoice_id=str(INV), reason="other", reason_detail="TES E2E pesanan batal, telat deadline",
                items=[CreditNoteItemCreate(item_id=str(i["item_id"]) if i["item_id"] else None, description=i["description"] or "TES E2E",
                                            quantity=i["quantity"], unit_price=i["unit_price"], discount_percent=float(i["discount_percent"] or 0),
                                            tax_rate=float(i["tax_rate"] or 0)) for i in items])
            nk = await C.buat_nota_kredit(c, ctx, body)
            nk_id = uuid.UUID(str(nk.get("id") or nk.get("data", {}).get("id")))
            await C.posting_nota_kredit(c, ctx, nk_id)
            cn = await c.fetchrow("SELECT * FROM credit_notes WHERE id=$1", nk_id)
            krd = await c.fetchrow("SELECT * FROM customer_deposits WHERE id=$1", cn["created_deposit_id"])
            o = {"nk": cn["credit_note_number"], "total": cn["total_amount"], "krd": krd["deposit_number"] if krd else None,
                 "krd_amount": krd["amount"] if krd else None, "j_nk": await jurnal(c, cn["journal_id"]),
                 "ar_sesudah_nk": await c.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ar_outstanding($1) WHERE invoice_id=$2", T, INV),
                 "keadaan": await C._keadaan_nk(c, T, nk_id)}
            rb = RefundCreditNoteRequest(amount=cn["total_amount"], refund_date=TGL_REFUND, payment_method="transfer", bank_account_id=BANK)
            o["refund_nk"] = await sp(c, lambda: galat(C.refund_nota_kredit_core(c, ctx, nk_id, rb)))
            o["apply_nk"] = await sp(c, lambda: galat(C.apply_nota_kredit_core(c, ctx, nk_id, ApplyCreditNoteRequest(
                applications=[ApplyCreditNoteItem(invoice_id=str(INV), amount=cn["total_amount"])]))))
            o["pv_refund_nk"] = await C._pratinjau_nk_aksi(ctx, nk_id, "refund", rb)
            det0 = await C.get_credit_note(req(), nk_id)
            o["cc0"] = (det0.get("data") if isinstance(det0, dict) else det0.model_dump()["data"])
            o["cc0"] = o["cc0"].get("customer_credit") if isinstance(o["cc0"], dict) else None
            bt0 = await c.fetchval("SELECT count(*) FROM bank_transactions WHERE tenant_id=$1", T)
            dr = RefundCustomerDepositRequest(amount=krd["amount"], refund_date=TGL_REFUND, payment_method="transfer",
                                              account_id=COA_BANK, bank_account_id=BANK, notes="TES E2E refund pesanan batal")
            await CD.refund_deposit_core(c, ctx, krd["id"], dr)
            ref = await c.fetchrow("SELECT * FROM journal_entries WHERE tenant_id=$1 AND source_type='DEPOSIT_REFUND' ORDER BY created_at DESC LIMIT 1", T)
            o["j_refund"] = await jurnal(c, ref["id"]); o["j_refund_tgl"] = ref["journal_date"]
            o["bank_tx"] = [dict(r) for r in await c.fetch(
                "SELECT transaction_date, amount, transaction_type, reference_type FROM bank_transactions WHERE tenant_id=$1 ORDER BY created_at DESC LIMIT 1", T)]
            o["bank_tx_baru"] = await c.fetchval("SELECT count(*) FROM bank_transactions WHERE tenant_id=$1", T) - bt0
            from app.routers.customer_deposits import compute_deposit_remaining
            o["krd_sisa"] = await compute_deposit_remaining(c, T, krd["id"])
            det1 = await C.get_credit_note(req(), nk_id)
            d1 = det1.get("data") if isinstance(det1, dict) else det1.model_dump()["data"]
            o["cc1"] = d1.get("customer_credit")
            o["ar_akhir"] = await c.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ar_outstanding($1) WHERE invoice_id=$2", T, INV)
            return o
        o = await sp(c, alur)
        print("UKUR", json.dumps(o, default=str)[:1500], flush=True)
        cek("0 faktur lunas sebelum NK (piutang 0)", ar0 == 0, str(ar0))
        cek("1 NK penuh bertaut faktur lunas -> SELURUHNYA saldo kredit KRD", o["krd"] and o["krd"].startswith("KRD") and o["krd_amount"] == o["total"], f"{o['nk']} {o['krd']} {o['krd_amount']}")
        cek("2 jurnal NK: Dr Retur/Dimuka (pendapatan dibalik) / Cr Uang Muka Pelanggan, tanpa baris piutang",
            sum(d for _, _, d, _ in o["j_nk"]) == o["total"] and all(t != "RECEIVABLE" for _, t, _, _ in o["j_nk"]) and len([x for x in o["j_nk"] if x[3] > 0]) == 1,
            str(o["j_nk"]))
        cek("3 piutang faktur tetap 0 sesudah NK", o["ar_sesudah_nk"] == 0)
        cek("4 sisa kredit NK = 0; saldo kredit di KRD", o["keadaan"]["remaining"] == 0.0 and (o["keadaan"].get("customer_credit") or {}).get("remaining") == float(o["total"]),
            str({k: o["keadaan"].get(k) for k in ("remaining", "customer_credit")}))
        cek("5 refund DARI NK ditolak 409 CN_KREDIT_DI_UANG_MUKA (pesan menyebut KRD)",
            o["refund_nk"].get("HTTP") == 409 and o["refund_nk"]["detail"].get("code") == "CN_KREDIT_DI_UANG_MUKA" and o["krd"] in o["refund_nk"]["detail"]["message"], str(o["refund_nk"])[:200])
        cek("6 apply DARI NK ditolak 409 CN_KREDIT_DI_UANG_MUKA", o["apply_nk"].get("HTTP") == 409 and o["apply_nk"]["detail"].get("code") == "CN_KREDIT_DI_UANG_MUKA", str(o["apply_nk"])[:200])
        cek("7 pratinjau refund NK ikut memblok (kode sama)", not o["pv_refund_nk"]["can_proceed"] and [b["code"] for b in o["pv_refund_nk"]["blocks"]] == ["CN_KREDIT_DI_UANG_MUKA"],
            str(o["pv_refund_nk"]["blocks"])[:200])
        cek("8 refund KRD: jurnal Dr Uang Muka Pelanggan / Cr Bank 1-10201, tanggal 1 Okt (lampau, periode open)",
            o["j_refund_tgl"] == TGL_REFUND and o["j_refund"][0][2] == o["total"] and o["j_refund"][0][1] == "LIABILITY" and o["j_refund"][-1][3] == o["total"] and o["j_refund"][-1][0] == "1-10201",
            str(o["j_refund"]))
        cek("9 bank_transactions: satu withdrawal -nilai tanggal 1 Okt (DEPOSIT_REFUND)",
            o["bank_tx_baru"] == 1 and o["bank_tx"][0]["amount"] == -o["total"] and o["bank_tx"][0]["transaction_date"] == TGL_REFUND, str(o["bank_tx"]))
        if "customer_credit" in str(o.get("cc0")) or o.get("cc0") is not None:
            pass
        cek("13 GET detail NK: customer_credit KRD sisa penuh sebelum refund, 0 sesudah (kontrak FE)",
            o.get("cc0") and o["cc0"]["deposit_number"] == o["krd"] and o["cc0"]["remaining_amount"] == float(o["total"])
            and o.get("cc1") and o["cc1"]["remaining_amount"] == 0.0, f"{o.get('cc0')} {o.get('cc1')}")
        cek("14 detail 409 refund NK membawa deposit_number", o["refund_nk"].get("detail", {}).get("deposit_number") == o["krd"] if isinstance(o["refund_nk"].get("detail"), dict) else False)
        cek("10 KRD saldo 0; piutang faktur tetap 0", o["krd_sisa"] == 0 and o["ar_akhir"] == 0, f"{o['krd_sisa']} {o['ar_akhir']}")

        rb = RefundCreditNoteRequest(amount=Decimal("100000"), refund_date=TGL_REFUND, payment_method="transfer", bank_account_id=BANK)
        b2 = await sp(c, lambda: galat(C.refund_nota_kredit_core(c, ctx, NK_BERTAUT, rb)))
        cek("11 NK bertaut TANPA KRD (CN-2609-0028): refund 409 CN_SUDAH_MEMOTONG_FAKTUR",
            b2.get("HTTP") == 409 and b2["detail"].get("code") == "CN_SUDAH_MEMOTONG_FAKTUR", str(b2)[:200])
        rb3 = RefundCreditNoteRequest(amount=Decimal("25000"), refund_date=TGL_REFUND, payment_method="transfer", bank_account_id=BANK)
        b3 = await sp(c, lambda: galat(C.refund_nota_kredit_core(c, ctx, NK_BEBAS, rb3)))
        cek("12 NK BEBAS (CN-2609-0006) TETAP bisa direfund", "ok" in b3, str(b3)[:200])
        db = await C.get_credit_note(req(), NK_BEBAS)
        db = db.get("data") if isinstance(db, dict) else db.model_dump()["data"]
        cek("15 GET detail NK bebas: customer_credit null", db.get("customer_credit") is None)
        sum1 = (await C.get_credit_notes_summary(req()))
        sum1 = (sum1.get("data", sum1) if isinstance(sum1, dict) else sum1.model_dump())
        print("RINGKASAN available_balance kaos:", sum1.get("available_balance"), flush=True)
    finally:
        await tr.rollback(); await pe.close()
    cek("AKHIR nol jejak (je, audit, bank_tx, NK, uang muka)", await jumlah(c) == sebelum)
    await c.close()
    g = [n for n, ok in HASIL if not ok]
    print(f"RINGKAS {len(HASIL)-len(g)}/{len(HASIL)}", "GAGAL: " + "; ".join(g) if g else "SEMUA LULUS")

asyncio.run(main())
