"""UJI NYATA NK POTONGAN BRUTO PKP (putusan pemilik 21 Okt 2026). kaos SAJA, tiap kasus di transaksi yang SELALU di-ROLLBACK
('TES E2E'); is_pkp=true / faktor kode HANYA di dalam transaksi. Faktur ber-PPN dibuat + diposting lewat handler NYATA, lalu NK
potongan nominal (bentuk FE: unit_price bruto, tanpa tarif) dibuat lewat buat_nota_kredit + pratinjau _rencana + posting_nota_kredit.
Periksa: total NK == bruto; Σ baris (harga jual + PPN) == bruto; header == Σ baris; PPN ≈ bruto × PPN/bruto faktur (≤ 1 sen/kelompok);
jurnal seimbang (Σ debit == Σ kredit == header) dgn Dr PPN Keluaran == PPN NK, Cr Piutang == bruto; DTL Σ == PPN NK; sisa faktur turun
== bruto; pratinjau == hasil. Kontrol: non-PKP (kaos apa adanya) -> PPN 0, total == bruto (jalur lama). MERAH=1 -> harapan dibalik."""
import asyncio, os, sys
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from app.routers import credit_notes as CN, sales_invoices as SI
from app.schemas import sales_invoices as SIS

T = "kaos-biru-konveksi"
MERAH = os.environ.get("MERAH") == "1"
gagal = []


def cek(ok, label, info=""):
    print(("PASS " if ok else "FAIL ") + label + (f" | {info}" if info else ""), flush=True)
    if not ok: gagal.append(label)


def J(x):
    import json
    if hasattr(x, "body"): return json.loads(x.body)
    if hasattr(x, "model_dump"): return x.model_dump(mode="json")
    return x


