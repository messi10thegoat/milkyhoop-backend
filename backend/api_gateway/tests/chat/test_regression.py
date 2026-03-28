"""
Regression Tests — 5 scenarios
Previously broken queries that should now work.
"""
from conftest import TestSuite, run_test, THRESHOLDS


async def run_regression_tests(suite: TestSuite):
    print("\n── Regression Tests (5 scenarios) ──")

    # 1. Q24 fix: "Ada berapa vendor aktif?" — was FAIL, now calc_engine
    await run_test(
        suite,
        "regression_q24_vendor_count",
        "Ada berapa vendor aktif?",
        expect_type="TEXT",
        expect_model="calc_engine",
        expect_contains=["Vendor"],
        max_latency=THRESHOLDS["calc"],
    )

    # 2. Q2 fix: "Daftar semua vendor" — was 15s agent loop, now pipeline
    await run_test(
        suite,
        "regression_q2_vendor_list",
        "Daftar semua vendor",
        expect_type="TEXT",
        expect_contains=["vendor"],
        max_latency=THRESHOLDS["query_pipeline"],
    )

    # 3. Q12 fix: "Data lengkap pelanggan Ain" — was 8s, now pipeline
    await run_test(
        suite,
        "regression_q12_customer_detail",
        "Data lengkap pelanggan Ain",
        expect_type="TEXT",
        expect_contains=["Ain"],
        max_latency=THRESHOLDS["query_pipeline"],
    )

    # 4. Rata-rata harga jual — was "tidak bisa", now calc_engine
    await run_test(
        suite,
        "regression_avg_price",
        "Berapa rata-rata harga jual item?",
        expect_type="TEXT",
        expect_model="calc_engine",
        expect_contains=["Rp"],
        max_latency=THRESHOLDS["calc"],
    )

    # 5. Item termahal — was agent loop 10s+, now calc_engine
    await run_test(
        suite,
        "regression_rank_price",
        "Ranking item termahal",
        expect_type="TEXT",
        expect_model="calc_engine",
        expect_contains=["Termahal"],
        max_latency=THRESHOLDS["calc"],
    )
