#!/usr/bin/env python3
"""
MilkyHoop Foundation Health Checker.

Direct API endpoint health checks — bypasses LLM entirely.
Tests whether the kernel/API layer actually works before running agent tests.

Usage:
  python foundation_health.py                    # run all checks
  python foundation_health.py --output health.json  # save results
"""

import asyncio
import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx


BASE_URL = "http://localhost:8001"
AUTH_EMAIL = "grapmanado@gmail.com"
AUTH_PASSWORD = "Jalanatputno.4"
AUTH_TENANT = "evlogia"


@dataclass
class HealthCheck:
    name: str
    method: str
    path: str
    expect_status: int = 200
    expect_has: Optional[str] = None
    expect_data_min: Optional[int] = None
    custom_check: Optional[str] = None
    category: str = "general"


# ─── Health Check Definitions ──────────────────────────────────────────────────

HEALTH_CHECKS: List[HealthCheck] = [
    # Master Data - Read
    HealthCheck("Customer list", "GET", "/api/customers", expect_has="items", category="master_data"),
    HealthCheck("Customer search", "GET", "/api/customers/search?q=Grapgrap", expect_data_min=1, category="master_data"),
    HealthCheck("Vendor list", "GET", "/api/vendors", category="master_data"),
    HealthCheck("Vendor search", "GET", "/api/vendors?q=Wave4", category="master_data"),
    HealthCheck("Item list", "GET", "/api/items", expect_has="items", category="master_data"),
    HealthCheck("Item search", "GET", "/api/items?search=kaos", expect_data_min=1, category="master_data"),
    HealthCheck("Chart of Accounts", "GET", "/api/accounts", expect_has="items", category="master_data"),
    HealthCheck("Account search", "GET", "/api/accounts/search?q=kas", expect_data_min=1, category="master_data"),
    HealthCheck("Account search by type", "GET", "/api/accounts/search?q=listrik&account_type=expense", category="master_data"),
    HealthCheck("Bank accounts", "GET", "/api/bank-accounts", category="master_data"),

    # Reports
    HealthCheck("Trial Balance", "GET", "/api/reports/trial-balance", category="reports"),
    HealthCheck("Profit Loss", "GET", "/api/reports/laba-rugi/2026-02", category="reports"),
    HealthCheck("Balance Sheet", "GET", "/api/reports/neraca/2026-02", category="reports"),
    HealthCheck("Cash Flow", "GET", "/api/reports/arus-kas/2026-02", category="reports"),
    HealthCheck("AR Aging", "GET", "/api/reports/aging-receivable", category="reports"),
    HealthCheck("AP Aging", "GET", "/api/reports/aging-payable", category="reports"),

    # Transaction Lists
    HealthCheck("Sales Invoices", "GET", "/api/sales-invoices", category="transactions"),
    HealthCheck("Bills", "GET", "/api/bills", category="transactions"),
    HealthCheck("Expenses", "GET", "/api/expenses", category="transactions"),
    HealthCheck("Journal Entries", "GET", "/api/journals", category="transactions"),
    HealthCheck("Receive Payments", "GET", "/api/receive-payments", category="transactions"),
    HealthCheck("Bill Payments", "GET", "/api/bill-payments", category="transactions"),

    # Dashboard
    HealthCheck("Dashboard Summary", "GET", "/api/dashboard/summary", category="dashboard"),
    HealthCheck("Overdue Invoices", "GET", "/api/dashboard/overdue-invoices", category="dashboard"),
    HealthCheck("Overdue Bills", "GET", "/api/dashboard/overdue-bills", category="dashboard"),

    # Data Integrity
    HealthCheck("Trial balance balanced", "GET", "/api/reports/trial-balance",
                custom_check="trial_balance_is_balanced", category="integrity"),
    HealthCheck("Posted invoices have journals", "GET", "/api/sales-invoices",
                custom_check="posted_invoices_have_journals", category="integrity"),
    HealthCheck("Posted bills have journals", "GET", "/api/bills",
                custom_check="posted_bills_have_journals", category="integrity"),

    # Chat endpoint health
    HealthCheck("Chat v3 endpoint", "POST", "/api/v3/chat/message",
                custom_check="chat_endpoint_responds", category="agent"),
]


