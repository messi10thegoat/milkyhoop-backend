"""UJI NYATA Pembelian P-U1 (22 Okt 2026): BACA-SAJA. Mode: baru [out.json]
 A. akun PERAN per tenant: AP_PREPAID != VAT_INPUT (bug G2: kode '1-10800' = PPN Masukan), AP_PREPAID ASSET, AP_TRADE PAYABLE, CASH_GENERAL ada.
 B. statis: vendor_deposits.py tanpa kode akun hardcode / seed / resolve_account_id.
 C. SQL sisa jurnal (services.uang_muka_vendor.SQL_SISA_JURNAL) pada data SINTETIS (CTE yang menutupi journal_entries/journal_lines; data nyata = 0 deposit):
    deposit +1000, apply -300, refund -200 => 500; jurnal DIBALIK (void) / DRAFT / tenant lain / deposit lain / akun lain TIDAK ikut. SQL == acuan Python.
 D. nyata: untuk semua vendor_deposits (kini 0) sisa jurnal == amount - applied - refunded."""
import asyncio, os, re, sys, uuid
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
from decimal import Decimal
import asyncpg
from app.services import uang_muka_vendor as UMV

gagal = []


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:200]}"))
    if not ok: gagal.append(nama)


def U(): return str(uuid.uuid4())


def vals(nama, kolom, baris):
    if not baris:
        return f"{nama} AS (SELECT {', '.join(f'NULL::{t} AS {c}' for c, t in kolom)} WHERE false)"
    def lit(v, t):
        if v is None: return f"NULL::{t}"
        return f"'{v}'::{t}" if t in ("uuid", "text") else f"{v}::{t}"
    rows = ", ".join("(" + ", ".join(lit(r[c], t) for c, t in kolom) + ")" for r in baris)
    return f"{nama} AS (SELECT * FROM (VALUES {rows}) v({', '.join(c for c, _ in kolom)}))"


async def sintetis(c, sql):
    T, T2 = "tenant-uji", "tenant-lain"
    PRE, LAIN = U(), U()
    D1, D2 = U(), U()
    je, jl = [], []
    def j(src_t, src_id, tenant=T, status="POSTED", rev_by=None, rev_of=None, baris=()):
        i = U(); je.append(dict(id=i, tenant_id=tenant, source_id=src_id, source_type=src_t, status=status, reversed_by_id=rev_by, reversal_of_id=rev_of))
        for acc, d, k in baris: jl.append(dict(journal_id=i, account_id=acc, debit=d, credit=k))
        return i
    j("VENDOR_DEPOSIT", D1, baris=[(PRE, 1000, 0), (LAIN, 0, 1000)])                    # +1000
    j("DEPOSIT_APPLICATION", D1, baris=[(LAIN, 300, 0), (PRE, 0, 300)])                 # -300
    j("DEPOSIT_REFUND", D1, baris=[(LAIN, 200, 0), (PRE, 0, 200)])                      # -200
    j("DEPOSIT_APPLICATION", D1, rev_by=U(), baris=[(LAIN, 90, 0), (PRE, 0, 90)])       # DIBALIK -> tak ikut
    j("DEPOSIT_REFUND", D1, status="DRAFT", baris=[(LAIN, 70, 0), (PRE, 0, 70)])        # DRAFT -> tak ikut
    j("DEPOSIT_APPLICATION", D1, rev_of=U(), baris=[(PRE, 60, 0), (LAIN, 0, 60)])       # jurnal pembalik -> tak ikut
    j("DEPOSIT_APPLICATION", D1, tenant=T2, baris=[(LAIN, 50, 0), (PRE, 0, 50)])        # tenant lain
    j("VENDOR_DEPOSIT", D2, baris=[(PRE, 5000, 0), (LAIN, 0, 5000)])                    # deposit lain
    j("BILL", D1, baris=[(PRE, 40, 0), (LAIN, 0, 40)])                                  # source_type lain
    def acuan(dep):
        tot = Decimal(0)
        for e in je:
            if e["tenant_id"] != T or e["source_id"] != dep or e["source_type"] not in UMV.SUMBER_JURNAL: continue
            if e["status"] != "POSTED" or e["reversed_by_id"] or e["reversal_of_id"]: continue
            for l in jl:
                if l["journal_id"] == e["id"] and l["account_id"] == PRE: tot += Decimal(l["debit"]) - Decimal(l["credit"])
        return tot
    cte = "WITH " + ", ".join([
        vals("journal_entries", [("id", "uuid"), ("tenant_id", "text"), ("source_id", "uuid"), ("source_type", "text"), ("status", "text"), ("reversed_by_id", "uuid"), ("reversal_of_id", "uuid")], je),
        vals("journal_lines", [("journal_id", "uuid"), ("account_id", "uuid"), ("debit", "numeric"), ("credit", "numeric")], jl)]) + " "
    for dep, harap in ((D1, 500), (D2, 5000)):
        v = Decimal(str(await c.fetchval(cte + sql, T, uuid.UUID(dep), list(UMV.SUMBER_JURNAL), uuid.UUID(PRE))))
        cek(f"sintetis: sisa jurnal deposit {'D1' if dep == D1 else 'D2'} == {harap} (== acuan Python)", v == harap == acuan(dep), (v, harap, acuan(dep)))


