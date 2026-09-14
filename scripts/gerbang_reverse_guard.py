"""GERBANG Gate 5 pembalikan generik. Modul journals.py SALINAN dimuat; V254 badan opsional dipasang DI DALAM transaksi
luar (ROLLBACK di akhir -> trigger tak menetap). Savepoint per skenario.
argv: <path journals.py> <mode: baru|lama|hidup|sabotase> [--v254]
"""
import asyncio
import importlib.util
import os
import sys
import uuid as U
from datetime import date

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402
from starlette.requests import Request  # noqa: E402

PATH, MODE = sys.argv[1], sys.argv[2]
V254 = "--v254" in sys.argv
T = "kaos-biru-konveksi"
hasil = []


def catat(s, u, ok, k=""):
    hasil.append((s, u, bool(ok), str(k)[:170]))


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
        async def fetchrow(self, *a, **k):
            return await conn.fetchrow(*a, **k)
        async def fetch(self, *a, **k):
            return await conn.fetch(*a, **k)
        async def fetchval(self, *a, **k):
            return await conn.fetchval(*a, **k)
        async def execute(self, *a, **k):
            return await conn.execute(*a, **k)

    async def fake(*a, **k):
        return FakePool()
    dbp.get_db_pool = fake
    src = open(PATH, encoding="utf-8").read()
    if MODE == "sabotase":
        A = 'if journal["source_type"] not in REVERSAL_ALLOWLIST:'
        if src.count(A) != 1:
            print("ALAT: jangkar sabotase"); sys.exit(3)
        src = src.replace(A, 'if False and journal["source_type"] not in REVERSAL_ALLOWLIST:')
        open("/tmp/journals_g.py", "w", encoding="utf-8").write(src)
        loadpath = "/tmp/journals_g.py"
    else:
        loadpath = PATH
    import app.routers  # noqa: F401
    spec = importlib.util.spec_from_file_location("app.routers.journals_g", loadpath)
    m = importlib.util.module_from_spec(spec); sys.modules[spec.name] = m; spec.loader.exec_module(m)
    from app.schemas.journals import ReverseJournalRequest

    uid = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)

    def rq():
        return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b"",
                        "state": {"user": {"tenant_id": T, "user_id": str(uid)}}})

    async def reverse(jid):
        try:
            await m.reverse_journal(rq(), jid, ReverseJournalRequest(reversal_date=date.today(), reason="gerbang gate5"))
            return 200, ""
        except Exception as e:  # noqa: BLE001
            return getattr(e, "status_code", type(e).__name__), str(getattr(e, "detail", e))

    async def buat_jurnal(source_type, akun_pair):
        jid = U.uuid4()
        await conn.execute("""INSERT INTO journal_entries (id, tenant_id, journal_number, journal_date, description,
            source_type, source_id, total_debit, total_credit, status, created_by)
            VALUES ($1,$2,$3,CURRENT_DATE,'gerbang g5',$4,$5,1000,1000,'DRAFT',$6)""",
            jid, T, f"GERBANG-G5-{str(jid)[:8]}", source_type, U.uuid4(), uid)
        await conn.execute("INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo) VALUES ($1,$2,1,$3,1000,0,'g'),($4,$2,2,$5,0,1000,'g')",
                           U.uuid4(), jid, akun_pair[0], U.uuid4(), akun_pair[1])
        await conn.execute("UPDATE journal_entries SET status='POSTED' WHERE id=$1", jid)
        return jid

    luar = conn.transaction(); await luar.start()
    try:
        if V254:
            await conn.execute(open("/tmp/V254_badan.sql", encoding="utf-8").read())
            catat("SKEMA", "trigger trg_prevent_reverse_of_reversal ada",
                  await conn.fetchval("SELECT count(*) FROM pg_trigger WHERE tgname='trg_prevent_reverse_of_reversal'") == 1)

        aman = [r["id"] for r in await conn.fetch("""SELECT id FROM chart_of_accounts WHERE tenant_id=$1 AND account_type IN ('EXPENSE','EQUITY','REVENUE','OTHER_INCOME','OTHER_EXPENSE')
            AND COALESCE(is_header,false)=false AND account_code NOT IN ('1-10600','5-10100') LIMIT 2""", T)]
        payable = await conn.fetchval("SELECT id FROM chart_of_accounts WHERE tenant_id=$1 AND account_type='PAYABLE' AND COALESCE(is_header,false)=false LIMIT 1", T)
        if len(aman) < 2:
            print("TAK SAH: tak ada 2 akun aman"); sys.exit(2)

        async def skenario(fn):
            sp = conn.transaction(); await sp.start()
            try:
                return await fn()
            finally:
                await sp.rollback()

        async def s_manual():
            return await reverse(await buat_jurnal("MANUAL", aman))
        k, d = await skenario(s_manual)
        catat("ALLOW", "MANUAL -> reverse 200", k == 200, (k, d[:50]))

        for st, frag in (("BILL", "void bill"), ("INVOICE", "void invoice"), ("PRODUCTION_OUTPUT", "produksi")):
            j = await conn.fetchval("SELECT id FROM journal_entries WHERE tenant_id=$1 AND source_type=$2 AND status='POSTED' AND reversed_by_id IS NULL AND reversal_of_id IS NULL LIMIT 1", T, st)
            if not j:
                catat("REJECT", f"{st}: subjek ada", False, "tak ada"); continue
            k, d = await skenario(lambda j=j: reverse(j))
            if MODE == "lama":
                catat("LAMA", f"{st}: reverse LOLOS 200 (celah)", k == 200, (k, d[:40]))
            else:
                catat("REJECT", f"{st}: reverse -> 400 '{frag}'", k == 400 and frag in d, (k, d[:70]))

        jrev = await conn.fetchval("SELECT id FROM journal_entries WHERE tenant_id=$1 AND reversal_of_id IS NOT NULL AND reversed_by_id IS NULL AND status='POSTED' LIMIT 1", T)
        if jrev and MODE != "lama":
            k, d = await skenario(lambda: reverse(jrev))
            catat("REJECT", "reverse SEBUAH PEMBALIK -> 400 (Law 26)", k == 400 and "pembalikan" in d.lower(), (k, d[:60]))

        jdone = await conn.fetchval("SELECT id FROM journal_entries WHERE tenant_id=$1 AND reversed_by_id IS NOT NULL AND status='POSTED' LIMIT 1", T)
        if jdone:
            k, d = await skenario(lambda: reverse(jdone))
            catat("REJECT", "jurnal sudah dibalik -> 409", k == 409, (k, d[:40]))

        if payable and MODE != "lama":
            async def s_pay():
                return await reverse(await buat_jurnal("MANUAL", [aman[0], payable]))
            k, d = await skenario(s_pay)
            catat("DERIVED", "reverse MANUAL menyentuh PAYABLE -> 400 (pagar lapisan di jalur balik)", k == 400 and "PAYABLE" in d, (k, d[:60]))

        if V254 and jrev:
            async def s_ror():
                try:
                    await conn.execute("""INSERT INTO journal_entries (id, tenant_id, journal_number, journal_date, description,
                        source_type, source_id, total_debit, total_credit, status, created_by, reversal_of_id)
                        VALUES (gen_random_uuid(),$1,'G5-ROR',CURRENT_DATE,'x','MANUAL',gen_random_uuid(),0,0,'DRAFT',$2,$3)""", T, uid, jrev)
                    return "LOLOS"
                except asyncpg.PostgresError as e:
                    return e.sqlstate
            got = await skenario(s_ror)
            catat("TRIGGER", "INSERT langsung pembalik-atas-pembalik -> 23514", got == "23514", got)
    finally:
        await luar.rollback()
        sisa = await conn.fetchval("SELECT count(*) FROM journal_entries WHERE journal_number LIKE 'GERBANG-G5-%'")
        trg = await conn.fetchval("SELECT count(*) FROM pg_trigger WHERE tgname='trg_prevent_reverse_of_reversal'")
        await conn.close()
    catat("BERSIH", "nol jurnal sintetis + trigger tak menetap (kecuali hidup)", sisa == 0 and (trg == 0 or MODE == "hidup"), (sisa, trg))

    for s, u, ok, k in hasil:
        print(("[H] " if ok else "[X] ") + f"{s:8} {u}  | {k}")
    g = [h[1] for h in hasil if not h[2]]
    print(f"\nMODE={MODE} v254={V254} gagal={len(g)} total={len(hasil)}")
    if MODE in ("baru", "hidup"):
        sys.exit(0 if not g else 1)
    if MODE == "sabotase":
        ok = any(x.startswith("BILL: reverse") for x in g)
        print("[sabotase] ->", "TERTANGKAP" if ok else "LOLOS", g[:3]); sys.exit(0 if ok else 1)
    merah_lama = [h for h in hasil if h[0] == "LAMA" and not h[2]]
    print("[lama] ->", "CELAH TERBUKTI" if not merah_lama else "TAK SESUAI", [h[1] for h in merah_lama])
    sys.exit(0 if not merah_lama else 1)


asyncio.run(main())