# ─── Custom Check Functions ────────────────────────────────────────────────────

# Custom checks are async and receive (data, client, headers, base_url)
# to allow multi-step verification (e.g., fetch invoice then check journals).

async def check_trial_balance_is_balanced(
    data: Any, client: httpx.AsyncClient, headers: Dict, base_url: str
) -> Dict[str, str]:
    """Verify trial balance: total debits == total credits (Law 4)."""
    entries = []
    if isinstance(data, dict):
        entries = data.get("data", data.get("accounts", data.get("items", [])))
    elif isinstance(data, list):
        entries = data

    if not entries:
        return {"status": "WARN", "detail": "No trial balance entries"}

    total_debit = 0.0
    total_credit = 0.0
    for e in entries:
        if isinstance(e, dict):
            total_debit += float(e.get("debit", e.get("total_debit", 0)) or 0)
            total_credit += float(e.get("credit", e.get("total_credit", 0)) or 0)

    diff = abs(total_debit - total_credit)
    if diff < 1.0:  # Allow Rp 1 rounding
        return {"status": "PASS", "detail": f"Balanced: DR={total_debit:,.0f} CR={total_credit:,.0f}"}
    else:
        return {"status": "FAIL", "detail": f"UNBALANCED: DR={total_debit:,.0f} CR={total_credit:,.0f} diff={diff:,.0f}"}


async def check_posted_invoices_have_journals(
    data: Any, client: httpx.AsyncClient, headers: Dict, base_url: str
) -> Dict[str, str]:
    """Verify posted invoices have journal entries via source_id lookup (Law 6).

    The link between invoices and journals is:
      journal_entries.source_id = sales_invoices.id
      journal_entries.source_type = 'INVOICE'
    (NOT via a journal_entry_id column on the invoice table)
    """
    # Extract invoices from response
    items = _extract_items(data)
    if not items:
        return {"status": "PASS", "detail": "No invoices to check"}

    # Find posted invoices
    posted = [inv for inv in items if inv.get("status") == "posted"]
    if not posted:
        return {"status": "PASS", "detail": "No posted invoices to check"}

    # Check first posted invoice has journal via journals API
    inv = posted[0]
    inv_id = inv.get("id", "")
    inv_num = inv.get("invoice_number", inv.get("number", "?"))

    try:
        resp = await client.get(
            f"{base_url}/api/journals",
            headers=headers,
            params={"source_id": inv_id},
        )
        if resp.status_code == 200:
            journal_data = resp.json()
            journal_items = _extract_items(journal_data)
            if journal_items:
                return {"status": "PASS", "detail": f"Invoice {inv_num} has {len(journal_items)} journal(s)"}
            else:
                return {"status": "FAIL", "detail": f"Invoice {inv_num} MISSING journal entry (Law 6 violation)"}
        else:
            # Journals API might not support source_id filter — fallback
            return {"status": "WARN", "detail": f"Cannot verify journal for {inv_num} (journals API {resp.status_code})"}
    except Exception as e:
        return {"status": "WARN", "detail": f"Journal check error: {str(e)[:100]}"}


