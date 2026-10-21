"""Gerbang V440 compute_ap_rincian (PEMBELIAN-SERAGAM unit (1), 22 Okt 2026). BACA saja (transaksi READ ONLY, ROLLBACK), SEMUA tenant.
argv: badan <V440.sql>   -- sebelum migrasi: badan fungsi (teks di antara BADAN-MULAI/SELESAI berkas migrasi) dijalankan sebagai kueri,
                            p_tenant_id -> $1
      fungsi            -- sesudah migrasi: memanggil compute_ap_rincian($1) yang terpasang
Penegasan, untuk SETIAP tagihan aktif (status_v2 bukan draft/void) SETIAP tenant:
  himpunan tagihan rincian == tagihan aktif; outstanding rincian == COALESCE(compute_ap_outstanding.outstanding, 0);
  bill_total & paid_amount sama bila ada baris AP; paid == payment + vendor_credit + deposit; setiap baris AP ber-tagihan ada di rincian."""
import asyncio, json, os, re, sys
from decimal import Decimal
sys.path.insert(0, "/app"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg

gagal = 0
MODE = sys.argv[1]


def cek(ok, l, i=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else "")); gagal += 0 if ok else 1


async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    tx = c.transaction(readonly=True); await tx.start()
    try:
        if MODE == "badan":
            teks = open(sys.argv[2]).read()
            badan = teks[teks.index("-- BADAN-MULAI"):teks.index("-- BADAN-SELESAI")]
            sql = re.sub(r"\bp_tenant_id\b", "$1::text", badan)
            sql = (f"SELECT * FROM ({sql}) q(bill_id, bill_total, payment_amount, vendor_credit_amount, deposit_applied_amount, "
                   f"paid_amount, outstanding)")
        else:
            sql = "SELECT * FROM compute_ap_rincian($1)"
        tenants = [r["id"] for r in await c.fetch('SELECT id FROM "Tenant" ORDER BY id')]
        n_b = n_t = 0
        beda, hilang, rumus = [], [], []
        hit = {"payment": 0, "vendor_credit": 0, "deposit": 0, "lunas_0": 0, "total_0_jurnal_dibalik": 0}
        for t in tenants:
            rin = {r["bill_id"]: r for r in await c.fetch(sql, t)}
            ap = {r["bill_id"]: r for r in await c.fetch(
                "SELECT bill_id, bill_total, paid_amount, outstanding FROM compute_ap_outstanding($1) WHERE bill_id IS NOT NULL", t)}
            aktif = {r["id"] for r in await c.fetch(
                "SELECT id FROM bills WHERE tenant_id = $1 AND status_v2 NOT IN ('draft', 'void')", t)}
            if aktif: n_t += 1
            n_b += len(aktif)
            if set(rin) != aktif:
                beda.append((t, "himpunan tagihan", len(rin), len(aktif)))
            for i, r in rin.items():
                a = ap.get(i)
                if r["outstanding"] != (a["outstanding"] if a else Decimal("0")):
                    beda.append((t, str(i)[:8], "outstanding", str(r["outstanding"]), str(a["outstanding"]) if a else "0"))
                if a and (r["bill_total"] != a["bill_total"] or r["paid_amount"] != a["paid_amount"]):
                    beda.append((t, str(i)[:8], "total/paid", str(r["bill_total"]), str(a["bill_total"]), str(r["paid_amount"]), str(a["paid_amount"])))
                if r["paid_amount"] != r["payment_amount"] + r["vendor_credit_amount"] + r["deposit_applied_amount"]:
                    rumus.append((t, str(i)[:8]))
                hit["payment"] += r["payment_amount"] != 0
                hit["vendor_credit"] += r["vendor_credit_amount"] != 0
                hit["deposit"] += r["deposit_applied_amount"] != 0
                hit["lunas_0"] += r["outstanding"] == 0 and r["bill_total"] != 0
                hit["total_0_jurnal_dibalik"] += r["bill_total"] == 0
            hilang += [(t, str(i)[:8]) for i in ap if i not in rin]
        if MODE == "badan":
            # cabang tanpa data hari ini (proporsional multi-tagihan, nota debit, uang muka vendor): data tak bisa menangkap salinan rusak ->
            # fragmen kunci WAJIB identik (spasi dinormalkan) dengan definisi compute_ap_outstanding yang terpasang.
            n = lambda x: re.sub(r"\s+", " ", x).strip()
            ap_def = n(await c.fetchval("SELECT pg_get_functiondef('public.compute_ap_outstanding(text)'::regprocedure)"))
            mig = n(badan)
            FRAG = ["THEN ROUND(pb.ap_debit * bpa.amount_applied / SUM(bpa.amount_applied) OVER (PARTITION BY bpa.payment_id), 2)",
                    "ROW_NUMBER() OVER (PARTITION BY bpa.payment_id ORDER BY bpa.id DESC) AS urut_akhir",
                    "THEN x.ap_debit - x.jumlah_bagian ELSE 0 END), 0) AS total_debit",
                    "FROM vendor_credit_applications vca JOIN journal_entries je ON je.id = vca.journal_id",
                    "FROM vendor_deposit_applications vda JOIN journal_entries je ON je.id = vda.journal_id AND je.source_type = 'DEPOSIT_APPLICATION'",
                    "AND b.status_v2 NOT IN ('draft', 'void')",
                    "AND je.status = 'POSTED' AND je.reversed_by_id IS NULL AND coa.account_type = 'PAYABLE' AND jl.credit > 0 AND je.source_type = 'BILL'"]
            salah = [f[:60] for f in FRAG if f not in ap_def or f not in mig]
            cek(not salah, f"[badan] {len(FRAG)} fragmen cabang identik dgn compute_ap_outstanding terpasang", str(salah))
        cek(not beda, f"[{MODE}] rincian == compute_ap_outstanding ({n_b} tagihan aktif, {n_t} tenant bertagihan, {len(tenants)} tenant)",
            str(beda[:3]))
        cek(not hilang, f"[{MODE}] setiap baris AP ber-tagihan ada di rincian", str(hilang[:3]))
        cek(not rumus, f"[{MODE}] paid == payment + vendor_credit + deposit", str(rumus[:3]))
        print("  tagihan ber-sumber", json.dumps(hit))
    finally:
        await tx.rollback(); await c.close()
        print("RINGKAS gagal=%d" % gagal)


asyncio.run(main())
