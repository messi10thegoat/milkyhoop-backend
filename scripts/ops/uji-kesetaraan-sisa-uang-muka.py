"""KESETARAAN compute_deposit_remaining_many (16 Okt 2026, PERF-DAFTAR-SO 2): SQL LAMA (berkorelasi, verbatim dari master e9ad7b32)
vs SQL BARU (SQL_SISA_UANG_MUKA satu-lintasan). Tanpa menulis data nyata: (A) DATA NYATA baca-saja semua uang muka per tenant;
(B) SINTETIK di tabel TEMP yang membayangi tabel jurnal (transaksi di-ROLLBACK) -- acak seed tetap: jurnal ganda lintas cabang,
satu jurnal untuk dua uang muka, jurnal non-efektif (draf/dibalik/pembalik/tipe pembalik), tenant lain, NULL, akun lain."""
import asyncio, os, random, sys, uuid
from decimal import Decimal as D
sys.path.insert(0, "/app/backend/api_gateway")
os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from app.routers.customer_deposits import SQL_SISA_UANG_MUKA as BARU

LAMA = r'''
        SELECT d.id,
               COALESCE((
                   SELECT SUM(jl.credit) - SUM(jl.debit)
                   FROM journal_lines jl
                   JOIN journal_entries je ON je.id = jl.journal_id
                   WHERE je.tenant_id = $1
                     AND jl.account_id = $3
                     AND is_effective_journal(je.id)
                     AND (
                         je.source_id = d.id
                         OR je.id IN (
                             SELECT journal_id FROM receive_payments
                             WHERE tenant_id = $1 AND created_deposit_id = d.id AND journal_id IS NOT NULL
                         )
                         -- 30 Sep 2026: kredit dari KELEBIHAN NOTA KREDIT (credit_notes.created_deposit_id, V352)
                         OR je.id IN (
                             SELECT journal_id FROM credit_notes
                             WHERE tenant_id = $1 AND created_deposit_id = d.id AND journal_id IS NOT NULL
                         )
                         -- V299: kredit dari LEPAS PEMBAYARAN (jurnal lepas bersumber faktur, Law 29/30)
                         OR je.id IN (
                             SELECT unapply_journal_id FROM receive_payment_allocations
                             WHERE tenant_id = $1 AND unapply_deposit_id = d.id AND unapply_journal_id IS NOT NULL
                         )
                     )
               ), 0) AS remaining
        FROM unnest($2::uuid[]) AS d(id)
        '''
gagal = []


def norm(rows):
    return {str(r["id"]): D(str(r["remaining"] or 0)) for r in rows}


async def banding(c, tenant, ids, akun, ket):
    a = norm(await c.fetch(LAMA, tenant, [str(i) for i in ids], akun))
    b = norm(await c.fetch(BARU, tenant, [str(i) for i in ids], akun))
    if a != b:
        gagal.append((ket, {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)}))
    return len(ids)


