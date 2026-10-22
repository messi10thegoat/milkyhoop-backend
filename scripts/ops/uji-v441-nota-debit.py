"""Gerbang V441 atribusi nota debit (PEMBELIAN-SERAGAM unit (3a), 22 Okt 2026). argv: badan <V441.sql> | fungsi
  badan  : badan compute_ap_outstanding (AS $function$ ... $function$) dan compute_ap_rincian (BADAN-MULAI/SELESAI) dari berkas migrasi
           dijalankan sebagai KUERI (p_tenant_id -> $1) -- TANPA DDL di produksi.
  fungsi : sesudah migrasi, memanggil fungsi terpasang (harapan sama seperti 'badan').
  (1) SEMUA tenant: AP baru == compute_ap_outstanding terpasang (baris identik; hari ini 0 nota debit) [hanya mode badan]
  (2) SEMUA tenant: rincian baru == AP baru per tagihan (pola gerbang V440)
  (3) Skenario sintetis di kaos, transaksi SELALU ROLLBACK (nota debit + jurnal DRAFT->POSTED lewat trigger nyata), delta terhadap baseline:
      S1 nota debit bebas 10.000           -> baris vendor −10.000 (baru == lama)
      S2 nota debit tertaut tagihan B       -> sisa B −10.000, baris vendor 0 ; rincian B vendor_credit 10.000  (lama: B tetap, vendor −10.000)
      S3 nota debit bebas + refund 10.000   -> baris vendor 0                                                   (lama: −10.000)
      S4 nota debit bebas lalu void (cermin) -> 0                                                               (lama: 0)
Kontrol merah: gate_v441_nota_debit.sh --kontrol-merah disjoint|refund (badan disabotase) -> WAJIB gagal."""
import asyncio, os, re, sys, uuid
from decimal import Decimal
sys.path.insert(0, "/app"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg

MODE = sys.argv[1]
gagal = 0
T = "kaos-biru-konveksi"
N = Decimal("10000")


def cek(ok, l, i=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True); gagal += 0 if ok else 1


def kueri():
    if MODE != "badan":
        return "SELECT * FROM compute_ap_outstanding($1)", "SELECT * FROM compute_ap_rincian($1)"
    teks = open(sys.argv[2]).read()
    a = teks.index("AS $function$") + len("AS $function$"); b = teks.index("$function$", a)
    ap = re.sub(r"\bp_tenant_id\b", "$1::text", teks[a:b])
    rb = teks[teks.index("-- BADAN-MULAI"):teks.index("-- BADAN-SELESAI")]
    rin = re.sub(r"\bp_tenant_id\b", "$1::text", rb)
    rin = f"SELECT * FROM ({rin}) q(bill_id, bill_total, payment_amount, vendor_credit_amount, deposit_applied_amount, paid_amount, outstanding)"
    return ap, rin


async def jurnal(c, src_type, src_id, baris, reversal_of=None):
    jid = uuid.uuid4()
    tot = sum(d for _, d, _ in baris)
    tgl = await c.fetchval("SELECT tanggal_bisnis($1)", T)
    await c.execute("""INSERT INTO journal_entries (id, tenant_id, journal_number, journal_date, description, source_type, source_id, status,
                       total_debit, total_credit, reversal_of_id) VALUES ($1,$2,$3,$4,$5,$6,$7,'DRAFT',$8,$8,$9)""",
                    jid, T, f"TES-V441-{str(jid)[:8]}", tgl, "TES E2E V441 (ROLLBACK)", src_type, src_id, tot, reversal_of)
    for n, (akun, d, k) in enumerate(baris, 1):
        await c.execute("INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo) VALUES ($1,$2,$3,$4,$5,$6,'TES')",
                        uuid.uuid4(), jid, n, akun, d, k)
    await c.execute("UPDATE journal_entries SET status='POSTED' WHERE id=$1", jid)
    return jid


async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    q_ap, q_rin = kueri()
    try:
        # (1)+(2) semua tenant, baca saja
        tx = c.transaction(readonly=True); await tx.start()
        try:
            tenants = [r["id"] for r in await c.fetch('SELECT id FROM "Tenant" ORDER BY id')]
            beda1, beda2, n_b = [], [], 0
            for t in tenants:
                baru = sorted(tuple(map(str, r.values())) for r in await c.fetch(q_ap, t))
                if MODE == "badan":
                    lama = sorted(tuple(map(str, r.values())) for r in await c.fetch("SELECT * FROM compute_ap_outstanding($1)", t))
                    if baru != lama: beda1.append((t, len(baru), len(lama)))
                apb = {r["bill_id"]: r for r in await c.fetch(q_ap, t) if r["bill_id"]}
                for r in await c.fetch(q_rin, t):
                    n_b += 1
                    a = apb.get(r["bill_id"])
                    if r["outstanding"] != (a["outstanding"] if a else Decimal(0)) or r["paid_amount"] != r["payment_amount"] + r["vendor_credit_amount"] + r["deposit_applied_amount"]:
                        beda2.append((t, str(r["bill_id"])[:8]))
            if MODE == "badan":
                cek(not beda1, f"[{MODE}] AP baru == compute_ap_outstanding terpasang ({len(tenants)} tenant)", str(beda1[:3]))
            cek(not beda2, f"[{MODE}] rincian == AP per tagihan ({n_b} tagihan)", str(beda2[:3]))
        finally:
            await tx.rollback()
        # (3) skenario sintetis kaos, ROLLBACK
        b = await c.fetchrow("""SELECT bill_id, vendor_id, vendor_name, outstanding FROM compute_ap_outstanding($1)
                                WHERE bill_id IS NOT NULL AND outstanding > $2 AND vendor_id IS NOT NULL ORDER BY outstanding DESC LIMIT 1""", T, N)
        akun = lambda role: c.fetchval("SELECT account_id FROM account_roles WHERE tenant_id=$1 AND role_key=$2", T, role)
        ap_id, retur_id = await akun("AP_TRADE"), await akun("COGS_PURCHASE_RETURN")
        kas_id = await c.fetchval("SELECT id FROM chart_of_accounts WHERE tenant_id=$1 AND account_type='ASSET' AND account_code LIKE '1-101%' ORDER BY account_code LIMIT 1", T)
        uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId"=$1 ORDER BY "createdAt" LIMIT 1', T)

        async def ambil(q):
            rows = await c.fetch(q, T)
            vend = sum((r["outstanding"] for r in rows if r["bill_id"] is None and r["vendor_id"] == b["vendor_id"]), Decimal(0))
            bill = next((r["outstanding"] for r in rows if r["bill_id"] == b["bill_id"]), Decimal(0))
            return vend, bill

        async def skenario(nama, tertaut, refund, void, harap_baru, harap_lama):
            tx = c.transaction(); await tx.start()
            try:
                v0b, b0b = await ambil(q_ap); v0l, b0l = await ambil("SELECT * FROM compute_ap_outstanding($1)")
                vc = uuid.uuid4()
                await c.execute("""INSERT INTO vendor_credits (id, tenant_id, credit_number, vendor_id, vendor_name, subtotal, total_amount, status,
                                   credit_date, reason, created_by, original_bill_id) VALUES ($1,$2,$3,$4,$5,$6,$6,'posted',tanggal_bisnis($2),'return',$7,$8)""",
                                vc, T, f"TES-V441-{str(vc)[:6]}", b["vendor_id"], b["vendor_name"], N, uid, b["bill_id"] if tertaut else None)
                j = await jurnal(c, "VENDOR_CREDIT", vc, [(ap_id, N, 0), (retur_id, 0, N)])
                if refund:
                    await jurnal(c, "VENDOR_CREDIT_REFUND", vc, [(kas_id, N, 0), (ap_id, 0, N)])
                if void:
                    r = await jurnal(c, "VENDOR_CREDIT", vc, [(retur_id, N, 0), (ap_id, 0, N)], reversal_of=j)
                    await c.execute("UPDATE journal_entries SET reversed_by_id=$1, reversed_at=NOW() WHERE id=$2", r, j)
                v1b, b1b = await ambil(q_ap); v1l, b1l = await ambil("SELECT * FROM compute_ap_outstanding($1)")
                rin = next((r for r in await c.fetch(q_rin, T) if r["bill_id"] == b["bill_id"]), None)
                db, dl = (v1b - v0b, b1b - b0b), (v1l - v0l, b1l - b0l)
                ok = db == harap_baru and (MODE != "badan" or dl == harap_lama)
                if tertaut:
                    ok = ok and rin is not None and rin["vendor_credit_amount"] == N and rin["outstanding"] == b1b
                cek(ok, f"[{MODE}] {nama}: Δ(vendor, tagihan) baru {db} harap {harap_baru}" + (f"; lama {dl} harap {harap_lama}" if MODE == "badan" else ""))
            finally:
                await tx.rollback()

        await skenario("S1 bebas", False, False, False, (-N, 0), (-N, 0))
        await skenario("S2 tertaut tagihan", True, False, False, (0, -N), (-N, 0))
        await skenario("S3 bebas + refund", False, True, False, (0, 0), (-N, 0))
        await skenario("S4 bebas lalu void", False, False, True, (0, 0), (0, 0))
    finally:
        await c.close()
        print("RINGKAS gagal=%d" % gagal)


asyncio.run(main())
