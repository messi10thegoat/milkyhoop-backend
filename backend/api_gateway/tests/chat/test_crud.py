"""
CRUD Tests — 10 scenarios
Tests create, update, delete for items, customers, vendors.
"""
from conftest import TestSuite, run_test, THRESHOLDS


async def run_crud_tests(suite: TestSuite):
    print("\n── CRUD Tests (10 scenarios) ──")

    # 1. Create item
    await run_test(
        suite,
        "create_item",
        "Tambah barang TestBot001 tipe persediaan satuan pcs harga jual 5000",
        expect_type="DIRECT_ACTION_PREVIEW",
        expect_contains=["TestBot001"],
        max_latency=THRESHOLDS["crud"],
    )

    # 2. Create customer
    await run_test(
        suite,
        "create_customer",
        "Tambah pelanggan TestCustomer001 telepon 08123456789",
        expect_type="DIRECT_ACTION_PREVIEW",
        expect_contains=["TestCustomer001"],
        max_latency=THRESHOLDS["crud"],
    )

    # 3. Create vendor
    await run_test(
        suite,
        "create_vendor",
        "Tambah vendor TestVendor001 telepon 08111222333",
        expect_type="DIRECT_ACTION_PREVIEW",
        expect_contains=["TestVendor001"],
        max_latency=THRESHOLDS["crud"],
    )

    # 4. Create item (service type)
    await run_test(
        suite,
        "create_item_service",
        "Tambah jasa Konsultasi IT harga jual 500000 satuan jam",
        expect_type="DIRECT_ACTION_PREVIEW",
        expect_contains=["Konsultasi IT"],
        max_latency=THRESHOLDS["crud"],
    )

    # 5. Update item
    await run_test(
        suite,
        "update_item",
        "Ubah harga jual Paracetamol 500mg jadi 25000",
        expect_type="DIRECT_ACTION_PREVIEW",
        max_latency=THRESHOLDS["crud"],
    )

    # 6. Update customer (may return TEXT if needs clarification, or PREVIEW)
    await run_test(
        suite,
        "update_customer",
        "Ubah alamat pelanggan Ain jadi Jl. Merdeka 10",
        max_latency=THRESHOLDS["crud"],
    )

    # 7. Update vendor
    await run_test(
        suite,
        "update_vendor",
        "Ubah telepon vendor Evlogia jadi 08999888777",
        expect_type="DIRECT_ACTION_PREVIEW",
        max_latency=THRESHOLDS["crud"],
    )

    # 8. Delete item (proposal only, won't confirm)
    await run_test(
        suite,
        "delete_item",
        "Hapus barang Semen Portland 50kg",
        expect_type="DIRECT_ACTION_PREVIEW",
        max_latency=THRESHOLDS["crud"],
    )

    # 9. Create expense (complex — may ask for bank account or return preview)
    await run_test(
        suite,
        "create_expense",
        "Catat pengeluaran bensin 150000 dari kas kecil",
        max_latency=THRESHOLDS["agent_loop"],
    )

    # 10. Create sales invoice (complex — may ask clarification or return preview)
    await run_test(
        suite,
        "create_sales_invoice",
        "Buat faktur penjualan untuk pelanggan Ain, 2 pcs Paracetamol 500mg",
        max_latency=THRESHOLDS["agent_loop"],
    )