async def main():
    c = await asyncpg.connect(os.environ["URL"])
    await c.execute("SET default_transaction_read_only = on")
    # (A) data nyata
    for t in ("kaos-biru-konveksi", "grapgrap-manado"):
        akun = await c.fetchval("SELECT account_id FROM account_roles WHERE tenant_id=$1 AND role_key='CUSTOMER_DEPOSIT_LIABILITY' LIMIT 1", t)
        ids = [r["id"] for r in await c.fetch("SELECT id FROM customer_deposits WHERE tenant_id=$1", t)]
        n = await banding(c, t, ids, akun, f"nyata:{t}")
        for i in range(0, len(ids), 7):   # juga himpunan kecil acak/berkas (hasil tak boleh bergantung pada himpunan)
            await banding(c, t, ids[i:i + 7], akun, f"nyata-potong:{t}:{i}")
        await banding(c, t, ids + ids[:5], akun, f"nyata-duplikat:{t}")
        print(f"(A) nyata {t}: {n} uang muka, akun={'ada' if akun else 'TAK ADA'}")
    await c.execute("RESET default_transaction_read_only")
    # (B) sintetik di TEMP (membayangi tabel asli; semua di-ROLLBACK)
    tr = c.transaction(); await tr.start()
    try:
        await c.execute("""
          CREATE TEMP TABLE journal_entries (id uuid PRIMARY KEY, tenant_id text, source_id uuid, status text, reversed_by_id uuid, reversal_of_id uuid, source_type text);
          CREATE TEMP TABLE journal_lines (id serial, journal_id uuid, account_id uuid, debit numeric, credit numeric);
          CREATE TEMP TABLE receive_payments (tenant_id text, created_deposit_id uuid, journal_id uuid);
          CREATE TEMP TABLE credit_notes (tenant_id text, created_deposit_id uuid, journal_id uuid);
          CREATE TEMP TABLE receive_payment_allocations (tenant_id text, unapply_deposit_id uuid, unapply_journal_id uuid);""")
        assert await c.fetchval("SELECT count(*) FROM journal_entries") == 0     # TEMP membayangi tabel asli
        rnd = random.Random(20261016)
        T, T2 = "tA", "tB"
        total = 0
        for skenario in range(400):
            await c.execute("TRUNCATE journal_entries, journal_lines, receive_payments, credit_notes, receive_payment_allocations")
            akun, lain = uuid.uuid4(), uuid.uuid4()
            deps = [uuid.uuid4() for _ in range(rnd.randint(1, 6))]
            jur = []
            for _ in range(rnd.randint(0, 14)):
                jid = uuid.uuid4()
                st = rnd.choice(["POSTED"] * 5 + ["DRAFT", "VOID"])
                rev = uuid.uuid4() if rnd.random() < .12 else None
                revof = uuid.uuid4() if rnd.random() < .1 else None
                stype = rnd.choice(["CUSTOMER_DEPOSIT"] * 4 + ["DEPOSIT_APPLICATION", "RECEIVE_PAYMENT", "INVOICE_REVERSAL", "BILL_REVERSAL", "CREDIT_NOTE"])
                ten = T if rnd.random() < .85 else T2
                src = rnd.choice(deps + [None, uuid.uuid4()])
                await c.execute("INSERT INTO journal_entries VALUES ($1,$2,$3,$4,$5,$6,$7)", jid, ten, src, st, rev, revof, stype)
                for _ in range(rnd.randint(0, 3)):
                    ac = akun if rnd.random() < .75 else lain
                    cr = rnd.choice([D(0), D(rnd.randint(1, 900)) * D("1000.5"), None, D("0.01")])
                    db = rnd.choice([D(0), D(rnd.randint(1, 900)) * D("100.25"), None, D("0.01")])
                    await c.execute("INSERT INTO journal_lines (journal_id, account_id, debit, credit) VALUES ($1,$2,$3,$4)", jid, ac, db, cr)
                jur.append(jid)
            for tbl, kd, kj in (("receive_payments", "created_deposit_id", "journal_id"), ("credit_notes", "created_deposit_id", "journal_id"),
                                ("receive_payment_allocations", "unapply_deposit_id", "unapply_journal_id")):
                for _ in range(rnd.randint(0, 5)):
                    ten = T if rnd.random() < .85 else T2
                    await c.execute(f"INSERT INTO {tbl} VALUES ($1,$2,$3)", ten, rnd.choice(deps + [None]),
                                    rnd.choice(jur + [None]) if jur else None)
            ids = deps + ([deps[0]] if rnd.random() < .2 else [])
            total += await banding(c, T, ids, akun, f"sintetik#{skenario}")
            await banding(c, T, ids, None, f"sintetik-tanpa-akun#{skenario}")
        print(f"(B) sintetik: 400 skenario, {total} uang muka dibandingkan")
    finally:
        await tr.rollback()
    await c.close()
    print("RINGKAS BEDA=%d %s" % (len(gagal), gagal[:3]))
    sys.exit(1 if gagal else 0)

asyncio.run(main())
