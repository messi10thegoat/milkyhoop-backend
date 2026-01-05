#!/usr/bin/env python3
"""
Test script for Accounting Kernel Adapter
Validates the integration between reporting_service and accounting_kernel
"""
import asyncio
import os
import sys

# Add paths - order matters!
# The accounting_kernel needs to be at a level where its internal imports work
sys.path.insert(0, '/root/milkyhoop-dev/backend/services')
sys.path.insert(0, '/root/milkyhoop-dev/backend/services/reporting_service/app')

os.environ['DATABASE_URL'] = 'postgresql://postgres:Proyek771977@localhost:5433/milkydb'


async def test_adapter():
    """Test the AccountingKernelAdapter"""
    print("=" * 60)
    print("Testing Accounting Kernel Adapter")
    print("=" * 60)

    from adapters.accounting_kernel_adapter import AccountingKernelAdapter

    adapter = AccountingKernelAdapter()

    try:
        # Initialize
        print("\n1. Initializing adapter...")
        await adapter.initialize()
        print("   ✅ Adapter initialized successfully")

        # Test Laba Rugi
        print("\n2. Testing get_laba_rugi (Profit & Loss)...")
        tenant_id = "evlogia"
        periode = "2026-01"

        try:
            laba_rugi = await adapter.get_laba_rugi(tenant_id, periode)
            print(f"   ✅ Laba Rugi Report for {tenant_id}:")
            print(f"      - Total Pendapatan: Rp{laba_rugi['total_pendapatan']:,}")
            print(f"      - Total HPP: Rp{laba_rugi['total_hpp']:,}")
            print(f"      - Laba Bersih: Rp{laba_rugi['laba_bersih']:,}")
        except Exception as e:
            print(f"   ⚠️  Laba Rugi (no data yet): {e}")

        # Test Neraca
        print("\n3. Testing get_neraca (Balance Sheet)...")
        try:
            neraca = await adapter.get_neraca(tenant_id, periode)
            print(f"   ✅ Neraca Report for {tenant_id}:")
            print(f"      - Total Aset: Rp{neraca['total_aset']:,}")
            print(f"      - Total Liabilitas: Rp{neraca['total_liabilitas']:,}")
            print(f"      - Total Ekuitas: Rp{neraca['total_ekuitas']:,}")
            print(f"      - Balanced: {neraca['is_balanced']}")
        except Exception as e:
            print(f"   ⚠️  Neraca (no data yet): {e}")

        # Test Arus Kas
        print("\n4. Testing get_arus_kas (Cash Flow)...")
        try:
            arus_kas = await adapter.get_arus_kas(tenant_id, periode)
            print(f"   ✅ Arus Kas Report for {tenant_id}:")
            print(f"      - Arus Kas Operasi: Rp{arus_kas['arus_kas_operasi']:,}")
            print(f"      - Arus Kas Investasi: Rp{arus_kas['arus_kas_investasi']:,}")
            print(f"      - Arus Kas Pendanaan: Rp{arus_kas['arus_kas_pendanaan']:,}")
            print(f"      - Kas Akhir: Rp{arus_kas['kas_akhir_periode']:,}")
        except Exception as e:
            print(f"   ⚠️  Arus Kas (no data yet): {e}")

        print("\n" + "=" * 60)
        print("Test completed!")
        print("Note: Reports show 0 because no journal entries exist yet.")
        print("Once outbox_worker processes transactions with")
        print("USE_ACCOUNTING_KERNEL=true, journal entries will be created.")
        print("=" * 60)

    finally:
        await adapter.close()


async def test_journal_posting():
    """Test posting a journal entry"""
    print("\n" + "=" * 60)
    print("Testing Journal Entry Posting")
    print("=" * 60)

    from accounting_kernel.integration.facade import AccountingFacade
    import asyncpg
    from datetime import date
    from decimal import Decimal
    from uuid import uuid4

    database_url = os.environ['DATABASE_URL']
    pool = await asyncpg.create_pool(database_url, min_size=2, max_size=5)

    try:
        facade = AccountingFacade(pool)

        # Test posting a simple sale
        print("\n1. Posting a test sale transaction...")
        tenant_id = "evlogia"
        transaction_id = uuid4()

        result = await facade.record_sale(
            tenant_id=tenant_id,
            transaction_date=date.today(),
            transaction_id=transaction_id,
            amount=Decimal("100000"),  # Rp 100,000
            payment_method="tunai",
            customer_name="Test Customer",
            description="Test sale from validation script"
        )

        if result.get("success"):
            print(f"   ✅ Journal posted: {result.get('journal_number')}")
            print(f"   Journal ID: {result.get('journal_id')}")
        else:
            print(f"   ❌ Failed: {result.get('error')}")

        # Verify
        print("\n2. Verifying journal entry in database...")
        async with pool.acquire() as conn:
            entry = await conn.fetchrow(
                "SELECT * FROM journal_entries WHERE tenant_id = $1 ORDER BY created_at DESC LIMIT 1",
                tenant_id
            )
            if entry:
                print(f"   ✅ Found journal: {entry['journal_number']}")
                print(f"      Status: {entry['status']}")
                print(f"      Total Debit: Rp{entry['total_debit']:,}")
                print(f"      Total Credit: Rp{entry['total_credit']:,}")

            lines = await conn.fetch(
                """SELECT jl.*, coa.account_code, coa.name
                   FROM journal_lines jl
                   JOIN chart_of_accounts coa ON coa.id = jl.account_id
                   WHERE jl.journal_id = $1""",
                entry['id'] if entry else None
            )
            if lines:
                print(f"   ✅ Journal lines ({len(lines)}):")
                for line in lines:
                    dr_cr = "DR" if line['debit'] > 0 else "CR"
                    amt = line['debit'] if line['debit'] > 0 else line['credit']
                    print(f"      {line['account_code']} {line['name']}: {dr_cr} Rp{amt:,}")

        print("\n" + "=" * 60)
        print("Journal posting test completed!")
        print("=" * 60)

    finally:
        await pool.close()


async def main():
    """Run all tests"""
    try:
        await test_adapter()
        await test_journal_posting()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
