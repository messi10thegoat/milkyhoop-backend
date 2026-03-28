"""
Pipeline Expansion Tests — 8 scenarios
Tests new query pipeline for Kas & Bank, Sales Invoice, Bills, Expense.
"""
from conftest import TestSuite, run_test, THRESHOLDS


async def run_pipeline_expansion_tests(suite: TestSuite):
    print("\n── Pipeline Expansion Tests (8 scenarios) ──")

    # Kas & Bank
    await run_test(
        suite,
        "query_bank_accounts_list",
        "Daftar semua rekening",
        expect_type="TEXT",
        expect_contains=["rekening"],
        max_latency=THRESHOLDS["query_pipeline"],
    )

    # Sales Invoices
    await run_test(
        suite,
        "query_sales_invoices_list",
        "Daftar faktur penjualan",
        expect_type="TEXT",
        max_latency=THRESHOLDS["query_pipeline"],
    )

    await run_test(
        suite,
        "query_sales_invoices_summary",
        "Ringkasan penjualan",
        expect_type="TEXT",
        max_latency=THRESHOLDS["query_pipeline"],
    )

    # Bills
    await run_test(
        suite,
        "query_bills_list",
        "Daftar faktur pembelian",
        expect_type="TEXT",
        max_latency=THRESHOLDS["query_pipeline"],
    )

    await run_test(
        suite,
        "query_bills_summary",
        "Ringkasan pembelian",
        expect_type="TEXT",
        max_latency=THRESHOLDS["query_pipeline"],
    )

    # Expenses
    await run_test(
        suite,
        "query_expenses_list",
        "Daftar pengeluaran",
        expect_type="TEXT",
        max_latency=THRESHOLDS["query_pipeline"],
    )

    await run_test(
        suite,
        "query_expenses_summary",
        "Ringkasan pengeluaran",
        expect_type="TEXT",
        max_latency=THRESHOLDS["query_pipeline"],
    )

    # Cross-module: ensure no regression on existing
    await run_test(
        suite,
        "regression_items_still_work",
        "Cek detail barang Paracetamol 500mg",
        expect_type="TEXT",
        expect_contains=["Paracetamol"],
        max_latency=THRESHOLDS["query_pipeline"],
    )
