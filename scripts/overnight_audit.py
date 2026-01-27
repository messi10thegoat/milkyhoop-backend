#!/usr/bin/env python3
"""
Overnight Accounting Endpoint Audit Script
Tests all 16 accounting modules endpoints
"""

import requests
import json
from datetime import datetime, date
from typing import Dict, List, Any
import sys

# Configuration
BASE_URL = "http://localhost:8000/api"
RESULTS = []

def log(msg: str, status: str = "INFO"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    icon = {"PASS": "✅", "FAIL": "❌", "INFO": "ℹ️", "WARN": "⚠️"}.get(status, "•")
    print(f"[{timestamp}] {icon} {msg}")
    RESULTS.append({"time": timestamp, "status": status, "message": msg})

def test_endpoint(method: str, path: str, token: str, data: dict = None, expected_status: int = 200) -> dict:
    """Test a single endpoint"""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = f"{BASE_URL}{path}"
    
    try:
        if method == "GET":
            resp = requests.get(url, headers=headers, timeout=30)
        elif method == "POST":
            resp = requests.post(url, headers=headers, json=data, timeout=30)
        elif method == "PUT":
            resp = requests.put(url, headers=headers, json=data, timeout=30)
        elif method == "DELETE":
            resp = requests.delete(url, headers=headers, timeout=30)
        
        success = resp.status_code == expected_status
        return {
            "success": success,
            "status_code": resp.status_code,
            "data": resp.json() if resp.text else None,
            "error": None
        }
    except Exception as e:
        return {"success": False, "status_code": 0, "data": None, "error": str(e)}

def get_auth_token() -> str:
    """Get auth token for testing"""
    # This should use your actual auth mechanism
    resp = requests.post(f"{BASE_URL}/auth/login", json={
        "email": "test@milkyhoop.com",
        "password": "testpassword"
    })
    if resp.status_code == 200:
        return resp.json().get("access_token")
    return None

# ============ TEST SUITES ============

def test_journals(token: str) -> Dict[str, Any]:
    """Test Jurnal Umum endpoints"""
    log("Testing Journals (Jurnal Umum)...")
    results = {}
    
    # GET /journals
    r = test_endpoint("GET", "/journals", token)
    results["list"] = r["success"]
    log(f"GET /journals: {r['status_code']}", "PASS" if r["success"] else "FAIL")
    
    # GET /journals/summary (if exists)
    r = test_endpoint("GET", "/journals?page=1&per_page=5", token)
    results["paginated"] = r["success"]
    
    return results

def test_ledger(token: str) -> Dict[str, Any]:
    """Test Buku Besar endpoints"""
    log("Testing Ledger (Buku Besar)...")
    results = {}
    
    r = test_endpoint("GET", "/ledger", token)
    results["list"] = r["success"]
    log(f"GET /ledger: {r['status_code']}", "PASS" if r["success"] else "FAIL")
    
    r = test_endpoint("GET", "/ledger/summary", token)
    results["summary"] = r["success"]
    log(f"GET /ledger/summary: {r['status_code']}", "PASS" if r["success"] else "FAIL")
    
    return results

def test_periods(token: str) -> Dict[str, Any]:
    """Test Periode & Tutup Buku endpoints"""
    log("Testing Periods (Tutup Buku)...")
    results = {}
    
    r = test_endpoint("GET", "/periods", token)
    results["list"] = r["success"]
    log(f"GET /periods: {r['status_code']}", "PASS" if r["success"] else "FAIL")
    
    r = test_endpoint("GET", "/periods/current", token)
    results["current"] = r["success"]
    log(f"GET /periods/current: {r['status_code']}", "PASS" if r["success"] else "FAIL")
    
    r = test_endpoint("GET", "/fiscal-years", token)
    results["fiscal_years"] = r["success"]
    log(f"GET /fiscal-years: {r['status_code']}", "PASS" if r["success"] else "FAIL")
    
    return results

def test_reports(token: str) -> Dict[str, Any]:
    """Test Laporan endpoints"""
    log("Testing Reports (Laporan)...")
    results = {}
    
    periode = "2026-01"
    
    r = test_endpoint("GET", "/reports/trial-balance", token)
    results["trial_balance"] = r["success"]
    log(f"GET /reports/trial-balance: {r['status_code']}", "PASS" if r["success"] else "FAIL")
    
    r = test_endpoint("GET", f"/reports/laba-rugi/{periode}", token)
    results["laba_rugi"] = r["success"]
    log(f"GET /reports/laba-rugi: {r['status_code']}", "PASS" if r["success"] else "FAIL")
    
    r = test_endpoint("GET", f"/reports/neraca/{periode}", token)
    results["neraca"] = r["success"]
    log(f"GET /reports/neraca: {r['status_code']}", "PASS" if r["success"] else "FAIL")
    
    r = test_endpoint("GET", f"/reports/arus-kas/{periode}", token)
    results["arus_kas"] = r["success"]
    log(f"GET /reports/arus-kas: {r['status_code']}", "PASS" if r["success"] else "FAIL")
    
    r = test_endpoint("GET", "/reports/ar-aging", token)
    results["ar_aging"] = r["success"]
    log(f"GET /reports/ar-aging: {r['status_code']}", "PASS" if r["success"] else "FAIL")
    
    r = test_endpoint("GET", "/reports/ap-aging", token)
    results["ap_aging"] = r["success"]
    log(f"GET /reports/ap-aging: {r['status_code']}", "PASS" if r["success"] else "FAIL")
    
    return results

def test_receive_payments(token: str) -> Dict[str, Any]:
    """Test Penerimaan Pembayaran endpoints"""
    log("Testing Receive Payments (Penerimaan Pembayaran)...")
    results = {}
    
    r = test_endpoint("GET", "/receive-payments", token)
    results["list"] = r["success"]
    log(f"GET /receive-payments: {r['status_code']}", "PASS" if r["success"] else "FAIL")
    
    r = test_endpoint("GET", "/receive-payments/summary", token)
    results["summary"] = r["success"]
    log(f"GET /receive-payments/summary: {r['status_code']}", "PASS" if r["success"] else "FAIL")
    
    return results

def test_expenses(token: str) -> Dict[str, Any]:
    """Test Biaya & Pengeluaran endpoints"""
    log("Testing Expenses (Biaya & Pengeluaran)...")
    results = {}
    
    r = test_endpoint("GET", "/expenses", token)
    results["list"] = r["success"]
    log(f"GET /expenses: {r['status_code']}", "PASS" if r["success"] else "FAIL")
    
    r = test_endpoint("GET", "/expenses/summary", token)
    results["summary"] = r["success"]
    log(f"GET /expenses/summary: {r['status_code']}", "PASS" if r["success"] else "FAIL")
    
    return results

def test_master_data(token: str) -> Dict[str, Any]:
    """Test master data endpoints"""
    log("Testing Master Data (Customers, Vendors, Items, Accounts)...")
    results = {}
    
    for endpoint in ["/customers", "/vendors", "/items", "/accounts/tree"]:
        r = test_endpoint("GET", endpoint, token)
        key = endpoint.replace("/", "_").strip("_")
        results[key] = r["success"]
        log(f"GET {endpoint}: {r['status_code']}", "PASS" if r["success"] else "FAIL")
    
    return results

def test_bank(token: str) -> Dict[str, Any]:
    """Test Kas & Bank endpoints"""
    log("Testing Bank (Kas & Bank)...")
    results = {}
    
    r = test_endpoint("GET", "/bank-accounts", token)
    results["list"] = r["success"]
    log(f"GET /bank-accounts: {r['status_code']}", "PASS" if r["success"] else "FAIL")
    
    r = test_endpoint("GET", "/bank-reconciliation/sessions", token)
    results["reconciliation"] = r["success"]
    log(f"GET /bank-reconciliation/sessions: {r['status_code']}", "PASS" if r["success"] else "FAIL")
    
    return results

def test_invoices(token: str) -> Dict[str, Any]:
    """Test Invoice endpoints"""
    log("Testing Invoices (Sales & Purchase)...")
    results = {}
    
    r = test_endpoint("GET", "/sales-invoices", token)
    results["sales"] = r["success"]
    log(f"GET /sales-invoices: {r['status_code']}", "PASS" if r["success"] else "FAIL")
    
    r = test_endpoint("GET", "/bills", token)
    results["bills"] = r["success"]
    log(f"GET /bills: {r['status_code']}", "PASS" if r["success"] else "FAIL")
    
    return results

# ============ MAIN ============

def main():
    log("="*50)
    log("MILKYHOOP ACCOUNTING ENDPOINT AUDIT")
    log(f"Started: {datetime.now().isoformat()}")
    log("="*50)
    
    # Get token
    token = get_auth_token()
    if not token:
        log("Failed to get auth token!", "FAIL")
        sys.exit(1)
    log("Auth token obtained", "PASS")
    
    # Run all tests
    all_results = {}
    all_results["journals"] = test_journals(token)
    all_results["ledger"] = test_ledger(token)
    all_results["periods"] = test_periods(token)
    all_results["reports"] = test_reports(token)
    all_results["receive_payments"] = test_receive_payments(token)
    all_results["expenses"] = test_expenses(token)
    all_results["master_data"] = test_master_data(token)
    all_results["bank"] = test_bank(token)
    all_results["invoices"] = test_invoices(token)
    
    # Summary
    log("="*50)
    log("SUMMARY")
    log("="*50)
    
    total_pass = 0
    total_fail = 0
    for module, tests in all_results.items():
        passes = sum(1 for v in tests.values() if v)
        fails = sum(1 for v in tests.values() if not v)
        total_pass += passes
        total_fail += fails
        status = "PASS" if fails == 0 else "FAIL"
        log(f"{module}: {passes}/{passes+fails} passed", status)
    
    log("="*50)
    log(f"TOTAL: {total_pass}/{total_pass+total_fail} endpoints passed")
    log(f"Completed: {datetime.now().isoformat()}")
    
    # Save results
    with open("/root/milkyhoop-dev/docs/audit_results.json", "w") as f:
        json.dump({"results": all_results, "log": RESULTS}, f, indent=2)
    log("Results saved to docs/audit_results.json")

if __name__ == "__main__":
    main()