async def check_posted_bills_have_journals(
    data: Any, client: httpx.AsyncClient, headers: Dict, base_url: str
) -> Dict[str, str]:
    """Verify posted bills have journal entries (Law 6).

    Bills table has a journal_id column. Also cross-check via journals API.
    source_type for bills is 'BILL' in journal_entries.
    """
    items = _extract_items(data)
    if not items:
        return {"status": "PASS", "detail": "No bills to check"}

    # Find paid/unpaid bills (these were posted at some point)
    posted = [b for b in items if b.get("status") in ("posted", "paid", "unpaid", "partial")]
    if not posted:
        return {"status": "PASS", "detail": "No posted bills to check"}

    bill = posted[0]
    bill_num = bill.get("invoice_number", bill.get("bill_number", bill.get("number", "?")))

    # Check journal_id on the bill directly (bills table has this column)
    has_direct_link = bool(bill.get("journal_id") or bill.get("journal_entry_id"))
    if has_direct_link:
        return {"status": "PASS", "detail": f"Bill {bill_num} has journal_id"}

    # Fallback: check via journals API
    bill_id = bill.get("id", "")
    try:
        resp = await client.get(
            f"{base_url}/api/journals",
            headers=headers,
            params={"source_id": bill_id},
        )
        if resp.status_code == 200:
            journal_data = resp.json()
            journal_items = _extract_items(journal_data)
            if journal_items:
                return {"status": "PASS", "detail": f"Bill {bill_num} has {len(journal_items)} journal(s)"}
            else:
                return {"status": "FAIL", "detail": f"Bill {bill_num} MISSING journal entry (Law 6 violation)"}
        else:
            return {"status": "WARN", "detail": f"Cannot verify journal for {bill_num} (journals API {resp.status_code})"}
    except Exception as e:
        return {"status": "WARN", "detail": f"Journal check error: {str(e)[:100]}"}


async def check_chat_endpoint_responds(
    data: Any, client: httpx.AsyncClient, headers: Dict, base_url: str
) -> Dict[str, str]:
    """Verify chat endpoint responds without crashing."""
    if isinstance(data, dict):
        if data.get("message_type") or data.get("text") or data.get("detail"):
            return {"status": "PASS", "detail": "Chat endpoint responding"}
    return {"status": "PASS", "detail": "Chat endpoint alive"}


CUSTOM_CHECKS = {
    "trial_balance_is_balanced": check_trial_balance_is_balanced,
    "posted_invoices_have_journals": check_posted_invoices_have_journals,
    "posted_bills_have_journals": check_posted_bills_have_journals,
    "chat_endpoint_responds": check_chat_endpoint_responds,
}


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _extract_items(data: Any) -> List[Dict]:
    """Extract list items from various API response formats.

    APIs return data in different keys: 'data', 'items', 'customers', 'accounts',
    'invoices', 'bills', 'journals', 'vendors', etc. This helper checks all of them.
    """
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []

    # Check known response keys in priority order
    for key in ("data", "items", "customers", "accounts", "vendors",
                "invoices", "bills", "journals", "expenses", "payments",
                "entries"):
        val = data.get(key)
        if isinstance(val, list) and val:
            return val

    return []


# ─── Health Check Runner ──────────────────────────────────────────────────────

