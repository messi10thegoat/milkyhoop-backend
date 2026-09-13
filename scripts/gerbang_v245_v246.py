"""GERBANG V245 — alokasi proporsional compute_ap/ar_outstanding. SATU transaksi luar, ROLLBACK.

Urutan (semua di dalam transaksi luar; tiap skenario savepoint sendiri; hasil di MEMORI Python):
  0 potret identitas: outstanding PER DOKUMEN seluruh tenant (AP & AR) + hc_verdict 8 + verify_ar_all
  1 MERAH lama: multi AP 2x1000 -> -2000 per tagihan · multi AR 2x1000 -> -2000 per faktur, cache +2000
  2 pasang badan V245 (di transaksi ini)
  3 IDENTITAS: per dokumen seluruh tenant SAMA PERSIS · hc_verdict 8 sama · verify_ar_all sama
  4 HIJAU: AP multi -1000/-1000, drift R8 AP diam, check_7 PASS_EXEMPT · AR multi -1000/-1000,
    cache +1000, verify_ar_reconciliation pelanggan itu PASS
  5 TAK SIMETRIS 1500+500 (AP & AR)
  6 G-A AP lebih bayar 3000 alok 2x1000 -> -1000/-1000
  7 G-C AP diskon · AP biaya bank · AR diskon -> -1000/-1000 dan Σ penurunan = sisi AP/AR GL
  8 G-B POST lalu VOID multi (AP & AR) -> outstanding kembali PERSIS
  9 G-A AR: TIDAK DIJALANKAN (label)
 10 SABOTASE: definisi lama dipasang ulang (badan ROLLBACK) -> multi AP kembali -2000 (sisi hijau HARUS memerah)
     — sabotase meng-assert NILAI (-2000), bukan sekadar "gagal" (pelajaran gerbang lebih bayar)
"""
import asyncio
import os
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402
from starlette.requests import Request  # noqa: E402

T = "kaos-biru-konveksi"
TENANTS = ["kaos-biru-konveksi", "grapgrap-manado"]
MAJU = "/tmp/V245_badan.sql"
MUNDUR = "/tmp/V245_rb_badan.sql"
MAJU2 = "/tmp/V246_badan.sql"      # hc_ap_members proporsional, independen
MUNDUR2 = "/tmp/V246_rb_badan.sql"  # hc_ap_members V242
HARAP = 28
hasil = []


def catat(s, u, ok, k=""):
    hasil.append((s, u, bool(ok), str(k)[:260]))


def baca_id(r):
    """SATU pembaca respons handler (dict ATAU model) — kelas yang dua kali menggigit hari ini."""
    for k in (r, getattr(r, "data", None), (r.get("data") if isinstance(r, dict) else None)):
        if isinstance(k, dict) and k.get("id"):
            return k["id"]
        if k is not None and getattr(k, "id", None):
            return k.id
    raise RuntimeError(f"ALAT: id tak terbaca dari {type(r).__name__}")


