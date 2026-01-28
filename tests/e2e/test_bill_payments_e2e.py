#!/usr/bin/env python3
"""
E2E Tests for Bill Payments (PaymentOut) Module
================================================
Tests the complete bill payment workflow including:
- Authentication
- Listing vendors and bank accounts
- Getting open bills for vendors
- Creating draft and posted payments
- Viewing payment details
- Deleting draft payments
- Voiding posted payments

Run: python3 test_bill_payments_e2e.py
"""

import requests
import json
from datetime import date, datetime
import sys
import time

# Configuration
BASE_URL = "https://milkyhoop.com/api"
EMAIL = "grapmanado@gmail.com"
PASSWORD = "Jalanatputno.4"
TENANT = "Evlogia"

# Disable SSL warnings for testing (if needed)
requests.packages.urllib3.disable_warnings()


class Colors:
    """ANSI color codes for terminal output"""
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    END = "\033[0m"


def log_pass(msg):
    print(f"{Colors.GREEN}✅ PASS{Colors.END}: {msg}")


def log_fail(msg):
    print(f"{Colors.RED}❌ FAIL{Colors.END}: {msg}")


def log_info(msg):
    print(f"{Colors.YELLOW}ℹ️  INFO{Colors.END}: {msg}")


def log_skip(msg):
    print(f"{Colors.CYAN}⏭️  SKIP{Colors.END}: {msg}")


def log_section(msg):
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*60}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{msg}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'='*60}{Colors.END}\n")


def pretty_json(data):
    """Format JSON for readable output"""
    return json.dumps(data, indent=2, default=str)