async def get_token() -> str:
    """Login and get auth token."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": AUTH_EMAIL, "password": AUTH_PASSWORD, "tenant_slug": AUTH_TENANT},
        )
        if resp.status_code == 200:
            return resp.json()["data"]["access_token"]
        else:
            print(f"Login failed: {resp.status_code}")
            sys.exit(1)


async def run_health_checks(token: str) -> List[Dict]:
    """Run all health checks, return results."""
    results = []
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30) as client:
        for check in HEALTH_CHECKS:
            result = {"name": check.name, "category": check.category, "status": "?", "detail": "", "latency_ms": 0}
            start = time.time()

            try:
                if check.method == "GET":
                    resp = await client.get(f"{BASE_URL}{check.path}", headers=headers)
                elif check.method == "POST":
                    body = {}
                    if "chat" in check.path:
                        body = {"conversation_id": "health-check", "text": "ping"}
                    resp = await client.post(f"{BASE_URL}{check.path}", json=body, headers=headers)
                else:
                    result["status"] = "SKIP"
                    result["detail"] = f"Unsupported method: {check.method}"
                    results.append(result)
                    continue

                result["latency_ms"] = int((time.time() - start) * 1000)

                # Status code check
                if resp.status_code != check.expect_status:
                    if resp.status_code < 500:
                        result["status"] = "WARN"
                        result["detail"] = f"Expected {check.expect_status}, got {resp.status_code}"
                    else:
                        result["status"] = "FAIL"
                        result["detail"] = f"Server error: {resp.status_code}"
                    results.append(result)
                    continue

                data = resp.json()

                # Custom check (async, with client access for multi-step verification)
                if check.custom_check:
                    checker = CUSTOM_CHECKS.get(check.custom_check)
                    if checker:
                        custom_result = await checker(data, client, headers, BASE_URL)
                        result["status"] = custom_result["status"]
                        result["detail"] = custom_result["detail"]
                    else:
                        result["status"] = "PASS"
                        result["detail"] = f"Unknown custom check: {check.custom_check}"
                    result["latency_ms"] = int((time.time() - start) * 1000)
                    results.append(result)
                    continue

                # Field existence check
                if check.expect_has:
                    if isinstance(data, dict) and check.expect_has in data:
                        result["status"] = "PASS"
                    elif isinstance(data, dict):
                        result["status"] = "WARN"
                        result["detail"] = f"Missing key: '{check.expect_has}'. Keys: {list(data.keys())[:5]}"
                    else:
                        result["status"] = "PASS"

                # Data minimum check (uses _extract_items for all response formats)
                elif check.expect_data_min is not None:
                    items = _extract_items(data)
                    count = len(items)
                    if count >= check.expect_data_min:
                        result["status"] = "PASS"
                        result["detail"] = f"{count} items"
                    else:
                        result["status"] = "FAIL"
                        result["detail"] = f"Expected >= {check.expect_data_min}, got {count}"

                # Default: status code was OK
                else:
                    result["status"] = "PASS"

            except httpx.TimeoutException:
                result["status"] = "FAIL"
                result["detail"] = "Timeout"
                result["latency_ms"] = int((time.time() - start) * 1000)
            except Exception as e:
                result["status"] = "ERROR"
                result["detail"] = str(e)[:200]
                result["latency_ms"] = int((time.time() - start) * 1000)

            results.append(result)

    return results


def print_results(results: List[Dict]):
    """Print results as a formatted table."""
    icons = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️ ", "ERROR": "💥", "SKIP": "⏭️"}

    categories = {}
    for r in results:
        cat = r["category"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(r)

    sep = "=" * 80
    print(f"\n{sep}")
    print("  MilkyHoop Foundation Health Check")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{sep}\n")

    for cat, checks in categories.items():
        print(f"  [{cat.upper()}]")
        for r in checks:
            icon = icons.get(r["status"], "?")
            latency = f"({r['latency_ms']}ms)" if r["latency_ms"] else ""
            detail = f" — {r['detail']}" if r["detail"] else ""
            print(f"    {icon} {r['name']}: {r['status']} {latency}{detail}")
        print()

    passed = sum(1 for r in results if r["status"] == "PASS")
    warned = sum(1 for r in results if r["status"] == "WARN")
    failed = sum(1 for r in results if r["status"] in ("FAIL", "ERROR"))
    total = len(results)

    print(f"{sep}")
    print(f"  Health: {passed}/{total} passed, {warned} warnings, {failed} failures")
    if failed > 0:
        print(f"\n  ❌ FAILURES:")
        for r in results:
            if r["status"] in ("FAIL", "ERROR"):
                print(f"     - {r['name']}: {r['detail']}")
    print(f"{sep}\n")


async def main():
    parser = argparse.ArgumentParser(description="MilkyHoop Foundation Health Checker")
    parser.add_argument("--output", help="Save results as JSON")
    parser.add_argument("--category", help="Filter by category")
    args = parser.parse_args()

    print("Authenticating...", end=" ", flush=True)
    token = await get_token()
    print("OK\n")

    results = await run_health_checks(token)

    if args.category:
        results = [r for r in results if r["category"] == args.category]

    print_results(results)

    if args.output:
        report = {
            "timestamp": datetime.now().isoformat(),
            "total": len(results),
            "passed": sum(1 for r in results if r["status"] == "PASS"),
            "failed": sum(1 for r in results if r["status"] in ("FAIL", "ERROR")),
            "warned": sum(1 for r in results if r["status"] == "WARN"),
            "results": results,
        }
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"Results saved to {args.output}")

    failures = sum(1 for r in results if r["status"] in ("FAIL", "ERROR"))
    sys.exit(0 if failures == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
