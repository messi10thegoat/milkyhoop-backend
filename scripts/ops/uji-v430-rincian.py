"""Gerbang V430 compute_ar_rincian (S1-U3, 20 Okt 2026). BACA saja (transaksi READ ONLY, ROLLBACK), SEMUA tenant.
argv: badan <V430.sql>   -- sebelum migrasi: badan fungsi (teks di antara BADAN-MULAI/SELESAI berkas migrasi) dijalankan
                            sebagai kueri, p_tenant_id -> $1
      fungsi            -- sesudah migrasi: memanggil compute_ar_rincian($1) yang terpasang
      detail            -- sesudah migrasi + kode: payment_summary GET /sales-invoices/{id} == amount_paid/amount_due detail,
                            untuk SEMUA faktur grapgrap + kaos
Penegasan (badan/fungsi), untuk SETIAP faktur terbit SETIAP tenant:
  outstanding rincian == COALESCE(compute_ar_outstanding.outstanding, 0); invoice_total & paid_amount sama bila ada baris AR;
  paid == receipt + dp + cn; setiap baris AR ber-faktur ada di rincian."""
import asyncio, inspect, json, os, re, sys
from decimal import Decimal
sys.path.insert(0, "/app"); os.environ.setdefault("OPENAI_API_KEY", "x")

gagal = 0


def cek(ok, l, i=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else "")); gagal += 0 if ok else 1


import asyncpg

MODE = sys.argv[1]


async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    tx = c.transaction(readonly=True); await tx.start()
    try:
        if MODE == "detail":
            await detail(c)
            return
        if MODE == "badan":
            teks = open(sys.argv[2]).read()
            badan = teks[teks.index("-- BADAN-MULAI"):teks.index("-- BADAN-SELESAI")]
            sql = re.sub(r"\bp_tenant_id\b", "$1::text", badan)
            sql = f"SELECT * FROM ({sql}) q(invoice_id, invoice_total, receipt_amount, dp_applied_amount, " \
                  f"credit_note_amount, paid_amount, outstanding)"
        else:
            sql = "SELECT * FROM compute_ar_rincian($1)"
        tenants = [r["id"] for r in await c.fetch('SELECT id FROM "Tenant" ORDER BY id')]
        n_f = n_t = 0
        beda, hilang, rumus = [], [], []
        hit = {"receipt": 0, "dp": 0, "cn": 0, "unapply_negatif": 0}
        for t in tenants:
            rin = {r["invoice_id"]: r for r in await c.fetch(sql, t)}
            ar = {r["invoice_id"]: r for r in await c.fetch(
                "SELECT invoice_id, invoice_total, paid_amount, outstanding FROM compute_ar_outstanding($1) "
                "WHERE invoice_id IS NOT NULL", t)}
            aktif = {r["id"] for r in await c.fetch(
                "SELECT id FROM sales_invoices WHERE tenant_id = $1 AND status NOT IN ('draft', 'void')", t)}
            if aktif: n_t += 1
            n_f += len(aktif)
            if set(rin) != aktif:
                beda.append((t, "himpunan faktur", len(rin), len(aktif)))
            for i, r in rin.items():
                a = ar.get(i)
                if r["outstanding"] != (a["outstanding"] if a else Decimal("0")):
                    beda.append((t, str(i)[:8], "outstanding", str(r["outstanding"]), str(a["outstanding"]) if a else "0"))
                if a and (r["invoice_total"] != a["invoice_total"] or r["paid_amount"] != a["paid_amount"]):
                    beda.append((t, str(i)[:8], "total/paid", str(r["invoice_total"]), str(a["invoice_total"]),
                                 str(r["paid_amount"]), str(a["paid_amount"])))
                if r["paid_amount"] != r["receipt_amount"] + r["dp_applied_amount"] + r["credit_note_amount"]:
                    rumus.append((t, str(i)[:8]))
                hit["receipt"] += r["receipt_amount"] != 0
                hit["dp"] += r["dp_applied_amount"] != 0
                hit["cn"] += r["credit_note_amount"] != 0
                hit["unapply_negatif"] += r["receipt_amount"] < 0
            hilang += [(t, str(i)[:8]) for i in ar if i not in rin]
        cek(not beda, f"[{MODE}] Σ rincian: outstanding == total − compute_ar_outstanding ({n_f} faktur, {n_t} tenant ber-faktur, "
                      f"{len(tenants)} tenant)", str(beda[:3]))
        cek(not hilang, f"[{MODE}] setiap baris AR ber-faktur ada di rincian", str(hilang[:3]))
        cek(not rumus, f"[{MODE}] paid == receipt + dp + cn", str(rumus[:3]))
        print("  faktur ber-sumber", json.dumps(hit))
    finally:
        await tx.rollback(); await c.close()
        print("RINGKAS gagal=%d" % gagal)


async def detail(c):
    from types import SimpleNamespace
    from backend.api_gateway.app.routers import sales_invoices as SI
    class Pool:
        def acquire(self_):
            class A:
                async def __aenter__(s): return c
                async def __aexit__(s, *a): return False
            return A()
    async def gp(): return Pool()
    SI.get_pool = gp
    for t in ("grapgrap-manado", "kaos-biru-konveksi"):
        uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId"=$1 ORDER BY "createdAt" LIMIT 1', t)
        req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": t, "user_id": str(uid)}), headers={})
        ids = [r["id"] for r in await c.fetch("SELECT id FROM sales_invoices WHERE tenant_id=$1", t)]
        beda = []
        for i in ids:
            d = (await SI.get_invoice(req, i))["data"]
            ps = d["payment_summary"]
            ok = (Decimal(str(ps["paid_amount"])) == Decimal(str(d["amount_paid"]))
                  and Decimal(str(ps["remaining_amount"])) == Decimal(str(d["amount_due"]))
                  and Decimal(str(ps["paid_amount"])) == sum(Decimal(str(ps[k])) for k in
                                                             ("receipt_amount", "dp_applied_amount", "credit_note_amount")))
            if not ok:
                beda.append((d["invoice_number"], d["status"], ps, d["amount_paid"], d["amount_due"]))
        cek(not beda, f"[detail] {t}: payment_summary == amount_paid/amount_due detail ({len(ids)} faktur)", str(beda[:2]))


asyncio.run(main())