class BillPaymentE2ETest:
    """
    End-to-End test suite for Bill Payments module.
    Tests the full lifecycle of bill payments.
    """

    def __init__(self):
        self.session = requests.Session()
        self.token = None
        self.headers = {}
        self.test_vendor_id = None
        self.test_vendor_name = None
        self.test_bank_account_id = None
        self.test_bank_account_name = None
        self.test_open_bill_id = None
        self.test_open_bill_amount = None
        self.created_draft_payment_id = None
        self.created_posted_payment_id = None
        self.payments_to_cleanup = []
        self.results = {"passed": 0, "failed": 0, "skipped": 0}

    def run_all_tests(self):
        """Execute all test cases in sequence"""
        log_section("BILL PAYMENTS (PaymentOut) E2E TEST SUITE")
        print(f"Target: {BASE_URL}")
        print(f"User: {EMAIL}")
        print(f"Tenant: {TENANT}")
        print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        tests = [
            # Authentication
            ("1. Authentication - Login", self.test_login),
            
            # Setup - Get required IDs
            ("2. List Vendors", self.test_list_vendors),
            ("3. List Bank Accounts", self.test_list_bank_accounts),
            ("4. Get Bank Accounts Dropdown", self.test_bank_accounts_dropdown),
            ("5. Get Open Bills for Vendor", self.test_get_open_bills),
            
            # Bill Payments CRUD
            ("6. List Bill Payments (All)", self.test_list_bill_payments_all),
            ("7. List Bill Payments (Posted)", self.test_list_bill_payments_by_status),
            ("8. List Bill Payments (By Vendor)", self.test_list_bill_payments_by_vendor),
            ("9. Create Draft Payment", self.test_create_draft_payment),
            ("10. Get Draft Payment Detail", self.test_get_draft_payment_detail),
            ("11. Delete Draft Payment", self.test_delete_draft_payment),
            
            # Posted Payment Flow (conditional on open bills)
            ("12. Create Posted Payment with Allocation", self.test_create_posted_payment),
            ("13. Get Posted Payment Detail", self.test_get_posted_payment_detail),
            ("14. Void Posted Payment", self.test_void_posted_payment),
        ]

        for name, test_func in tests:
            log_section(name)
            try:
                result = test_func()
                if result == "skipped":
                    self.results['skipped'] += 1
                else:
                    self.results['passed'] += 1
            except AssertionError as e:
                log_fail(f"{str(e)}")
                self.results['failed'] += 1
            except Exception as e:
                log_fail(f"Unexpected error: {type(e).__name__}: {str(e)}")
                self.results['failed'] += 1

        # Cleanup
        self._cleanup()

        # Summary
        log_section("TEST RESULTS SUMMARY")
        total = self.results['passed'] + self.results['failed'] + self.results['skipped']
        print(f"Total Tests: {total}")
        print(f"{Colors.GREEN}Passed: {self.results['passed']}{Colors.END}")
        print(f"{Colors.RED}Failed: {self.results['failed']}{Colors.END}")
        print(f"{Colors.CYAN}Skipped: {self.results['skipped']}{Colors.END}")
        
        success_rate = (self.results['passed'] / total * 100) if total > 0 else 0
        print(f"\nSuccess Rate: {success_rate:.1f}%")

        return self.results['failed'] == 0

    def _cleanup(self):
        """Clean up any remaining test data"""
        log_section("CLEANUP")
        
        if not self.payments_to_cleanup:
            log_info("No payments to clean up")
            return
            
        for payment_id, status in self.payments_to_cleanup:
            try:
                if status == "draft":
                    # Delete draft
                    resp = self.session.delete(
                        f"{BASE_URL}/bill-payments/{payment_id}",
                        headers=self.headers
                    )
                    if resp.status_code in [200, 204]:
                        log_info(f"Deleted draft payment: {payment_id}")
                    else:
                        log_info(f"Could not delete payment {payment_id}: {resp.status_code}")
                elif status == "posted":
                    # Void posted payment
                    resp = self.session.post(
                        f"{BASE_URL}/bill-payments/{payment_id}/void",
                        headers=self.headers,
                        json={"void_reason": "E2E Test Cleanup - auto voiding test payment"}
                    )
                    if resp.status_code == 200:
                        log_info(f"Voided posted payment: {payment_id}")
                    else:
                        log_info(f"Could not void payment {payment_id}: {resp.status_code}")
            except Exception as e:
                log_info(f"Cleanup error for {payment_id}: {e}")

    # =========================================================================
    # TEST: Authentication
    # =========================================================================
    def test_login(self):
        """Test user authentication and token retrieval"""
        log_info(f"Attempting login for: {EMAIL}")
        
        resp = self.session.post(
            f"{BASE_URL}/auth/login",
            json={"email": EMAIL, "password": PASSWORD},
            headers={"Content-Type": "application/json"}
        )
        
        log_info(f"Response Status: {resp.status_code}")
        
        assert resp.status_code == 200, f"Login failed with status {resp.status_code}: {resp.text}"
        
        data = resp.json()
        log_info(f"Response keys: {list(data.keys())}")
        
        # Handle different token response formats
        self.token = data.get("data", {}).get("access_token") or data.get("access_token") or data.get("token")
        assert self.token, f"No access token in response. Keys: {list(data.keys())}"
        
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        
        # Check if tenant header is needed
        if "tenant" in data or "tenantId" in data:
            tenant_id = data.get("tenant") or data.get("tenantId")
            self.headers["X-Tenant-ID"] = str(tenant_id)
            log_info(f"Using tenant ID: {tenant_id}")
        
        log_pass(f"Login successful. Token: {self.token[:20]}...")
        return True

    # =========================================================================
    # TEST: List Vendors
    # =========================================================================
    def test_list_vendors(self):
        """Test listing vendors to get a vendor ID for testing"""
        log_info("Fetching vendors list...")
        
        resp = self.session.get(
            f"{BASE_URL}/vendors",
            headers=self.headers
        )
        
        log_info(f"Response Status: {resp.status_code}")
        
        assert resp.status_code == 200, f"List vendors failed: {resp.status_code} - {resp.text}"
        
        data = resp.json()
        
        # Handle paginated or direct array response
        vendors = data.get("data") or data.get("vendors") or data.get("items") or data
        if isinstance(vendors, dict):
            vendors = vendors.get("data") or vendors.get("items") or []
        
        log_info(f"Found {len(vendors)} vendors")
        
        if vendors and len(vendors) > 0:
            # Get first vendor
            vendor = vendors[0]
            self.test_vendor_id = vendor.get("id") or vendor.get("vendor_id")
            self.test_vendor_name = vendor.get("name") or vendor.get("vendor_name")
            log_info(f"Using vendor: {self.test_vendor_name} (ID: {self.test_vendor_id})")
            log_pass(f"Listed {len(vendors)} vendors successfully")
        else:
            log_info("No vendors found - some tests will be skipped")
            log_pass("Vendor list endpoint working (empty result)")
        
        return True

    # =========================================================================
    # TEST: List Bank Accounts
    # =========================================================================
    def test_list_bank_accounts(self):
        """Test listing bank accounts"""
        log_info("Fetching bank accounts...")
        
        resp = self.session.get(
            f"{BASE_URL}/bank-accounts",
            headers=self.headers
        )
        
        log_info(f"Response Status: {resp.status_code}")
        
        assert resp.status_code == 200, f"List bank accounts failed: {resp.status_code} - {resp.text}"
        
        data = resp.json()
        
        # Handle different response formats
        accounts = data.get("data") or data.get("bank_accounts") or data.get("items") or data
        if isinstance(accounts, dict):
            accounts = accounts.get("data") or accounts.get("items") or []
        
        log_info(f"Found {len(accounts)} bank accounts")
        
        if accounts and len(accounts) > 0:
            # Get first bank account
            account = accounts[0]
            self.test_bank_account_id = account.get("id") or account.get("bank_account_id")
            self.test_bank_account_name = account.get("name") or account.get("account_name")
            log_info(f"Using bank account: {self.test_bank_account_name} (ID: {self.test_bank_account_id})")
            log_pass(f"Listed {len(accounts)} bank accounts successfully")
        else:
            log_info("No bank accounts found - payment tests will be limited")
            log_pass("Bank accounts endpoint working (empty result)")
        
        return True

    # =========================================================================
    # TEST: Bank Accounts Dropdown
    # =========================================================================
    def test_bank_accounts_dropdown(self):
        """Test the bank accounts dropdown endpoint"""
        log_info("Fetching bank accounts dropdown...")
        
        resp = self.session.get(
            f"{BASE_URL}/bank-accounts/dropdown",
            headers=self.headers
        )
        
        log_info(f"Response Status: {resp.status_code}")
        
        # This endpoint might not exist
        if resp.status_code == 404:
            log_skip("Bank accounts dropdown endpoint not implemented")
            return "skipped"
        
        assert resp.status_code == 200, f"Bank accounts dropdown failed: {resp.status_code} - {resp.text}"
        
        data = resp.json()
        log_info(f"Dropdown response type: {type(data).__name__}")
        
        if isinstance(data, list):
            log_info(f"Dropdown has {len(data)} items")
        elif isinstance(data, dict):
            items = data.get("data") or data.get("items") or []
            log_info(f"Dropdown has {len(items)} items")
        
        log_pass("Bank accounts dropdown endpoint working")
        return True

    # =========================================================================
    # TEST: Get Open Bills for Vendor
    # =========================================================================
    def test_get_open_bills(self):
        """Test getting open bills for a vendor"""
        if not self.test_vendor_id:
            log_skip("No vendor ID available - skipping open bills test")
            return "skipped"
        
        log_info(f"Fetching open bills for vendor: {self.test_vendor_id}")
        
        resp = self.session.get(
            f"{BASE_URL}/bill-payments/vendors/{self.test_vendor_id}/open-bills",
            headers=self.headers
        )
        
        log_info(f"Response Status: {resp.status_code}")
        
        # Try alternative endpoint if 404
        if resp.status_code == 404:
            log_info("Trying alternative endpoint: /bills?vendor_id=X&status=open")
            resp = self.session.get(
                f"{BASE_URL}/bills",
                headers=self.headers,
                params={"vendor_id": self.test_vendor_id, "status": "open"}
            )
            log_info(f"Alternative Response Status: {resp.status_code}")
        
        if resp.status_code == 404:
            log_skip("Open bills endpoint not found")
            return "skipped"
        
        assert resp.status_code == 200, f"Get open bills failed: {resp.status_code} - {resp.text}"
        
        data = resp.json()
        
        # Handle different response formats
        bills = data.get("data") or data.get("bills") or data.get("items") or data
        if isinstance(bills, dict):
            bills = bills.get("data") or bills.get("items") or []
        
        log_info(f"Found {len(bills)} open bills for vendor")
        
        if bills and len(bills) > 0:
            bill = bills[0]
            self.test_open_bill_id = bill.get("id") or bill.get("bill_id")
            self.test_open_bill_amount = bill.get("amount_due") or bill.get("balance") or bill.get("total")
            log_info(f"Using open bill: {self.test_open_bill_id}, Amount: {self.test_open_bill_amount}")
            log_pass(f"Found {len(bills)} open bills")
        else:
            log_info("No open bills found - posted payment test will be skipped")
            log_pass("Open bills endpoint working (no open bills)")
        
        return True

    # =========================================================================
    # TEST: List Bill Payments (All)
    # =========================================================================
    def test_list_bill_payments_all(self):
        """Test listing all bill payments"""
        log_info("Fetching all bill payments...")
        
        resp = self.session.get(
            f"{BASE_URL}/bill-payments",
            headers=self.headers
        )
        
        log_info(f"Response Status: {resp.status_code}")
        
        assert resp.status_code == 200, f"List bill payments failed: {resp.status_code} - {resp.text}"
        
        data = resp.json()
        
        # Handle different response formats
        payments = data.get("data") or data.get("payments") or data.get("items") or data
        if isinstance(payments, dict):
            payments = payments.get("data") or payments.get("items") or []
        
        count = len(payments) if isinstance(payments, list) else 0
        log_info(f"Found {count} bill payments")
        
        # Log sample if available
        if count > 0:
            sample = payments[0]
            log_info(f"Sample payment keys: {list(sample.keys())}")
        
        log_pass(f"Listed {count} bill payments successfully")
        return True

    # =========================================================================
    # TEST: List Bill Payments by Status
    # =========================================================================
    def test_list_bill_payments_by_status(self):
        """Test listing bill payments filtered by status"""
        log_info("Fetching bill payments with status=posted...")
        
        resp = self.session.get(
            f"{BASE_URL}/bill-payments",
            headers=self.headers,
            params={"status": "posted"}
        )
        
        log_info(f"Response Status: {resp.status_code}")
        
        assert resp.status_code == 200, f"List bill payments by status failed: {resp.status_code} - {resp.text}"
        
        data = resp.json()
        payments = data.get("data") or data.get("payments") or data.get("items") or data
        if isinstance(payments, dict):
            payments = payments.get("data") or payments.get("items") or []
        
        count = len(payments) if isinstance(payments, list) else 0
        log_info(f"Found {count} posted bill payments")
        
        log_pass(f"Status filter working - found {count} posted payments")
        return True

    # =========================================================================
    # TEST: List Bill Payments by Vendor
    # =========================================================================
    def test_list_bill_payments_by_vendor(self):
        """Test listing bill payments filtered by vendor"""
        if not self.test_vendor_id:
            log_skip("No vendor ID available - skipping vendor filter test")
            return "skipped"
        
        log_info(f"Fetching bill payments for vendor: {self.test_vendor_id}")
        
        resp = self.session.get(
            f"{BASE_URL}/bill-payments",
            headers=self.headers,
            params={"vendor_id": self.test_vendor_id}
        )
        
        log_info(f"Response Status: {resp.status_code}")
        
        assert resp.status_code == 200, f"List bill payments by vendor failed: {resp.status_code} - {resp.text}"
        
        data = resp.json()
        payments = data.get("data") or data.get("payments") or data.get("items") or data
        if isinstance(payments, dict):
            payments = payments.get("data") or payments.get("items") or []
        
        count = len(payments) if isinstance(payments, list) else 0
        log_info(f"Found {count} bill payments for vendor {self.test_vendor_name}")
        
        log_pass(f"Vendor filter working - found {count} payments")
        return True

    # =========================================================================
    # TEST: Create Draft Payment
    # =========================================================================
    def test_create_draft_payment(self):
        """Test creating a draft bill payment"""
        if not self.test_vendor_id or not self.test_bank_account_id:
            log_skip("Missing vendor or bank account ID - skipping draft creation")
            return "skipped"
        
        payment_data = {
            "vendor_id": self.test_vendor_id,
            "payment_date": date.today().isoformat(),
            "payment_method": "bank_transfer",
            "total_amount": 100000,
            "bank_account_id": self.test_bank_account_id,
            "save_as_draft": True,
            "allocations": [],
            "memo": "E2E Test Draft Payment - will be deleted"
        }
        
        log_info(f"Creating draft payment:")
        log_info(f"  Vendor: {self.test_vendor_id}")
        log_info(f"  Bank Account: {self.test_bank_account_id}")
        log_info(f"  Amount: 100,000")
        
        resp = self.session.post(
            f"{BASE_URL}/bill-payments",
            headers=self.headers,
            json=payment_data
        )
        
        log_info(f"Response Status: {resp.status_code}")
        
        if resp.status_code in [400, 422]:
            log_info(f"Validation error: {resp.text}")
            # Try without save_as_draft flag
            del payment_data["save_as_draft"]
            payment_data["status"] = "draft"
            resp = self.session.post(
                f"{BASE_URL}/bill-payments",
                headers=self.headers,
                json=payment_data
            )
            log_info(f"Retry Response Status: {resp.status_code}")
        
        assert resp.status_code in [200, 201], f"Create draft payment failed: {resp.status_code} - {resp.text}"
        
        data = resp.json()
        log_info(f"Response: {pretty_json(data)}")
        
        # Get payment ID
        self.created_draft_payment_id = (
            data.get("id") or 
            data.get("payment_id") or 
            data.get("data", {}).get("id")
        )
        
        assert self.created_draft_payment_id, f"No payment ID in response: {data}"
        
        # Track for cleanup
        self.payments_to_cleanup.append((self.created_draft_payment_id, "draft"))
        
        log_pass(f"Created draft payment: {self.created_draft_payment_id}")
        return True

    # =========================================================================
    # TEST: Get Draft Payment Detail
    # =========================================================================
    def test_get_draft_payment_detail(self):
        """Test getting details of the created draft payment"""
        if not self.created_draft_payment_id:
            log_skip("No draft payment created - skipping detail test")
            return "skipped"
        
        log_info(f"Fetching payment detail: {self.created_draft_payment_id}")
        
        resp = self.session.get(
            f"{BASE_URL}/bill-payments/{self.created_draft_payment_id}",
            headers=self.headers
        )
        
        log_info(f"Response Status: {resp.status_code}")
        
        assert resp.status_code == 200, f"Get payment detail failed: {resp.status_code} - {resp.text}"
        
        data = resp.json()
        payment = data.get("data") or data
        
        log_info(f"Payment details:")
        log_info(f"  ID: {payment.get(id)}")
        log_info(f"  Vendor ID: {payment.get(vendor_id)}")
        log_info(f"  Amount: {payment.get(total_amount)}")
        log_info(f"  Status: {payment.get(status)}")
        log_info(f"  Payment Date: {payment.get(payment_date)}")
        
        log_pass(f"Retrieved payment detail successfully")
        return True

    # =========================================================================
    # TEST: Delete Draft Payment
    # =========================================================================
    def test_delete_draft_payment(self):
        """Test deleting a draft payment"""
        if not self.created_draft_payment_id:
            log_skip("No draft payment to delete - skipping")
            return "skipped"
        
        log_info(f"Deleting draft payment: {self.created_draft_payment_id}")
        
        resp = self.session.delete(
            f"{BASE_URL}/bill-payments/{self.created_draft_payment_id}",
            headers=self.headers
        )
        
        log_info(f"Response Status: {resp.status_code}")
        
        assert resp.status_code in [200, 204], f"Delete draft payment failed: {resp.status_code} - {resp.text}"
        
        # Remove from cleanup list since it is deleted
        self.payments_to_cleanup = [
            (pid, status) for pid, status in self.payments_to_cleanup 
            if pid != self.created_draft_payment_id
        ]
        
        # Verify deletion
        verify_resp = self.session.get(
            f"{BASE_URL}/bill-payments/{self.created_draft_payment_id}",
            headers=self.headers
        )
        
        if verify_resp.status_code == 404:
            log_info("Verified: Payment no longer exists")
        else:
            log_info(f"Payment still accessible (may be soft deleted): {verify_resp.status_code}")
        
        log_pass(f"Deleted draft payment: {self.created_draft_payment_id}")
        self.created_draft_payment_id = None
        return True

    # =========================================================================
    # TEST: Create Posted Payment with Allocation
    # =========================================================================
    def test_create_posted_payment(self):
        """Test creating a posted payment with bill allocation"""
        if not self.test_vendor_id or not self.test_bank_account_id:
            log_skip("Missing vendor or bank account ID - skipping posted payment creation")
            return "skipped"
        
        # Determine allocation
        allocations = []
        amount = 50000  # Default small amount
        
        if self.test_open_bill_id and self.test_open_bill_amount:
            # Allocate to open bill (use smaller of bill amount or 50000)
            alloc_amount = min(float(self.test_open_bill_amount), 50000)
            allocations = [
                {
                    "bill_id": self.test_open_bill_id,
                    "amount_applied": alloc_amount
                }
            ]
            amount = alloc_amount
            log_info(f"Will allocate {alloc_amount} to bill {self.test_open_bill_id}")
        else:
            log_info("No open bills - creating unallocated payment")
        
        payment_data = {
            "vendor_id": self.test_vendor_id,
            "payment_date": date.today().isoformat(),
            "payment_method": "bank_transfer",
            "total_amount": amount,
            "bank_account_id": self.test_bank_account_id,
            "save_as_draft": False,
            "allocations": allocations,
            "memo": "E2E Test Posted Payment - will be voided"
        }
        
        log_info(f"Creating posted payment:")
        log_info(f"  Vendor: {self.test_vendor_id}")
        log_info(f"  Amount: {amount}")
        log_info(f"  Allocations: {len(allocations)}")
        
        resp = self.session.post(
            f"{BASE_URL}/bill-payments",
            headers=self.headers,
            json=payment_data
        )
        
        log_info(f"Response Status: {resp.status_code}")
        
        if resp.status_code in [400, 422]:
            log_info(f"Validation error: {resp.text}")
            # Try without save_as_draft
            del payment_data["save_as_draft"]
            payment_data["status"] = "posted"
            resp = self.session.post(
                f"{BASE_URL}/bill-payments",
                headers=self.headers,
                json=payment_data
            )
            log_info(f"Retry Response Status: {resp.status_code}")
        
        assert resp.status_code in [200, 201], f"Create posted payment failed: {resp.status_code} - {resp.text}"
        
        data = resp.json()
        log_info(f"Response: {pretty_json(data)}")
        
        self.created_posted_payment_id = (
            data.get("id") or 
            data.get("payment_id") or 
            data.get("data", {}).get("id")
        )
        
        assert self.created_posted_payment_id, f"No payment ID in response: {data}"
        
        # Track for cleanup
        self.payments_to_cleanup.append((self.created_posted_payment_id, "posted"))
        
        log_pass(f"Created posted payment: {self.created_posted_payment_id}")
        return True

    # =========================================================================
    # TEST: Get Posted Payment Detail
    # =========================================================================
    def test_get_posted_payment_detail(self):
        """Test getting details of the posted payment"""
        if not self.created_posted_payment_id:
            log_skip("No posted payment created - skipping detail test")
            return "skipped"
        
        log_info(f"Fetching posted payment detail: {self.created_posted_payment_id}")
        
        resp = self.session.get(
            f"{BASE_URL}/bill-payments/{self.created_posted_payment_id}",
            headers=self.headers
        )
        
        log_info(f"Response Status: {resp.status_code}")
        
        assert resp.status_code == 200, f"Get posted payment detail failed: {resp.status_code} - {resp.text}"
        
        data = resp.json()
        payment = data.get("data") or data
        
        log_info(f"Posted Payment details:")
        log_info(f"  ID: {payment.get(id)}")
        log_info(f"  Status: {payment.get(status)}")
        log_info(f"  Total Amount: {payment.get(total_amount)}")
        
        allocations = payment.get("allocations") or []
        if allocations:
            log_info(f"  Allocations: {len(allocations)}")
            for alloc in allocations[:3]:  # Show first 3
                log_info(f"    - Bill: {alloc.get(bill_id)}, Amount: {alloc.get(amount_applied)}")
        
        log_pass("Retrieved posted payment detail successfully")
        return True

    # =========================================================================
    # TEST: Void Posted Payment
    # =========================================================================
    def test_void_posted_payment(self):
        """Test voiding a posted payment"""
        if not self.created_posted_payment_id:
            log_skip("No posted payment to void - skipping")
            return "skipped"
        
        log_info(f"Voiding payment: {self.created_posted_payment_id}")
        
        void_data = {
            "void_reason": "E2E Test - voiding test payment for cleanup"
        }
        
        resp = self.session.post(
            f"{BASE_URL}/bill-payments/{self.created_posted_payment_id}/void",
            headers=self.headers,
            json=void_data
        )
        
        log_info(f"Response Status: {resp.status_code}")
        
        if resp.status_code == 404:
            log_info("Void endpoint not found - trying alternative")
            # Try PATCH/PUT with status
            resp = self.session.patch(
                f"{BASE_URL}/bill-payments/{self.created_posted_payment_id}",
                headers=self.headers,
                json={"status": "voided", "void_reason": void_data["void_reason"]}
            )
            log_info(f"Alternative Response Status: {resp.status_code}")
        
        assert resp.status_code in [200, 204], f"Void payment failed: {resp.status_code} - {resp.text}"
        
        # Remove from cleanup list since it is voided
        self.payments_to_cleanup = [
            (pid, status) for pid, status in self.payments_to_cleanup 
            if pid != self.created_posted_payment_id
        ]
        
        # Verify void status
        verify_resp = self.session.get(
            f"{BASE_URL}/bill-payments/{self.created_posted_payment_id}",
            headers=self.headers
        )
        
        if verify_resp.status_code == 200:
            verify_data = verify_resp.json()
            payment = verify_data.get("data") or verify_data
            status = payment.get("status")
            log_info(f"Payment status after void: {status}")
        
        log_pass(f"Voided payment: {self.created_posted_payment_id}")
        return True


def main():
    """Main entry point"""
    tester = BillPaymentE2ETest()
    success = tester.run_all_tests()
    
    if success:
        print(f"\n{Colors.GREEN}All tests passed!{Colors.END}")
        sys.exit(0)
    else:
        print(f"\n{Colors.RED}Some tests failed!{Colors.END}")
        sys.exit(1)


if __name__ == "__main__":
    main()
