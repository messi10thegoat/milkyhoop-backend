"""
Query Tests — 10 scenarios
Tests calculation engine, query pipeline, and agent loop queries.
"""
from conftest import TestSuite, run_test, THRESHOLDS


async def run_query_tests(suite: TestSuite):
    print("\n── Query Tests (10 scenarios) ──")

    # Calculation engine tests
    # 1. AVG
    await run_test(
        suite,
        "calc_avg_harga_jual",
        "Rata-rata harga jual barang",
        expect_type="TEXT",
        expect_model="calc_engine",
        expect_contains=["Rp"],
        max_latency=THRESHOLDS["calc"],
    )

    # 2. COUNT items
    await run_test(
        suite,
        "calc_count_items",
        "Berapa jumlah item aktif?",
        expect_type="TEXT",
        expect_model="calc_engine",
        expect_contains=["Item Aktif"],
        max_latency=THRESHOLDS["calc"],
    )

    # 3. RANK
    await run_test(
        suite,
        "calc_rank_termahal",
        "Item termahal apa?",
        expect_type="TEXT",
        expect_model="calc_engine",
        expect_contains=["Termahal"],
        max_latency=THRESHOLDS["calc"],
    )

    # 4. COUNT vendors
    await run_test(
        suite,
        "calc_count_vendors",
        "Jumlah vendor aktif berapa?",
        expect_type="TEXT",
        expect_model="calc_engine",
        expect_contains=["Vendor Aktif"],
        max_latency=THRESHOLDS["calc"],
    )

    # 5. COUNT customers
    await run_test(
        suite,
        "calc_count_customers",
        "Jumlah pelanggan aktif?",
        expect_type="TEXT",
        expect_model="calc_engine",
        expect_contains=["Pelanggan Aktif"],
        max_latency=THRESHOLDS["calc"],
    )

    # Query pipeline tests
    # 6. Customer detail
    await run_test(
        suite,
        "query_customer_detail",
        "Data lengkap pelanggan Ain",
        expect_type="TEXT",
        expect_contains=["Ain"],
        max_latency=THRESHOLDS["query_pipeline"],
    )

    # 7. Vendor list
    await run_test(
        suite,
        "query_vendors_list",
        "Daftar semua vendor",
        expect_type="TEXT",
        expect_contains=["vendor"],
        max_latency=THRESHOLDS["query_pipeline"],
    )

    # 8. Customers list
    await run_test(
        suite,
        "query_customers_list",
        "Daftar semua pelanggan",
        expect_type="TEXT",
        expect_contains=["pelanggan"],
        max_latency=THRESHOLDS["query_pipeline"],
    )

    # 9. Item detail
    await run_test(
        suite,
        "query_item_detail",
        "Cek detail barang Paracetamol 500mg",
        expect_type="TEXT",
        expect_contains=["Paracetamol"],
        max_latency=THRESHOLDS["query_pipeline"],
    )

    # 10. Categories list
    await run_test(
        suite,
        "query_categories_list",
        "Daftar kategori barang",
        expect_type="TEXT",
        max_latency=THRESHOLDS["query_pipeline"],
    )