def req(uid):
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b"",
                    "state": {"user": {"tenant_id": T, "user_id": str(uid)}}})


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    import app.services.db_pool as dbp

    class FakePool:
        def acquire(self, *a, **k):
            class C:
                async def __aenter__(s):
                    return conn
                async def __aexit__(s, *e):
                    return False
            return C()
        async def release(self, c):
            return None

    async def fake(*a, **k):
        return FakePool()
    dbp.get_db_pool = fake
    from app.routers import bill_payments as bp, receive_payments as rp
    from app.schemas.bill_payments import CreateBillPaymentRequest as BP, BillAllocationInput as BA, VoidBillPaymentRequest as BV
    from app.schemas.receive_payments import CreateReceivePaymentRequest as RC, VoidPaymentRequest as RV
    RA = RC.model_fields["allocations"].annotation.__args__[0]

    uid = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)
    ba = await conn.fetchrow("SELECT id, coa_id FROM bank_accounts WHERE tenant_id=$1 AND is_active ORDER BY account_name LIMIT 1", T)
    je0 = await conn.fetchval("SELECT count(*) FROM journal_entries")

    async def potret():
        ap, ar = {}, {}
        for t in TENANTS:
            for r in await conn.fetch("SELECT bill_id, outstanding FROM compute_ap_outstanding($1)", t):
                ap[(t, r["bill_id"])] = r["outstanding"]
            for r in await conn.fetch("SELECT invoice_id, outstanding FROM compute_ar_outstanding($1)", t):
                ar[(t, r["invoice_id"])] = r["outstanding"]
        v = {}
        for t in TENANTS:
            for c in ("ap_invariant", "inventory_value", "status_desync", "bank_sync"):
                v[(t, c)] = await conn.fetchval("SELECT verdict FROM hc_verdict($1, $2)", c, t)
        va = {r["tenant_id"]: (r["total_drift"], r["verdict"]) for r in await conn.fetch("SELECT * FROM verify_ar_reconciliation_all()")}
        return ap, ar, v, va

    async def out_ap(b):
        return await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ap_outstanding($1) WHERE bill_id=$2", T, b)

    async def out_ar(i):
        return await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ar_outstanding($1) WHERE invoice_id=$2", T, i)

    async def r8_ap():
        gl = await conn.fetchval("""SELECT COALESCE(SUM(jl.credit-jl.debit),0) FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id
            JOIN chart_of_accounts coa ON coa.id=jl.account_id WHERE coa.account_type='PAYABLE' AND is_effective_journal(je.id) AND je.tenant_id=$1""", T)
        return gl - await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ap_outstanding($1)", T)

    async def bayar_ap(allocs, void=False, **extra):
        """allocs: [(bill, n)]. Kembalikan delta outstanding per tagihan + debit AP jurnal."""
        o0 = {b: await out_ap(b) for b, _ in allocs}
        body = BP(vendor_id=str(vend), payment_date=date.today(), bank_account_id=str(ba["id"]),
                  total_amount=extra.pop("total_amount", sum(n for _, n in allocs)),
                  allocations=[BA(bill_id=str(b), amount_applied=n) for b, n in allocs], save_as_draft=True, **extra)
        pid = baca_id(await bp.create_bill_payment(req(uid), body))
        await bp.post_bill_payment(req(uid), str(pid))
        jid = await conn.fetchval("SELECT journal_id FROM bill_payments_v2 WHERE id=$1::uuid", pid)
        gl = await conn.fetchval("""SELECT COALESCE(SUM(jl.debit),0) FROM journal_lines jl JOIN chart_of_accounts coa ON coa.id=jl.account_id
            WHERE jl.journal_id=$1 AND coa.account_type='PAYABLE'""", jid)
        if void:
            await bp.void_bill_payment(req(uid), str(pid), BV(void_reason="gerbang V245"))
        return {str(b)[:8]: await out_ap(b) - o0[b] for b, _ in allocs}, gl

    async def terima_ar(allocs, void=False, **extra):
        o0 = {i: await out_ar(i) for i, _ in allocs}
        p0 = {i: await conn.fetchval("SELECT amount_paid FROM sales_invoices WHERE id=$1", i) for i, _ in allocs}
        body = RC(customer_id=str(cust), payment_date=date.today(), payment_method="bank_transfer", bank_account_id=str(ba["id"]),
                  total_amount=extra.pop("total_amount", sum(n for _, n in allocs)),
                  allocations=[RA(invoice_id=str(i), amount_applied=n) for i, n in allocs], save_as_draft=False, **extra)
        pid = baca_id(await rp.create_receive_payment(req(uid), body))
        jid = await conn.fetchval("SELECT journal_id FROM receive_payments WHERE id=$1::uuid", pid)
        gl = await conn.fetchval("""SELECT COALESCE(SUM(jl.credit),0) FROM journal_lines jl JOIN chart_of_accounts coa ON coa.id=jl.account_id
            WHERE jl.journal_id=$1 AND coa.account_type='RECEIVABLE'""", jid)
        if void:
            await rp.void_receive_payment(req(uid), pid if not isinstance(pid, str) else __import__("uuid").UUID(pid), RV(void_reason="gerbang V245"))
        do = {str(i)[:8]: await out_ar(i) - o0[i] for i, _ in allocs}
        dp = {str(i)[:8]: (await conn.fetchval("SELECT amount_paid FROM sales_invoices WHERE id=$1", i) or 0) - (p0[i] or 0) for i, _ in allocs}
        return do, dp, gl

    async def sp_jalan(fn):
        sp = conn.transaction(); await sp.start()
        try:
            return await fn()
        finally:
            await sp.rollback()

    luar = conn.transaction(); await luar.start()
    try:
        vend = await conn.fetchval(
            """SELECT b.vendor_id FROM compute_ap_outstanding($1) a JOIN bills b ON b.id=a.bill_id
               WHERE a.bill_id IS NOT NULL AND a.outstanding >= 2000 GROUP BY b.vendor_id HAVING count(*) >= 2 LIMIT 1""", T)
        bills = [r["bill_id"] for r in await conn.fetch(
            """SELECT a.bill_id FROM compute_ap_outstanding($1) a JOIN bills b ON b.id=a.bill_id
               WHERE b.vendor_id=$2 AND a.outstanding >= 2000 ORDER BY a.bill_id LIMIT 2""", T, vend)]
        cust = await conn.fetchval(
            """SELECT s.customer_id FROM compute_ar_outstanding($1) a JOIN sales_invoices s ON s.id=a.invoice_id
               WHERE a.outstanding >= 2000 GROUP BY s.customer_id HAVING count(*) >= 2 LIMIT 1""", T)
        invs = [r["invoice_id"] for r in await conn.fetch(
            """SELECT a.invoice_id FROM compute_ar_outstanding($1) a JOIN sales_invoices s ON s.id=a.invoice_id
               WHERE s.customer_id=$2 AND a.outstanding >= 2000 ORDER BY a.invoice_id LIMIT 2""", T, cust)]
        catat("PRASYARAT", "subjek AP (vendor dgn 2 tagihan) & AR (pelanggan dgn 2 faktur)", len(bills) == 2 and len(invs) == 2, f"{bills} {invs}")

        # 0 potret
        ap0, ar0, v0, va0 = await potret()
        fp7_0 = await conn.fetchrow("SELECT member_count, members_sum, fingerprint, verdict FROM hc_verdict('ap_invariant', $1)", T)

        # 1 MERAH lama
        d, _ = await sp_jalan(lambda: bayar_ap([(bills[0], 1000), (bills[1], 1000)]))
        catat("1 MERAH lama", "AP multi 2x1000 -> -2000 per tagihan", all(x == -2000 for x in d.values()), d)
        do, dp, _ = await sp_jalan(lambda: terima_ar([(invs[0], 1000), (invs[1], 1000)]))
        catat("1 MERAH lama", "AR multi 2x1000 -> -2000 per faktur, cache +2000", all(x == -2000 for x in do.values()) and all(x == 2000 for x in dp.values()), (do, dp))

        # 2 pasang V245 + V246 (GABUNGAN, satu deploy)
        await conn.execute(open(MAJU, encoding="utf-8").read())
        await conn.execute(open(MAJU2, encoding="utf-8").read())
        # hc_ap_members SAH memanggil compute_ap_outstanding untuk sisi pembanding (itu yang diperiksa).
        # Independensi diukur pada RUMUS gl_pay: memuat penanda ekspresinya sendiri (a3.id) dan TIDAK
        # memuat konstruksi V245 (pd_bayar/pd_bagian/fungsi jendela).
        dfn = await conn.fetchval("SELECT pg_get_functiondef('hc_ap_members(text)'::regprocedure)")
        indep = ("a3.id" in dfn) and not any(x in dfn for x in ("pd_bayar", "pd_bagian", "OVER ("))
        catat("2 INDEPENDEN", "rumus gl_pay hc_ap_members = ekspresi sendiri, tanpa konstruksi V245", indep, indep)
        fp7_1 = await conn.fetchrow("SELECT member_count, members_sum, fingerprint, verdict FROM hc_verdict('ap_invariant', $1)", T)
        catat("3 IDENTITAS", "patok V242 ap_invariant SAMA (cacah, jumlah, sidik jari, verdict)",
              dict(fp7_0) == dict(fp7_1) and fp7_1["verdict"] == "PASS_EXEMPT", (dict(fp7_0), dict(fp7_1)))

        # 3 IDENTITAS
        ap1, ar1, v1, va1 = await potret()
        beda_ap = [k for k in set(ap0) | set(ap1) if ap0.get(k) != ap1.get(k)]
        beda_ar = [k for k in set(ar0) | set(ar1) if ar0.get(k) != ar1.get(k)]
        catat("3 IDENTITAS", f"AP per dokumen seluruh tenant sama ({len(ap0)} dok)", not beda_ap, beda_ap[:5])
        catat("3 IDENTITAS", f"AR per dokumen seluruh tenant sama ({len(ar0)} dok)", not beda_ar, beda_ar[:5])
        catat("3 IDENTITAS", "hc_verdict 8 sama", v0 == v1, {k: (v0[k], v1[k]) for k in v0 if v0[k] != v1[k]})
        catat("3 IDENTITAS", "verify_ar_reconciliation_all sama", va0 == va1, (va0, va1))

        # 4 HIJAU
        async def hijau_ap():
            r0 = await r8_ap()
            d, gl = await bayar_ap([(bills[0], 1000), (bills[1], 1000)])
            r1 = await r8_ap()
            v7 = await conn.fetchval("SELECT verdict FROM hc_verdict('ap_invariant', $1)", T)
            return d, gl, r0, r1, v7
        d, gl, r0, r1, v7 = await sp_jalan(hijau_ap)
        catat("4 HIJAU", "AP multi -> -1000/-1000", all(x == -1000 for x in d.values()), d)
        catat("4 HIJAU", "R8 AP drift diam & check_7 PASS_EXEMPT", r0 == r1 and v7 == "PASS_EXEMPT", (r0, r1, v7))

        async def hijau_ar():
            do, dp, gl = await terima_ar([(invs[0], 1000), (invs[1], 1000)])
            va = await conn.fetchval("SELECT status FROM verify_ar_reconciliation($1) WHERE customer_id=$2", T, str(cust))
            return do, dp, va
        do, dp, va = await sp_jalan(hijau_ar)
        catat("4 HIJAU", "AR multi -> -1000/-1000, cache +1000", all(x == -1000 for x in do.values()) and all(x == 1000 for x in dp.values()), (do, dp))
        catat("4 HIJAU", "verify_ar_reconciliation pelanggan itu PASS", va == "PASS", va)

        # 5 TAK SIMETRIS
        d, _ = await sp_jalan(lambda: bayar_ap([(bills[0], 1500), (bills[1], 500)]))
        catat("5 TAK SIMETRIS", "AP 1500+500 -> -1500/-500", sorted(d.values()) == [-1500, -500] and d[str(bills[0])[:8]] == -1500, d)
        do, dp, _ = await sp_jalan(lambda: terima_ar([(invs[0], 1500), (invs[1], 500)]))
        catat("5 TAK SIMETRIS", "AR 1500+500 -> -1500/-500", do[str(invs[0])[:8]] == -1500 and do[str(invs[1])[:8]] == -500, do)

        # 6 G-A AP
        d, gl = await sp_jalan(lambda: bayar_ap([(bills[0], 1000), (bills[1], 1000)], total_amount=3000))
        catat("6 G-A AP", "lebih bayar 3000 alok 2x1000 -> -1000/-1000; debit AP 2000", all(x == -1000 for x in d.values()) and gl == 2000, (d, gl))

        # 7 G-C
        d, gl = await sp_jalan(lambda: bayar_ap([(bills[0], 1000), (bills[1], 1000)], total_amount=1900, discount_amount=100))
        catat("7 G-C", "AP diskon 100 -> -1000/-1000 dan Σ = debit AP GL", all(x == -1000 for x in d.values()) and -sum(d.values()) == gl, (d, gl))
        d, gl = await sp_jalan(lambda: bayar_ap([(bills[0], 1000), (bills[1], 1000)], bank_fee_amount=50))
        catat("7 G-C", "AP biaya bank 50 -> -1000/-1000 dan Σ = debit AP GL", all(x == -1000 for x in d.values()) and -sum(d.values()) == gl, (d, gl))
        do, dp, gl = await sp_jalan(lambda: terima_ar([(invs[0], 1000), (invs[1], 1000)], total_amount=1900, discount_amount=100))
        catat("7 G-C", "AR diskon 100 -> -1000/-1000 dan Σ = kredit AR GL", all(x == -1000 for x in do.values()) and -sum(do.values()) == gl, (do, gl))

        # 8 G-B
        d, _ = await sp_jalan(lambda: bayar_ap([(bills[0], 1000), (bills[1], 1000)], void=True))
        catat("8 G-B", "AP multi POST lalu VOID -> outstanding kembali persis", all(x == 0 for x in d.values()), d)
        do, dp, _ = await sp_jalan(lambda: terima_ar([(invs[0], 1000), (invs[1], 1000)], void=True))
        catat("8 G-B", "AR multi POST lalu VOID -> outstanding kembali persis", all(x == 0 for x in do.values()), (do, dp))

        # 9 G-A AR
        catat("9 G-A AR", "TIDAK DIJALANKAN — jalur lebih bayar AR MATI di >=3 lapis (tipe pihak, kosakata metode, atribusi jurnal DP)", True,
              "label, bukan lulus; wajib diulang saat lebih bayar dihidupkan")

        # 10 SABOTASE
        async def sabotase():
            await conn.execute(open(MUNDUR, encoding="utf-8").read())
            d, _ = await bayar_ap([(bills[0], 1000), (bills[1], 1000)])
            return d
        d = await sp_jalan(sabotase)
        catat("10 SABOTASE", "definisi lama dipasang -> AP multi kembali -2000 (nilai, bukan sekadar gagal)", all(x == -2000 for x in d.values()), d)

        # 11 SABOTASE DUA ARAH untuk independensi pemeriksa (masing-masing terpisah)
        async def sab_fungsi_lama():
            await conn.execute(open(MUNDUR, encoding="utf-8").read())   # compute lama, pemeriksa BARU
            await bayar_ap([(bills[0], 1000), (bills[1], 1000)])
            return await conn.fetchval("SELECT verdict FROM hc_verdict('ap_invariant', $1)", T)
        v = await sp_jalan(sab_fungsi_lama)
        catat("11 SABOTASE-A", "compute_ap_outstanding LAMA + pemeriksa BARU, multi -> check_7 MERAH", v not in ("PASS", "PASS_EXEMPT"), v)

        async def sab_pemeriksa_lama():
            await conn.execute(open(MUNDUR2, encoding="utf-8").read())  # pemeriksa lama, compute BARU
            await bayar_ap([(bills[0], 1000), (bills[1], 1000)])
            return await conn.fetchval("SELECT verdict FROM hc_verdict('ap_invariant', $1)", T)
        v = await sp_jalan(sab_pemeriksa_lama)
        catat("11 SABOTASE-B", "hc_ap_members LAMA + compute BARU, multi -> check_7 MERAH", v not in ("PASS", "PASS_EXEMPT"), v)

        async def gabungan_tak_simetris():
            await bayar_ap([(bills[0], 1500), (bills[1], 500)])
            return await conn.fetchval("SELECT verdict FROM hc_verdict('ap_invariant', $1)", T)
        v = await sp_jalan(gabungan_tak_simetris)
        catat("12 GABUNGAN", "multi tak simetris 1500+500 -> check_7 PASS_EXEMPT", v == "PASS_EXEMPT", v)
    finally:
        await luar.rollback()

    je1 = await conn.fetchval("SELECT count(*) FROM journal_entries")
    catat("KONTROL", "nol baris menetap", je0 == je1, f"{je0}->{je1}")
    ada = await conn.fetchval("SELECT position('pd_bayar' in pg_get_functiondef(p.oid)) > 0 FROM pg_proc p WHERE proname='compute_ap_outstanding'")
    catat("KONTROL", "fungsi hidup tak berubah oleh gerbang (fase uji kering: tanpa pd_bayar)", ada is False or os.environ.get("FASE") == "hidup", ada)
    await conn.close()
    for s, u, ok, k in hasil:
        print(("[H] " if ok else "[X] ") + f"{s:14} {u}  | {k}")
    g = sum(1 for h in hasil if not h[2])
    print(f"\ngagal={g} total={len(hasil)} harap={HARAP} -> {'LENGKAP' if len(hasil) == HARAP else 'TAK SAH'}")
    sys.exit(0 if g == 0 and len(hasil) == HARAP else 1)


asyncio.run(main())
