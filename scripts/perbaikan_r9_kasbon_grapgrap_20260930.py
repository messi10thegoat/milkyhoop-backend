"""Perbaikan data R9 grapgrap (30 Sep 2026, izin pemilik LANGSUNG di sesi BACKEND, opsi 2).

Masalah: 4 jurnal kasbon (EMPLOYEE_ADVANCE_GRANT, 1 Sep, "Kasbon awal (saldo pembuka)") mengkredit
1-10202 BCA Pengeluaran TANPA cermin bank_transactions (employee_advances.grant_advance tak pernah
membuat cermin) -> gap R9 BCA Pengeluaran -1.600.000 (jurnal 1.366.900 vs mutasi 2.966.900).
Putusan pemilik: keempat kasbon memang KELUAR dari BCA Pengeluaran sesudah pakai MilkyHoop ->
tambahkan 4 cermin mutasi (withdrawal) untuk jurnal yang ADA. Jurnal TIDAK diubah (Law 2).

Jalur resmi: services.bank_sync.create_bank_transaction_for_journal (BankSync Rule 7: bank_txn yang
hilang = INSERT turunan jurnal). Idempoten: jurnal yang sudah bercermin dilewati.
Gagal-keras (ROLLBACK) bila: jumlah jurnal sasaran != 4, jurnal tak POSTED/terbalik, akun bukan
BCA Pengeluaran grapgrap, atau sesudah tulis gap R9 akun != 0 / cermin per jurnal != kredit jurnal.

Pakai (kontainer sekali-pakai, pohon :ro):  MODE=kering|tulis python perbaikan_r9_kasbon_grapgrap_20260930.py
  kering = jalankan semuanya lalu ROLLBACK (bukti); tulis = COMMIT.
"""
import asyncio
import os
import sys
from decimal import Decimal

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402

T = "grapgrap-manado"
BANK_ACCOUNT = "81921613-1412-4442-b380-295f82f23aa9"   # BCA Pengeluaran
COA = "1961d35d-0d74-4a3a-9c45-61ed166b4cfe"            # 1-10202
JURNAL = ["KASBON-B5C53D40", "KASBON-1163A690", "KASBON-56BC6302", "KASBON-9C85805F"]
MODE = os.environ.get("MODE", "kering")


async def main():
    assert MODE in ("kering", "tulis"), MODE
    from app.services.bank_sync import create_bank_transaction_for_journal
    c = await asyncpg.connect(os.environ["DSN"])
    try:
        assert await c.fetchval("select current_database()") == "milkydb"
        tr = c.transaction()
        await tr.start()
        try:
            ba = await c.fetchrow("select id, coa_id, account_name from bank_accounts where id=$1 and tenant_id=$2", BANK_ACCOUNT, T)
            assert ba and str(ba["coa_id"]) == COA and ba["account_name"] == "BCA Pengeluaran", ba
            await c.execute("select pg_advisory_xact_lock(hashtext($1))", f"BANK_TX:r9-kasbon:{BANK_ACCOUNT}")
            rows = await c.fetch(
                """select je.id, je.journal_number, je.journal_date, je.status, je.reversed_by_id, je.source_type,
                          je.source_id, je.created_by, jl.credit, jl.debit, e.name as karyawan
                   from journal_entries je join journal_lines jl on jl.journal_id=je.id and jl.account_id=$3
                   left join employee_advances ea on ea.id=je.source_id and ea.tenant_id=je.tenant_id
                   left join employees e on e.id=ea.employee_id
                   where je.tenant_id=$1 and je.journal_number = any($2::text[])""",
                T, JURNAL, ba["coa_id"],
            )
            assert len(rows) == 4, f"sasaran {len(rows)} != 4"
            dibuat = []
            for r in rows:
                assert r["status"] == "POSTED" and r["reversed_by_id"] is None and r["source_type"] == "EMPLOYEE_ADVANCE_GRANT", dict(r)
                assert r["debit"] == 0 and r["credit"] > 0, dict(r)
                ada = await c.fetchval("select count(*) from bank_transactions where journal_id=$1 and bank_account_id=$2", r["id"], ba["id"])
                if ada:
                    print("LEWATI (sudah bercermin):", r["journal_number"])
                    continue
                bt = await create_bank_transaction_for_journal(
                    c, tenant_id=T, bank_account_id=ba["id"], journal_id=r["id"],
                    transaction_date=r["journal_date"], transaction_type="withdrawal",
                    amount=-r["credit"], reference_type="employee_advance", reference_id=r["source_id"],
                    created_by=r["created_by"], reference_number=r["journal_number"],
                    description=f"Kasbon karyawan - {r['karyawan'] or ''} (saldo pembuka 1 Sep; cermin mutasi R9 30 Sep)".replace("-  (", "- ("),
                    payee_payer=r["karyawan"],
                )
                dibuat.append((r["journal_number"], str(-r["credit"]), str(bt)))
            # bukti SEBELUM commit
            for r in rows:
                cermin = await c.fetchval("select coalesce(sum(amount),0) from bank_transactions where journal_id=$1 and bank_account_id=$2", r["id"], ba["id"])
                assert cermin == -r["credit"], (r["journal_number"], cermin, r["credit"])
            buku = await c.fetchval("""select coalesce(sum(jl.debit)-sum(jl.credit),0) from journal_lines jl
                join journal_entries je on je.id=jl.journal_id and je.status='POSTED' where jl.account_id=$1""", ba["coa_id"])
            mutasi = await c.fetchval("select coalesce(sum(amount),0) from bank_transactions where bank_account_id=$1 and tenant_id=$2", ba["id"], T)
            print("DIBUAT:", dibuat)
            print(f"buku={buku} mutasi={mutasi} gap={buku - mutasi}")
            assert buku - mutasi == Decimal("0"), "gap R9 BCA Pengeluaran bukan 0 sesudah cermin"
        except BaseException:
            await tr.rollback()
            raise
        if MODE == "tulis":
            await tr.commit()
            print("COMMIT")
        else:
            await tr.rollback()
            print("ROLLBACK (kering)")
    finally:
        await c.close()

asyncio.run(main())
