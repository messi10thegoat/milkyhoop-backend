"""Bayar banyak faktur (4 Nov 2026): uji nyata BACA-SAJA kaos + grapgrap-manado (izin pemilik langsung: baca saja).

Dijalankan DUA kali oleh .sh: MODE=LAMA (pohon utama) menulis keluaran ke /out/lama.json; MODE=BARU (worktree) membandingkan.
  H1 pelunasan SO (semua SO ber-faktur terbit; bawaan + nominal separuh): BARU == LAMA, kecuali SO yang urutannya berubah
     karena kunci tunggal (jatuh tempo dulu) -> DICETAK per id (putusan MASTER 2: laporkan sebelum rilis).
  H2 /bill-payments/preview + /receive-payments/preview TANPA kolam: BARU == LAMA (tanpa kunci baru `notes`).
  H3 BARU dengan kolam: Σapplied = min(total, Σsisa); urutan = jatuh tempo/tanggal/nomor dihitung SENDIRI dari kolom DB;
     payload hasil dipratinjau ulang tanpa kolam -> bisa disimpan, alokasi sama; lebih -> catatan kelebihan.
  U  ukur: bill_payments_v2 unapplied_amount > 0 (putusan MASTER 1b).
Koneksi default_transaction_read_only=on. Tenant tak ada = GAGAL. MERAH=urut: kunci dibalik -> BARU WAJIB merah."""
import asyncio
import json
import os
import sys
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import asyncpg

sys.path.insert(0, "/app/backend/api_gateway")
TENANT = ("kaos-biru-konveksi", "grapgrap-manado")
MODE, OUT, MERAH = os.environ["MODE"], "/out", os.environ.get("MERAH", "")
gagal = []


def cek(ok, pesan, rinci=""):
    print(("PASS " if ok else "GAGAL ") + pesan + (f" | {rinci}" if rinci else ""))
    if not ok:
        gagal.append(pesan)


def j(x):
    return json.dumps(x, default=str, sort_keys=True)


def urut_db(r, jt, tg, no):  # urutan tertua dihitung SENDIRI (bukan lewat alokasi_tertua)
    return (r[jt] is None, r[jt] or date.min, r[tg] or date.min, r[no] or "")