async def main():
    c = await asyncpg.connect(os.environ["URL"]); await c.execute("SET default_transaction_read_only = on")
    # A
    for r in await c.fetch("""SELECT t.id tenant,
         (SELECT coa.account_code||'|'||coa.account_type FROM account_roles a JOIN chart_of_accounts coa ON coa.id=a.account_id WHERE a.tenant_id=t.id AND a.role_key='AP_PREPAID') prepaid,
         (SELECT coa.account_code FROM account_roles a JOIN chart_of_accounts coa ON coa.id=a.account_id WHERE a.tenant_id=t.id AND a.role_key='VAT_INPUT') vat,
         (SELECT coa.account_type FROM account_roles a JOIN chart_of_accounts coa ON coa.id=a.account_id WHERE a.tenant_id=t.id AND a.role_key='AP_TRADE') ap,
         (SELECT 1 FROM account_roles a WHERE a.tenant_id=t.id AND a.role_key='CASH_GENERAL') kas
       FROM "Tenant" t ORDER BY 1"""):
        kode = (r["prepaid"] or "").split("|")[0]
        cek(f"{r['tenant']}: AP_PREPAID ({r['prepaid']}) != VAT_INPUT ({r['vat']}), ASSET; AP_TRADE={r['ap']}; kas={'ada' if r['kas'] else 'TIADA'}",
            bool(r["prepaid"]) and kode != r["vat"] and r["prepaid"].endswith("ASSET") and r["ap"] == "PAYABLE" and bool(r["kas"]))
    # B
    src = open("/app/backend/api_gateway/app/routers/vendor_deposits.py").read()
    cek("statis: vendor_deposits.py tanpa kode akun hardcode ('1-10800'/'2-10100'/'1-10100')", not re.search(r"[\"'](1-10800|2-10100|1-10100)[\"']", src))
    cek("statis: tanpa seed_vendor_deposit_account / resolve_account_id", "seed_vendor_deposit_account" not in src and "resolve_account_id" not in src)
    # C
    await sintetis(c, UMV.SQL_SISA_JURNAL)
    # D
    n = 0
    for d in await c.fetch("SELECT id, tenant_id, amount FROM vendor_deposits WHERE status IN ('posted','partial')"):
        n += 1
        ap = await c.fetchval("SELECT COALESCE(SUM(amount),0) FROM vendor_deposit_applications WHERE vendor_deposit_id=$1", d["id"])
        rf = await c.fetchval("SELECT COALESCE(SUM(amount),0) FROM vendor_deposit_refunds WHERE vendor_deposit_id=$1", d["id"])
        sisa = await UMV.sisa_jurnal(c, d["tenant_id"], d["id"])
        cek(f"nyata: deposit {d['id']} sisa jurnal == amount-applied-refund", sisa == Decimal(d["amount"]) - Decimal(ap) - Decimal(rf), (sisa, d["amount"], ap, rf))
    print(f"INFO deposit posted/partial nyata = {n} (0 = pembuktian andalkan sintetis)")
    print("RINGKAS PEMBELIAN-P0 gagal=%d %s" % (len(gagal), gagal[:4]))
    await c.close(); sys.exit(1 if gagal else 0)

asyncio.run(main())