async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    SI.get_pool = gp; CN.get_pool = gp
    u = await c.fetchval('SELECT id FROM "User" WHERE "tenantId"=$1 ORDER BY "createdAt" LIMIT 1', T)
    cust = await c.fetchrow("SELECT id, nama FROM customers WHERE tenant_id=$1 ORDER BY created_at LIMIT 1", T)
    ctx = {"tenant_id": T, "user_id": u}
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": T, "user_id": str(u)}), headers={}, client=None, url=SimpleNamespace(path="/"))
    kode = {r["code"]: str(r["id"]) for r in await c.fetch(
        "SELECT id, code FROM tax_codes WHERE tenant_id=$1 AND tax_type='ppn' AND direction='output'", T)}
    KASUS = [
        # nama, pkp, faktor PPN-12 (num, den), baris faktur, ongkir, diskon dok, bruto potongan
        ("A 11% + ongkir ikut barang + diskon dok", True, None,
         [("Kaos A", 3, "150000", "PPN-11-OUT")], 25000, 10000, Decimal("111111")),
        ("B 12% x 11/12 + baris tanpa pajak", True, (11, 12),
         [("Kaos B", 2, "200000", "PPN-12-OUT"), ("Jasa sablon", 1, "55555", None)], 0, 0, Decimal("123457")),
        ("C 12% x 11/12 bruto kecil 1 rupiah", True, (11, 12), [("Kaos C", 1, "100000", "PPN-12-OUT")], 0, 0, Decimal("1")),
        ("D kontrol non-PKP (jalur lama)", False, None, [("Kaos D", 1, "100000", None)], 0, 0, Decimal("50000")),
    ]
    for nama, pkp, faktor, baris, ongkir, diskon, G in KASUS:
        tx = c.transaction(); await tx.start()
        try:
            await c.execute('UPDATE "Tenant" SET is_pkp = $2 WHERE id = $1', T, pkp)
            if faktor:
                await c.execute("UPDATE tax_codes SET dpp_factor_num=$2, dpp_factor_den=$3 WHERE id=$1", UUID(kode["PPN-12-OUT"]), *faktor)
            body = SIS.CreateInvoiceRequest(
                customer_id=str(cust["id"]), customer_name=cust["nama"], invoice_date=date(2026, 10, 21), notes="TES E2E NK PKP (ROLLBACK)",
                items=[SIS.InvoiceItemCreate(description=d, quantity=Decimal(q), unit_price=Decimal(p), tax_code_id=kode.get(k) if k else None,
                                             tax_rate=float(k.split("-")[1]) if k else 0, description_custom=True) for d, q, p, k in baris],
                discount_amount=diskon, shipping_amount=ongkir, auto_post=True)
            inv = J(await SI.create_invoice(req, body))
            inv = inv.get("data", inv)
            iid = UUID(inv["id"])
            si = await c.fetchrow("SELECT status, total_amount, tax_amount, shipping_amount, shipping_tax_amount FROM sales_invoices WHERE id=$1", iid)
            sisa0 = await c.fetchval("SELECT outstanding FROM compute_ar_outstanding($1) WHERE invoice_id=$2", T, iid)
            print(f"\n== {nama}: faktur {inv.get('invoice_number')} {si['status']} total {si['total_amount']} PPN {si['tax_amount']} "
                  f"ongkir {si['shipping_amount']}/{si['shipping_tax_amount']} sisa {sisa0} -> potongan bruto {G}", flush=True)
            cn_body = CN.CreateCreditNoteRequest(
                customer_id=str(cust["id"]), customer_name=cust["nama"], credit_note_date=date(2026, 10, 21), original_invoice_id=str(iid),
                reason="discount", reason_detail="Potongan", notes="TES E2E NK PKP (ROLLBACK)",
                items=[CN.CreditNoteItemCreate(description="Potongan harga", quantity=Decimal("1"), unit_price=G, item_id=None)])
            ren = await CN._rencana_nota_kredit(c, ctx, cn_body)
            d = await CN.buat_nota_kredit(c, ctx, cn_body)
            cid = UUID(d["data"]["id"])
            h = await c.fetchrow("SELECT subtotal, tax_amount, total_amount FROM credit_notes WHERE id=$1", cid)
            it = await c.fetch("SELECT tax_code_id, tax_rate, unit_price, subtotal, tax_amount, total, dpp, dpp_harga_jual FROM credit_note_items "
                               "WHERE credit_note_id=$1 ORDER BY line_number", cid)
            harap_total = G + (Decimal("0.01") if MERAH else 0)
            cek(h["total_amount"] == harap_total, f"{nama}: total NK == bruto", f"{h['total_amount']} vs {harap_total}")
            cek(all(r["subtotal"] + r["tax_amount"] == r["total"] for r in it) and sum(r["total"] for r in it) == G,
                f"{nama}: Σ baris harga jual + PPN == bruto", str([(str(r['subtotal']), str(r['tax_amount']), str(r['total'])) for r in it]))
            cek(sum(r["tax_amount"] for r in it) == h["tax_amount"] and sum(r["subtotal"] for r in it) == h["subtotal"],
                f"{nama}: header == Σ baris")
            cek(ren["doc"] is not None and Decimal(str(ren["doc"]["total_amount"])) == h["total_amount"]
                and Decimal(str(ren["doc"]["tax_amount"])) == h["tax_amount"] and not ren["blocks"], f"{nama}: pratinjau == hasil, tanpa blok",
                str(ren["blocks"])[:200])
            if pkp:
                rasio = Decimal(si["tax_amount"]) / Decimal(si["total_amount"])
                cek(abs(h["tax_amount"] - G * rasio) <= Decimal("0.01") * max(1, len(it)), f"{nama}: PPN NK ≈ bruto × PPN/total faktur",
                    f"{h['tax_amount']} vs {G * rasio:.4f}")
                cek(h["tax_amount"] > 0 or G < 10, f"{nama}: PPN keluaran DIKURANGI (PKP)", str(h["tax_amount"]))
            else:
                cek(h["tax_amount"] == 0 and h["subtotal"] == G, f"{nama}: non-PKP PPN 0, jalur lama")
            await CN.posting_nota_kredit(c, ctx, cid)
            jl = await c.fetch("""SELECT coa.account_code, coa.name, jl.debit, jl.credit, je.total_debit, je.total_credit FROM journal_entries je
                                  JOIN journal_lines jl ON jl.journal_id = je.id JOIN chart_of_accounts coa ON coa.id = jl.account_id
                                  WHERE je.tenant_id=$1 AND je.source_id=$2 AND je.source_type='CREDIT_NOTE'""", T, cid)
            for j in jl: print("   jurnal", j["account_code"], j["name"], "D", j["debit"], "K", j["credit"], flush=True)
            sd, sk = sum(j["debit"] for j in jl), sum(j["credit"] for j in jl)
            cek(sd == sk == G and all(j["total_debit"] == G for j in jl), f"{nama}: jurnal seimbang == header == bruto", f"D {sd} K {sk}")
            vat = sum(j["debit"] for j in jl if j["account_code"] == "2-10600")
            cek(vat == h["tax_amount"], f"{nama}: Dr PPN Keluaran == PPN NK", f"{vat} vs {h['tax_amount']}")
            ar = sum(j["credit"] for j in jl if j["account_code"] == "1-10400")
            cek(ar == G, f"{nama}: Cr Piutang == bruto", str(ar))
            dtl = await c.fetchval("SELECT COALESCE(SUM(tax_amount),0) FROM document_tax_lines WHERE tenant_id=$1 AND document_type='CREDIT_NOTE' AND document_id=$2", T, cid)
            cek(Decimal(str(dtl)) == h["tax_amount"], f"{nama}: DTL Σ == PPN NK", str(dtl))
            sisa1 = await c.fetchval("SELECT outstanding FROM compute_ar_outstanding($1) WHERE invoice_id=$2", T, iid)
            cek(sisa0 - sisa1 == G, f"{nama}: sisa faktur turun == bruto", f"{sisa0} -> {sisa1}")
        except Exception as e:
            import traceback; traceback.print_exc()
            cek(False, f"{nama}: galat {type(e).__name__}", str(getattr(e, "detail", e))[:300])
        finally:
            await tx.rollback()
    cek(await c.fetchval('SELECT is_pkp FROM "Tenant" WHERE id=$1', T) is False, "kaos is_pkp kembali False (rollback)")
    await c.close()
    print("RINGKAS gagal=%d %s" % (len(gagal), gagal[:6])); sys.exit(1 if gagal else 0)

asyncio.run(main())