async def main():
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3,
                                     server_settings={"default_transaction_read_only": "on"})
    from app.routers import bill_payments as BPR
    from app.routers import receive_payments as RPR
    from app.services import so_pelunasan as SP
    if MODE == "BARU" and MERAH == "urut":
        from app.services import alokasi_tertua as AT
        asli = AT.kunci_tertua

        class Balik:
            def __init__(self, k):
                self.k = k

            def __lt__(self, o):
                return self.k > o.k
        AT.kunci_tertua = lambda *a: Balik(asli(*a))

    async def _pool():
        return pool
    BPR.get_pool = _pool
    RPR.get_pool = _pool
    hasil, lama = {}, (json.load(open(f"{OUT}/lama.json")) if MODE == "BARU" else {})

    async with pool.acquire() as c:
        ada = {r["id"] for r in await c.fetch('SELECT id FROM "Tenant" WHERE id = ANY($1::text[])', list(TENANT))}
        cek(ada == set(TENANT), f"tenant ada: {sorted(ada)}")
        cek(await c.fetchval("SHOW transaction_read_only") == "on", "koneksi read-only")

    for t in TENANT:
        req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": t, "user_id": None}), headers={})
        ctx = {"tenant_id": t, "user_id": None}
        async with pool.acquire() as c:
            # ── H1 pelunasan SO ──
            sos = await c.fetch("""SELECT DISTINCT so.id, so.order_number FROM sales_orders so
                                   JOIN sales_invoices si ON si.sales_order_id = so.id AND si.tenant_id = so.tenant_id
                                   WHERE so.tenant_id = $1 AND si.status NOT IN ('draft', 'void') ORDER BY so.order_number""", t)
            berubah_urut, beda = [], []
            for so in sos:
                inv = await c.fetch("""SELECT si.id, si.invoice_number, si.invoice_date, si.due_date, COALESCE(o.s, 0) AS sisa
                                       FROM sales_invoices si LEFT JOIN (SELECT invoice_id, SUM(outstanding) s FROM compute_ar_outstanding($1)
                                       GROUP BY invoice_id) o ON o.invoice_id = si.id
                                       WHERE si.tenant_id = $1 AND si.sales_order_id = $2 AND si.status NOT IN ('draft', 'void')""",
                                    t, so["id"])
                lama_u = [r["id"] for r in sorted(inv, key=lambda r: (r["invoice_date"], r["invoice_number"], str(r["id"])))]
                baru_u = [r["id"] for r in sorted(inv, key=lambda r: (*urut_db(r, "due_date", "invoice_date", "invoice_number"), str(r["id"])))]
                sisa = sum((Decimal(str(r["sisa"])) for r in inv if r["sisa"] > 0), Decimal("0"))
                if lama_u != baru_u and sum(1 for r in inv if r["sisa"] > 0) > 1:
                    berubah_urut.append(so["order_number"])
                for var, amt in (("bawaan", None), ("separuh", (sisa / 2).quantize(Decimal("1")) if sisa > 1 else None)):
                    if var == "separuh" and amt is None:
                        continue
                    tr = c.transaction(readonly=True)
                    await tr.start()
                    try:
                        out = await SP.pratinjau_pelunasan_so(c, ctx, so["id"], SP.SOReceivePaymentPreviewRequest(amount=amt))
                    finally:
                        await tr.rollback()
                    k = f"{t}|so|{so['id']}|{var}"
                    hasil[k] = j(out)
                    if MODE == "BARU" and lama.get(k) != hasil[k]:
                        beda.append(f"{so['order_number']}/{var}")
            print(f"H1 {t}: {len(sos)} SO; urutan berubah (sisa>1 faktur): {berubah_urut or 'tidak ada'}")
            if MODE == "BARU":
                cek(set(b.split("/")[0] for b in beda) <= set(berubah_urut) and not [b for b in beda if b.endswith("bawaan")],
                    f"H1 {t}: pratinjau SO == LAMA kecuali SO berurutan baru (separuh)", f"beda={beda}")

            # ── data uji H2/H3 ──
            ap = await c.fetch("""SELECT r.bill_id, r.outstanding, b.vendor_id, b.invoice_number, b.due_date, b.issue_date
                                  FROM compute_ap_rincian($1) r JOIN bills b ON b.id = r.bill_id AND b.tenant_id = $1
                                  WHERE r.outstanding > 0""", t)
            per_v = {}
            for r in ap:
                per_v.setdefault(str(r["vendor_id"]), []).append(r)
            vend = max(per_v.items(), key=lambda kv: len(kv[1]), default=(None, []))
            bank = await c.fetchrow("""SELECT ba.id, ba.coa_id FROM bank_accounts ba JOIN chart_of_accounts coa ON coa.id = ba.coa_id
                                       WHERE ba.tenant_id = $1 AND ba.is_active AND NOT coa.is_header AND ba.account_type <> 'credit_card'
                                       ORDER BY ba.created_at LIMIT 1""", t)
            ar = await c.fetch("""SELECT o.invoice_id, o.outstanding, si.customer_id, si.invoice_number, si.due_date, si.invoice_date
                                  FROM (SELECT invoice_id, SUM(outstanding) outstanding FROM compute_ar_outstanding($1) GROUP BY invoice_id) o
                                  JOIN sales_invoices si ON si.id = o.invoice_id WHERE o.outstanding > 0""", t)
            per_c = {}
            for r in ar:
                per_c.setdefault(str(r["customer_id"]), []).append(r)
            cust = max(per_c.items(), key=lambda kv: len(kv[1]), default=(None, []))
            u = await c.fetchrow("""SELECT count(*) n, COALESCE(sum(unapplied_amount), 0) s FROM bill_payments_v2
                                    WHERE tenant_id = $1 AND unapplied_amount > 0 AND status NOT IN ('void', 'voided', 'draft')""", t)
            print(f"U {t}: bill_payments_v2 unapplied>0 = {u['n']} (Rp {u['s']})")
        hari = date.today()

        # ── H2 tanpa kolam (bill + receive) ──
        if vend[0] and bank:
            b0 = sorted(vend[1], key=lambda r: urut_db(r, "due_date", "issue_date", "invoice_number"))[0]
            dasar = {"vendor_id": vend[0], "payment_date": hari, "bank_account_id": str(bank["id"]), "total_amount": Decimal("1000"),
                     "allocations": [{"bill_id": str(b0["bill_id"]), "amount_applied": min(Decimal("1000"), Decimal(str(b0["outstanding"])))}]}
            Mdl = BPR.CreateBillPaymentRequest if MODE == "LAMA" else BPR.BillPaymentPreviewRequest
            r = (await BPR.preview_create_bill_payment(req, Mdl(**dasar)))["data"]
            k = f"{t}|bp|tanpa-kolam"
            hasil[k] = j({x: v for x, v in r.items() if x not in ("notes",)})
            if MODE == "BARU":
                cek(lama.get(k) == hasil[k], f"H2 {t}: /bill-payments/preview tanpa kolam == LAMA")
                # ── H3 kolam ──
                bills = sorted(vend[1], key=lambda r: urut_db(r, "due_date", "issue_date", "invoice_number"))
                tot_sisa = sum((Decimal(str(x["outstanding"])) for x in bills), Decimal("0"))
                for nama, total in (("sebagian", (tot_sisa * Decimal("0.6")).quantize(Decimal("0.01"))), ("lebih", tot_sisa + 1000)):
                    p = BPR.BillPaymentPreviewRequest(**{**dasar, "allocations": [], "total_amount": total,
                                                         "bill_ids": [str(x["bill_id"]) for x in reversed(bills)]})
                    r = (await BPR.preview_create_bill_payment(req, p))["data"]
                    sisa_t, harap = total, []
                    for x in bills:
                        a = min(Decimal(str(x["outstanding"])), max(sisa_t, Decimal("0")))
                        harap.append((str(x["bill_id"]), a))
                        sisa_t -= a
                    dapat = [(str(x["bill_id"]), Decimal(str(x["amount_applied"]))) for x in r["bills"]]
                    cek(dapat == harap, f"H3 {t} bill {nama}: urutan + nominal == hitungan sendiri", f"{len(bills)} tagihan")
                    cek(sum(a for _, a in dapat) == min(total, tot_sisa), f"H3 {t} bill {nama}: Σapplied = min(total, Σsisa)")
                    ulang = (await BPR.preview_create_bill_payment(req, BPR.BillPaymentPreviewRequest(**r["payload"])))["data"]
                    cek(r["can_proceed"] and ulang["can_proceed"]
                        and [(x["bill_id"], x["amount_applied"]) for x in ulang["bills"]] == [(x["bill_id"], x["amount_applied"])
                                                                                            for x in r["bills"] if x["amount_applied"] > 0],
                        f"H3 {t} bill {nama}: payload terselesaikan lolos pratinjau ulang", str(r["blocks"]))
                    if nama == "lebih":
                        cek([n["code"] for n in r["notes"]] == ["BP_OVERPAYMENT_PREPAID"], f"H3 {t} bill lebih: catatan jujur",
                            str(r["notes"]))
        else:
            print(f"H2/H3 {t}: bill dilewati (tak ada vendor bersisa / rekening)")
        if cust[0] and bank:
            i0 = sorted(cust[1], key=lambda r: urut_db(r, "due_date", "invoice_date", "invoice_number"))[0]
            dasar = {"customer_id": cust[0], "payment_date": hari, "bank_account_id": str(bank["coa_id"]), "total_amount": Decimal("1000"),
                     "allocations": [{"invoice_id": str(i0["invoice_id"]), "amount_applied": min(Decimal("1000"), Decimal(str(i0["outstanding"])))}]}
            Mdl = RPR.CreateReceivePaymentRequest if MODE == "LAMA" else RPR.ReceivePaymentPreviewRequest
            r = (await RPR.preview_create_receive_payment(req, Mdl(**dasar)))["data"]
            k = f"{t}|rp|tanpa-kolam"
            hasil[k] = j({x: v for x, v in r.items() if x not in ("notes",)})
            if MODE == "BARU":
                cek(lama.get(k) == hasil[k], f"H2 {t}: /receive-payments/preview tanpa kolam == LAMA")
                invs = sorted(cust[1], key=lambda r: urut_db(r, "due_date", "invoice_date", "invoice_number"))
                tot_sisa = sum((Decimal(str(x["outstanding"])) for x in invs), Decimal("0"))
                for nama, total in (("sebagian", (tot_sisa * Decimal("0.6")).quantize(Decimal("0.01"))), ("lebih", tot_sisa + 1000)):
                    p = RPR.ReceivePaymentPreviewRequest(**{**dasar, "allocations": [], "total_amount": total,
                                                            "invoice_ids": [str(x["invoice_id"]) for x in reversed(invs)]})
                    r = (await RPR.preview_create_receive_payment(req, p))["data"]
                    sisa_t, harap = total, []
                    for x in invs:
                        a = min(Decimal(str(x["outstanding"])), max(sisa_t, Decimal("0")))
                        harap.append((str(x["invoice_id"]), a))
                        sisa_t -= a
                    dapat = [(str(x["invoice_id"]), Decimal(str(x["amount_applied"]))) for x in r["invoices"]]
                    cek(dapat == harap, f"H3 {t} faktur {nama}: urutan + nominal == hitungan sendiri", f"{len(invs)} faktur")
                    cek(r["can_proceed"] and r["payload"] is not None, f"H3 {t} faktur {nama}: bisa disimpan", str(r["blocks"]))
                    if nama == "lebih":
                        cek("RP_OVERPAYMENT_DEPOSIT" in [n["code"] for n in r["notes"]], f"H3 {t} faktur lebih: catatan uang muka")
        else:
            print(f"H2/H3 {t}: faktur dilewati (tak ada pelanggan bersisa / rekening)")

    if MODE == "LAMA":
        json.dump(hasil, open(f"{OUT}/lama.json", "w"))
    await pool.close()
    print(f"SELESAI {MODE}: {len(gagal)} gagal")
    return 1 if gagal else 0


sys.exit(asyncio.run(main()))
