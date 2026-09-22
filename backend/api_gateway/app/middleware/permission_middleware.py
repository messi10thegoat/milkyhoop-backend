"""
Permission Middleware - Route-based RBAC Enforcement

Checks permissions based on URL pattern and HTTP method.
Applied globally to all routes matching the patterns.

IRON LAW COMPLIANCE:
- Law 0: Separation of Concerns - Permission layer separate from business logic
- Law 10: AI Safety - All permission decisions logged
- Law 12: Audit Immutability - Denials logged for audit
"""
import logging
import re
from typing import List, Tuple, Optional
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from backend.api_gateway.app.services.policy_engine_client import get_policy_engine

logger = logging.getLogger(__name__)


# Route patterns mapped to (module, action)
# Format: (url_pattern_regex, http_methods, module, action)
ROUTE_PERMISSIONS: List[Tuple[str, List[str], str, str]] = [
    # Sales Invoices
    (r"^/api/sales-invoices/summary$", ["GET"], "sales_invoice", "R"),
    (r"^/api/sales-invoices/calculate$", ["POST"], "sales_invoice", "R"),
    (r"^/api/sales-invoices$", ["GET"], "sales_invoice", "R"),
    (r"^/api/sales-invoices$", ["POST"], "sales_invoice", "C"),
    (r"^/api/sales-invoices/[^/]+$", ["GET"], "sales_invoice", "R"),
    (r"^/api/sales-invoices/[^/]+$", ["PATCH", "PUT"], "sales_invoice", "U"),
    (r"^/api/sales-invoices/[^/]+$", ["DELETE"], "sales_invoice", "D"),
    (r"^/api/sales-invoices/[^/]+/post$", ["POST"], "sales_invoice", "P"),
    (r"^/api/sales-invoices/[^/]+/void$", ["POST"], "sales_invoice", "V"),
    (r"^/api/sales-invoices/[^/]+/payments$", ["POST"], "receive_payment", "C"),
    (r"^/api/sales-invoices/[^/]+/pdf$", ["GET"], "sales_invoice", "E"),
    (r"^/api/sales-invoices/[^/]+/history$", ["GET"], "sales_invoice", "R"),
    (r"^/api/sales-invoices/[^/]+/activity$", ["GET"], "sales_invoice", "R"),
    (r"^/api/sales-invoices/[^/]+/journals$", ["GET"], "journal", "R"),
    # Bills / Purchase Invoices
    (r"^/api/bills/summary$", ["GET"], "purchase_invoice", "R"),
    (r"^/api/bills$", ["GET"], "purchase_invoice", "R"),
    (r"^/api/bills$", ["POST"], "purchase_invoice", "C"),
    (r"^/api/bills/[^/]+$", ["GET"], "purchase_invoice", "R"),
    (r"^/api/bills/[^/]+$", ["PATCH", "PUT"], "purchase_invoice", "U"),
    (r"^/api/bills/[^/]+$", ["DELETE"], "purchase_invoice", "D"),
    (r"^/api/bills/[^/]+/post$", ["POST"], "purchase_invoice", "P"),
    (r"^/api/bills/[^/]+/void$", ["POST"], "purchase_invoice", "V"),
    # Receive Payments
    (r"^/api/receive-payments/summary$", ["GET"], "receive_payment", "R"),
    (r"^/api/receive-payments$", ["GET"], "receive_payment", "R"),
    (r"^/api/receive-payments$", ["POST"], "receive_payment", "C"),
    (r"^/api/receive-payments/[^/]+$", ["GET"], "receive_payment", "R"),
    (r"^/api/receive-payments/[^/]+$", ["PATCH", "PUT"], "receive_payment", "U"),
    (r"^/api/receive-payments/[^/]+$", ["DELETE"], "receive_payment", "D"),
    (r"^/api/receive-payments/[^/]+/post$", ["POST"], "receive_payment", "P"),
    (r"^/api/receive-payments/[^/]+/void$", ["POST"], "receive_payment", "V"),
    # Bill Payments / Send Payments
    (r"^/api/bill-payments/summary$", ["GET"], "send_payment", "R"),
    (r"^/api/bill-payments$", ["GET"], "send_payment", "R"),
    (r"^/api/bill-payments$", ["POST"], "send_payment", "C"),
    (r"^/api/bill-payments/[^/]+$", ["GET"], "send_payment", "R"),
    (r"^/api/bill-payments/[^/]+$", ["PATCH", "PUT"], "send_payment", "U"),
    (r"^/api/bill-payments/[^/]+$", ["DELETE"], "send_payment", "D"),
    (r"^/api/bill-payments/[^/]+/post$", ["POST"], "send_payment", "P"),
    (r"^/api/bill-payments/[^/]+/void$", ["POST"], "send_payment", "V"),
    # Customers
    (r"^/api/customers/summary$", ["GET"], "customer", "R"),
    (r"^/api/customers$", ["GET"], "customer", "R"),
    (r"^/api/customers$", ["POST"], "customer", "C"),
    (r"^/api/customers/[^/]+$", ["GET"], "customer", "R"),
    (r"^/api/customers/[^/]+$", ["PATCH", "PUT"], "customer", "U"),
    (r"^/api/customers/[^/]+$", ["DELETE"], "customer", "D"),
    # Vendors / Suppliers
    (r"^/api/vendors/summary$", ["GET"], "supplier", "R"),
    (r"^/api/vendors$", ["GET"], "supplier", "R"),
    (r"^/api/vendors$", ["POST"], "supplier", "C"),
    (r"^/api/vendors/[^/]+$", ["GET"], "supplier", "R"),
    (r"^/api/vendors/[^/]+$", ["PATCH", "PUT"], "supplier", "U"),
    (r"^/api/vendors/[^/]+$", ["DELETE"], "supplier", "D"),
    # Items / Products
    (r"^/api/items/summary$", ["GET"], "item", "R"),
    (r"^/api/items$", ["GET"], "item", "R"),
    (r"^/api/items$", ["POST"], "item", "C"),
    (r"^/api/items/[^/]+$", ["GET"], "item", "R"),
    (r"^/api/items/[^/]+$", ["PATCH", "PUT"], "item", "U"),
    (r"^/api/items/[^/]+$", ["DELETE"], "item", "D"),
    (r"^/api/products", ["GET", "POST", "PATCH", "PUT", "DELETE"], "item", "R"),
    # Expenses
    (r"^/api/expenses/summary$", ["GET"], "expense", "R"),
    (r"^/api/expenses$", ["GET"], "expense", "R"),
    (r"^/api/expenses$", ["POST"], "expense", "C"),
    (r"^/api/expenses/[^/]+$", ["GET"], "expense", "R"),
    (r"^/api/expenses/[^/]+$", ["PATCH", "PUT"], "expense", "U"),
    (r"^/api/expenses/[^/]+$", ["DELETE"], "expense", "D"),
    # --- Sub-jalur beban — DITAMBAHKAN 12 Sep 2026 -------------------
    # Lima modul lain sudah menutup pasangan {id}/post + {id}/void
    # (sales-invoices, bills, receive-payments, bill-payments, payroll).
    # Beban SATU-SATUNYA yang terlewat, sehingga dua endpoint PENULIS
    # JURNAL bisa dipanggil siapa pun yang terautentikasi di tenant --
    # termasuk peran Viewer. Terukur sebelum perbaikan: akun tanpa izin
    # EXPENSE menembus sampai ke handler (404 'Expense not found'),
    # sementara PATCH/DELETE menolak 403 dengan benar.
    (r"^/api/expenses/[^/]+/post$", ["POST"], "expense", "P"),
    (r"^/api/expenses/[^/]+/void$", ["POST"], "expense", "V"),
    (r"^/api/expenses/[^/]+/attachments$", ["POST"], "expense", "U"),
    # ================================================================
    # Sub-jalur & rute tulis yang TAK PERNAH terjaga — DITAMBAHKAN 12 Sep 2026.
    # Sumber: backend/docs/TEMUAN-sapuan-izin-20260912.md (sapuan dua arah).
    # Huruf aksi diturunkan dari PERILAKU handler (sumber utuh via inspect),
    # bukan dari ejaan jalur -- `preview-journal` terbukti BACA-SAJA meski
    # namanya mengandung "journal".
    # ⚠️ Modul bertanda (baru) belum punya baris role_permissions, jadi ia
    #    GAGAL-TERTUTUP: non-owner ditolak sampai pemilik memberi hibah.
    # ----------------------------------------------------------------
    # pratinjau = BACA (nol INSERT/UPDATE/DELETE; 2 dari 5 mendokumentasikannya)
    (r"^/api/bills/preview-journal$", ["POST"], "purchase_invoice", "R"),
    (r"^/api/bill-payments/preview-journal$", ["POST"], "send_payment", "R"),
    (r"^/api/expenses/preview-journal$", ["POST"], "expense", "R"),
    (r"^/api/sales-invoices/preview-journal$", ["POST"], "sales_invoice", "R"),
    (r"^/api/receive-payments/preview-journal$", ["POST"], "receive_payment", "R"),
    # penulis jurnal terkonfirmasi
    (r"^/api/journals/[^/]+$", ["DELETE"], "journal", "D"),
    (r"^/api/bank-transfers/[^/]+/post$", ["POST"], "kas_bank", "P"),
    (r"^/api/bank-transfers/[^/]+/void$", ["POST"], "kas_bank", "V"),
    (r"^/api/bank-transactions/[^/]+/void$", ["POST"], "kas_bank", "V"),
    (r"^/api/sales-receipts/[^/]+/void$", ["POST"], "receive_payment", "V"),
    (r"^/api/customers/[^/]+/opening-balance/reverse$", ["POST"], "customer", "V"),
    (r"^/api/payroll-payments$", ["POST"], "payroll", "C"),
    (r"^/api/payroll-payments/[^/]+/post$", ["POST"], "payroll", "P"),
    (r"^/api/payroll-payments/[^/]+/void$", ["POST"], "payroll", "V"),
    (r"^/api/production/month-end-reconcile$", ["POST"], "journal", "P"),
    (r"^/api/production/month-end-reconcile/[^/]+/void$", ["POST"], "journal", "V"),
    (r"^/api/fixed-assets/post-depreciation$", ["POST"], "fixed_asset", "P"),  # (baru)
    # menulis lewat DELEGASI ke service (tulis=0 di handler, tapi bukan baca)
    (r"^/api/bills/[^/]+/payments$", ["POST"], "send_payment", "C"),
    (r"^/api/sales-invoices/[^/]+/fulfill$", ["POST"], "sales_invoice", "P"),
    (r"^/api/payment-requests/[^/]+/mark-paid$", ["POST"], "payment_request", "P"),
    (r"^/api/payment-requests/[^/]+/cancel$", ["POST"], "payment_request", "U"),
    # menulis data, tanpa jurnal
    (r"^/api/fiscal-years/[^/]+/close$", ["POST"], "period", "P"),
    (r"^/api/purchase-orders/[^/]+/close$", ["POST"], "purchase_order", "U"),
    (r"^/api/approval-requests/[^/]+/approve$", ["POST"], "approval_inbox", "A"),
    (r"^/api/expense-claims/[^/]+/approve$", ["POST"], "expense", "A"),
    (r"^/api/budgets/[^/]+/approve$", ["POST"], "budget", "A"),  # (baru)
    (r"^/api/budgets/[^/]+/close$", ["POST"], "budget", "U"),  # (baru)
    (r"^/api/intercompany/reconcile$", ["POST"], "intercompany", "U"),  # (baru)
    (r"^/api/branches/transfers/[^/]+/settle$", ["POST"], "warehouse", "U"),
    (r"^/api/tables/sessions/[^/]+/close$", ["POST"], "tables", "U"),  # (baru)
    # ⚠️ BELUM DIPASTIKAN: 0 tulis, 0 delegasi terdeteksi dari 72 baris sumber.
    #    Diberi R (paling tak membatasi). Kalau ternyata ia menulis, naikkan.
    (r"^/api/bank-reconciliation/sessions/[^/]+/agentic-reconcile$", ["POST"], "kas_bank", "R"),
    # ================================================================
    # Payroll
    (r"^/api/payroll/summary$", ["GET"], "payroll", "R"),
    (r"^/api/payroll$", ["GET"], "payroll", "R"),
    (r"^/api/payroll$", ["POST"], "payroll", "C"),
    (r"^/api/payroll/[^/]+$", ["GET"], "payroll", "R"),
    (r"^/api/payroll/[^/]+$", ["PATCH", "PUT"], "payroll", "U"),
    (r"^/api/payroll/[^/]+$", ["DELETE"], "payroll", "D"),
    (r"^/api/payroll/[^/]+/submit$", ["POST"], "payroll", "U"),
    (r"^/api/payroll/[^/]+/approve$", ["POST"], "payroll", "A"),
    (r"^/api/payroll/[^/]+/reject$", ["POST"], "payroll", "A"),
    (r"^/api/payroll/[^/]+/post$", ["POST"], "payroll", "P"),
    (r"^/api/payroll/[^/]+/void$", ["POST"], "payroll", "V"),
    (r"^/api/payroll/[^/]+/journal-entries$", ["GET"], "payroll", "R"),
    (r"^/api/payroll/[^/]+/allocations$", ["GET"], "payroll", "R"),
    # Reports - require Export permission
    (r"^/api/reports", ["GET"], "reports", "R"),
    (r"^/api/reports/.*/export$", ["GET", "POST"], "reports", "E"),
    # Journals
    (r"^/api/journals$", ["GET"], "journal", "R"),
    (r"^/api/journals$", ["POST"], "journal", "C"),
    (r"^/api/journals/[^/]+$", ["GET"], "journal", "R"),
    (r"^/api/journals/[^/]+/post$", ["POST"], "journal", "P"),
    (r"^/api/journals/[^/]+/reverse$", ["POST"], "journal", "V"),
    # =========================================================================
    # Customer & Vendor Deposits (Uang Muka) — B1 security fix.
    # These routes were UNMAPPED = unguarded: an unmapped route falls through the
    # middleware (fail-open), so ANY authenticated user could apply/refund/void a
    # deposit. Mapping them moves deposits onto the policy-engine (fail-closed).
    #
    # ACTION-CHOICE RATIONALE — do NOT "simplify" these; each is deliberate:
    #   refund -> V (NOT C/P): a refund is a NEW cash-OUT transaction. It is gated
    #       at the void tier ON PURPOSE — paying money out must require more
    #       privilege than receiving it. Downgrading refund to C or P silently
    #       weakens the cash-out gate.
    #   apply  -> P: applying a deposit POSTS a DEPOSIT_APPLICATION journal, so it
    #       is a posting action (same tier as posting an invoice/payment).
    #   post   -> P: draft -> posted is also a posting action.
    #   void / reverse-application -> V ; create (terima) -> C ; read -> R ;
    #   update draft -> U ; delete draft -> D.
    #   A (approve) is intentionally NOT used: the deposit lifecycle has no
    #   /approve endpoint (verified — customer: post/apply/refund/void/reverse;
    #   vendor: post/apply/refund/void).
    #
    # CONSEQUENCE: OWNER keeps access (unconditional policy-engine bypass); ALL
    # non-owner roles (incl. admin/accountant business roles) are DENIED until
    # CUSTOMER_DEPOSIT / VENDOR_DEPOSIT grants are seeded (Tim & Akses UI = follow-up).
    # This is the intended security posture, not a regression.
    (r"^/api/customer-deposits$", ["POST"], "customer_deposit", "C"),
    (r"^/api/customer-deposits$", ["GET"], "customer_deposit", "R"),
    (r"^/api/sales-invoices/[^/]+/deposit-plan$", ["GET"], "sales_invoice", "R"),
    (r"^/api/customer-deposits/summary$", ["GET"], "customer_deposit", "R"),
    (r"^/api/customer-deposits/customer/[^/]+$", ["GET"], "customer_deposit", "R"),
    (r"^/api/customer-deposits/[^/]+/pdf$", ["GET"], "customer_deposit", "R"),
    (r"^/api/customer-deposits/[^/]+/post$", ["POST"], "customer_deposit", "P"),
    (r"^/api/customer-deposits/[^/]+/apply$", ["POST"], "customer_deposit", "P"),
    (r"^/api/customer-deposits/[^/]+/applications/[^/]+/reverse$", ["POST"], "customer_deposit", "V"),
    (r"^/api/customer-deposits/[^/]+/refund$", ["POST"], "customer_deposit", "V"),
    (r"^/api/customer-deposits/[^/]+/void$", ["POST"], "customer_deposit", "V"),
    (r"^/api/customer-deposits/[^/]+$", ["GET"], "customer_deposit", "R"),
    (r"^/api/customer-deposits/[^/]+$", ["PATCH", "PUT"], "customer_deposit", "U"),
    (r"^/api/customer-deposits/[^/]+$", ["DELETE"], "customer_deposit", "D"),
    (r"^/api/vendor-deposits$", ["POST"], "vendor_deposit", "C"),
    (r"^/api/vendor-deposits$", ["GET"], "vendor_deposit", "R"),
    (r"^/api/vendor-deposits/summary$", ["GET"], "vendor_deposit", "R"),
    (r"^/api/vendor-deposits/by-vendor/[^/]+$", ["GET"], "vendor_deposit", "R"),
    (r"^/api/vendor-deposits/available/[^/]+$", ["GET"], "vendor_deposit", "R"),
    (r"^/api/vendor-deposits/[^/]+/post$", ["POST"], "vendor_deposit", "P"),
    (r"^/api/vendor-deposits/[^/]+/apply$", ["POST"], "vendor_deposit", "P"),
    (r"^/api/vendor-deposits/[^/]+/refund$", ["POST"], "vendor_deposit", "V"),
    (r"^/api/vendor-deposits/[^/]+/void$", ["POST"], "vendor_deposit", "V"),
    (r"^/api/vendor-deposits/[^/]+$", ["GET"], "vendor_deposit", "R"),
    (r"^/api/vendor-deposits/[^/]+$", ["PATCH", "PUT"], "vendor_deposit", "U"),
    (r"^/api/vendor-deposits/[^/]+$", ["DELETE"], "vendor_deposit", "D"),
    # Chart of Accounts
    (r"^/api/accounts$", ["GET"], "chart_of_accounts", "R"),
    (r"^/api/accounts$", ["POST"], "chart_of_accounts", "C"),
    (r"^/api/accounts/[^/]+$", ["GET"], "chart_of_accounts", "R"),
    (r"^/api/accounts/[^/]+$", ["PATCH", "PUT"], "chart_of_accounts", "U"),
    (r"^/api/accounts/[^/]+$", ["DELETE"], "chart_of_accounts", "D"),
    # Bank Accounts (Kas & Bank)
    (r"^/api/bank-accounts", ["GET"], "kas_bank", "R"),
    (r"^/api/bank-accounts", ["POST"], "kas_bank", "C"),
    (r"^/api/bank-accounts/[^/]+", ["GET"], "kas_bank", "R"),
    (r"^/api/bank-accounts/[^/]+", ["PATCH", "PUT"], "kas_bank", "U"),
    (r"^/api/bank-accounts/[^/]+", ["DELETE"], "kas_bank", "D"),
    # Team Management
    (r"^/api/team-members$", ["GET"], "team_management", "R"),
    # DIPERBAIKI 12 Sep: tak ada POST /api/team-members. Rute nyatanya
    # /invite -- pintu masuk SETIAP anggota berikutnya, jadi celah di sini
    # mengalikan celah lainnya.
    (r"^/api/team-members/invite$", ["POST"], "team_management", "C"),
    (r"^/api/team-members/invitations/[^/]+/resend$", ["POST"], "team_management", "C"),
    (r"^/api/team-members/[^/]+$", ["GET"], "team_management", "R"),
    (r"^/api/team-members/[^/]+$", ["PATCH", "PUT"], "team_management", "U"),
    (r"^/api/team-members/[^/]+$", ["DELETE"], "team_management", "D"),
    # Payment Requests
    (r"^/api/payment-requests$", ["GET"], "payment_request", "R"),
    (r"^/api/payment-requests$", ["POST"], "payment_request", "C"),
    (r"^/api/payment-requests/[^/]+$", ["GET"], "payment_request", "R"),
    (r"^/api/payment-requests/[^/]+$", ["PATCH", "PUT"], "payment_request", "U"),
    (r"^/api/payment-requests/[^/]+$", ["DELETE"], "payment_request", "D"),
    (r"^/api/payment-requests/[^/]+/approve$", ["POST"], "payment_request", "A"),
    (r"^/api/payment-requests/[^/]+/reject$", ["POST"], "payment_request", "A"),
    # Approval Inbox
    # Employees
    (r"^/api/employees$", ["GET"], "employee", "R"),
    (r"^/api/employees$", ["POST"], "employee", "C"),
    (r"^/api/employees/[^/]+$", ["GET"], "employee", "R"),
    (r"^/api/employees/[^/]+$", ["PATCH", "PUT"], "employee", "U"),
    (r"^/api/employees/[^/]+$", ["DELETE"], "employee", "D"),
    # Salary Components
    (r"^/api/salary-components", ["GET"], "salary_component", "R"),
    (r"^/api/salary-components", ["POST"], "salary_component", "C"),
    (r"^/api/salary-components/[^/]+", ["PATCH", "PUT"], "salary_component", "U"),
    (r"^/api/salary-components/[^/]+", ["DELETE"], "salary_component", "D"),
    # BPJS
    # DIPERBAIKI 12 Sep: rute nyata /api/payroll-config/bpjs, bukan /api/bpjs.
    (r"^/api/payroll-config/bpjs$", ["GET"], "bpjs", "R"),
    (r"^/api/payroll-config/bpjs$", ["POST", "PATCH", "PUT"], "bpjs", "U"),
    # Pay Groups
    (r"^/api/pay-groups", ["GET"], "pay_group", "R"),
    (r"^/api/pay-groups", ["POST"], "pay_group", "C"),
    (r"^/api/pay-groups/[^/]+", ["PATCH", "PUT"], "pay_group", "U"),
    (r"^/api/pay-groups/[^/]+", ["DELETE"], "pay_group", "D"),
    # Quotes
    (r"^/api/quotes", ["GET"], "quote", "R"),
    (r"^/api/quotes", ["POST"], "quote", "C"),
    (r"^/api/quotes/[^/]+", ["GET"], "quote", "R"),
    (r"^/api/quotes/[^/]+", ["PATCH", "PUT"], "quote", "U"),
    (r"^/api/quotes/[^/]+", ["DELETE"], "quote", "D"),
    # Sales Orders
    (r"^/api/sales-orders", ["GET"], "sales_order", "R"),
    (r"^/api/sales-orders", ["POST"], "sales_order", "C"),
    (r"^/api/sales-orders/[^/]+", ["GET"], "sales_order", "R"),
    (r"^/api/sales-orders/[^/]+", ["PATCH", "PUT"], "sales_order", "U"),
    (r"^/api/sales-orders/[^/]+", ["DELETE"], "sales_order", "D"),
    # Credit Notes
    (r"^/api/credit-notes", ["GET"], "credit_note", "R"),
    (r"^/api/credit-notes", ["POST"], "credit_note", "C"),
    (r"^/api/credit-notes/[^/]+", ["GET"], "credit_note", "R"),
    (r"^/api/credit-notes/[^/]+", ["PATCH", "PUT"], "credit_note", "U"),
    (r"^/api/credit-notes/[^/]+", ["DELETE"], "credit_note", "D"),
    # Vendor Credits / Debit Notes
    (r"^/api/vendor-credits", ["GET"], "debit_note", "R"),
    (r"^/api/vendor-credits", ["POST"], "debit_note", "C"),
    (r"^/api/vendor-credits/[^/]+", ["GET"], "debit_note", "R"),
    (r"^/api/vendor-credits/[^/]+", ["PATCH", "PUT"], "debit_note", "U"),
    (r"^/api/vendor-credits/[^/]+", ["DELETE"], "debit_note", "D"),
    # Units
    (r"^/api/units", ["GET"], "unit", "R"),
    (r"^/api/units", ["POST"], "unit", "C"),
    (r"^/api/units/[^/]+", ["PATCH", "PUT"], "unit", "U"),
    (r"^/api/units/[^/]+", ["DELETE"], "unit", "D"),
    # Stock Adjustments
    (r"^/api/stock-adjustments", ["GET"], "stock_adjust", "R"),
    (r"^/api/stock-adjustments", ["POST"], "stock_adjust", "C"),
    (r"^/api/stock-adjustments/[^/]+", ["GET"], "stock_adjust", "R"),
    # Warehouses
    (r"^/api/warehouses", ["GET"], "warehouse", "R"),
    (r"^/api/warehouses", ["POST"], "warehouse", "C"),
    (r"^/api/warehouses/[^/]+", ["PATCH", "PUT"], "warehouse", "U"),
    (r"^/api/warehouses/[^/]+", ["DELETE"], "warehouse", "D"),
    # Manufacturing
    (r"^/api/bom", ["GET"], "bom", "R"),
    (r"^/api/bom", ["POST"], "bom", "C"),
    (r"^/api/bom/[^/]+", ["GET"], "bom", "R"),
    (r"^/api/bom/[^/]+", ["PATCH", "PUT"], "bom", "U"),
    (r"^/api/bom/[^/]+", ["DELETE"], "bom", "D"),
    (r"^/api/production/work-orders", ["GET"], "work_order", "R"),
    (r"^/api/production/work-orders", ["POST"], "work_order", "C"),
    (r"^/api/production/work-orders/[^/]+", ["GET"], "work_order", "R"),
    (r"^/api/production/work-orders/[^/]+", ["PATCH", "PUT"], "work_order", "U"),
    (r"^/api/production/work-centers", ["GET"], "work_center", "R"),
    (r"^/api/production/work-centers", ["POST"], "work_center", "C"),
    (r"^/api/production/work-centers/[^/]+", ["PATCH", "PUT"], "work_center", "U"),
    (r"^/api/production/material-issues", ["GET"], "material_issue", "R"),
    (r"^/api/production/material-issues", ["POST"], "material_issue", "C"),
    (r"^/api/production/material-issues/[^/]+", ["GET"], "material_issue", "R"),
    (r"^/api/production/fg-receipts", ["GET"], "fg_receipt", "R"),
    (r"^/api/production/fg-receipts", ["POST"], "fg_receipt", "C"),
    (r"^/api/production/fg-receipts/[^/]+", ["GET"], "fg_receipt", "R"),
    # Ledger
    (r"^/api/ledger", ["GET"], "ledger", "R"),
    # Periods
    (r"^/api/periods", ["GET"], "period", "R"),
    (r"^/api/periods", ["POST"], "period", "C"),
    (r"^/api/periods/[^/]+/close", ["POST"], "period", "P"),
    # Tax
    (r"^/api/tax", ["GET"], "tax", "R"),
    (r"^/api/tax", ["POST"], "tax", "C"),
    (r"^/api/tax/[^/]+", ["PATCH", "PUT"], "tax", "U"),
    # Settings
    (r"^/api/settings", ["GET"], "tenant_settings", "R"),
    (r"^/api/settings", ["PATCH", "PUT"], "tenant_settings", "U"),
    (r"^/api/tenant/profile$", ["PUT", "PATCH"], "tenant_settings", "U"),
    (r"^/api/tenant/profile/logo$", ["POST", "DELETE"], "tenant_settings", "U"),
    # AR/AP Aging
    (r"^/api/ar", ["GET"], "ar_aging", "R"),
    (r"^/api/aging/ar", ["GET"], "ar_aging", "R"),
    (r"^/api/aging/ap", ["GET"], "send_payment", "R"),
    (r"^/api/approval-inbox", ["GET"], "approval_inbox", "R"),
    (r"^/api/approvals", ["GET"], "approval_inbox", "R"),
    # === 14 Sep 2026 sweep izin TAHAP 2: tulis modul pemindah uang yang dulu TANPA pola (lolos tanpa cek). ===
    # Dibangkitkan dari aturan tertulis (putusan pemilik: pakai 15 modul DB yang ada) oleh scripts/bangkit_pola_tahap2.py;
    # aksi: POST->C PUT/PATCH->U DELETE->D, verba jalur void|cancel|reject|bounce->V approve|confirm->A post|complete|...->P
    # export->E calculate|validate|preview->R. chat & document_intake SENGAJA belum dipola (otorisasi modul tujuan, terbuka).
    # bank_reconciliation
    (r"^/api/bank-reconciliation/sessions$", ["POST"], "kas_bank", "C"),
    (r"^/api/bank-reconciliation/sessions/[^/]+/auto-match$", ["POST"], "kas_bank", "P"),
    (r"^/api/bank-reconciliation/sessions/[^/]+/cancel$", ["POST"], "kas_bank", "V"),
    (r"^/api/bank-reconciliation/sessions/[^/]+/categorize$", ["POST"], "kas_bank", "P"),
    (r"^/api/bank-reconciliation/sessions/[^/]+/complete$", ["POST"], "kas_bank", "P"),
    (r"^/api/bank-reconciliation/sessions/[^/]+/import$", ["POST"], "kas_bank", "P"),
    (r"^/api/bank-reconciliation/sessions/[^/]+/match$", ["POST"], "kas_bank", "P"),
    (r"^/api/bank-reconciliation/sessions/[^/]+/match/[^/]+$", ["DELETE"], "kas_bank", "D"),
    (r"^/api/bank-reconciliation/sessions/[^/]+/transactions$", ["POST"], "kas_bank", "C"),
    # bank_transfers
    (r"^/api/bank-transfers$", ["POST"], "kas_bank", "C"),
    (r"^/api/bank-transfers/[^/]+$", ["DELETE"], "kas_bank", "D"),
    (r"^/api/bank-transfers/[^/]+$", ["PATCH"], "kas_bank", "U"),
    # bills
    (r"^/api/bills/[^/]+/attachments$", ["POST"], "purchase_invoice", "C"),
    (r"^/api/bills/[^/]+/attachments/[^/]+$", ["DELETE"], "purchase_invoice", "D"),
    (r"^/api/bills/[^/]+/mark-paid$", ["PATCH"], "purchase_invoice", "P"),
    (r"^/api/bills/calculate$", ["POST"], "purchase_invoice", "R"),
    (r"^/api/bills/v2$", ["POST"], "purchase_invoice", "C"),
    (r"^/api/bills/v2/[^/]+$", ["PATCH"], "purchase_invoice", "U"),
    # budgets
    (r"^/api/budgets$", ["POST"], "reports", "C"),
    (r"^/api/budgets/[^/]+$", ["DELETE"], "reports", "D"),
    (r"^/api/budgets/[^/]+$", ["PATCH"], "reports", "U"),
    (r"^/api/budgets/[^/]+/activate$", ["POST"], "reports", "P"),
    (r"^/api/budgets/[^/]+/duplicate$", ["POST"], "reports", "P"),
    (r"^/api/budgets/[^/]+/items$", ["POST"], "reports", "C"),
    (r"^/api/budgets/[^/]+/items/[^/]+$", ["DELETE"], "reports", "D"),
    # cheques
    (r"^/api/cheques/[^/]+$", ["DELETE"], "kas_bank", "D"),
    (r"^/api/cheques/[^/]+$", ["PATCH"], "kas_bank", "U"),
    (r"^/api/cheques/[^/]+/bounce$", ["POST"], "kas_bank", "V"),
    (r"^/api/cheques/[^/]+/cancel$", ["POST"], "kas_bank", "V"),
    (r"^/api/cheques/[^/]+/clear$", ["POST"], "kas_bank", "P"),
    (r"^/api/cheques/[^/]+/deposit$", ["POST"], "kas_bank", "P"),
    (r"^/api/cheques/issue$", ["POST"], "kas_bank", "C"),
    (r"^/api/cheques/receive$", ["POST"], "kas_bank", "P"),
    # consolidation
    (r"^/api/consolidation/entities/[^/]+$", ["DELETE"], "journal", "D"),
    (r"^/api/consolidation/entities/[^/]+$", ["PATCH"], "journal", "U"),
    (r"^/api/consolidation/groups$", ["POST"], "journal", "C"),
    (r"^/api/consolidation/groups/[^/]+$", ["DELETE"], "journal", "D"),
    (r"^/api/consolidation/groups/[^/]+$", ["PATCH"], "journal", "U"),
    (r"^/api/consolidation/groups/[^/]+/auto-map$", ["POST"], "journal", "P"),
    (r"^/api/consolidation/groups/[^/]+/entities$", ["POST"], "journal", "C"),
    (r"^/api/consolidation/groups/[^/]+/intercompany$", ["POST"], "journal", "P"),
    (r"^/api/consolidation/groups/[^/]+/mappings$", ["POST"], "journal", "C"),
    (r"^/api/consolidation/runs$", ["POST"], "journal", "C"),
    (r"^/api/consolidation/runs/[^/]+/process$", ["POST"], "journal", "P"),
    # customer_deposits
    (r"^/api/customer-deposits/[^/]+/attachments$", ["POST"], "customer", "C"),
    (r"^/api/customer-deposits/[^/]+/attachments/[^/]+$", ["DELETE"], "customer", "D"),
    # customers
    (r"^/api/customers/[^/]+/opening-balance$", ["POST"], "customer", "C"),
    (r"^/api/customers/[^/]+/reactivate$", ["PATCH"], "customer", "P"),
    (r"^/api/customers/merge$", ["POST"], "customer", "C"),
    (r"^/api/customers/merge/preview$", ["POST"], "customer", "R"),
    # efaktur
    (r"^/api/efaktur/export$", ["POST"], "tax", "E"),
    (r"^/api/efaktur/validate$", ["POST"], "tax", "R"),
    # expense_extended
    (r"^/api/expense-claims$", ["POST"], "expense", "C"),
    (r"^/api/expense-claims/[^/]+$", ["DELETE"], "expense", "D"),
    (r"^/api/expense-claims/[^/]+/reimburse$", ["POST"], "expense", "P"),
    (r"^/api/expense-claims/[^/]+/reject$", ["POST"], "expense", "V"),
    (r"^/api/expense-claims/[^/]+/submit$", ["POST"], "expense", "P"),
    (r"^/api/expense-policy$", ["POST"], "expense", "C"),
    (r"^/api/expense-policy$", ["PUT"], "expense", "U"),
    (r"^/api/recurring-expenses$", ["POST"], "expense", "C"),
    (r"^/api/recurring-expenses/[^/]+$", ["DELETE"], "expense", "D"),
    (r"^/api/recurring-expenses/[^/]+$", ["PUT"], "expense", "U"),
    (r"^/api/recurring-expenses/[^/]+/toggle$", ["POST"], "expense", "P"),
    # expenses
    (r"^/api/expenses/[^/]+/attachments/[^/]+$", ["DELETE"], "expense", "D"),
    (r"^/api/expenses/calculate$", ["POST"], "expense", "R"),
    # fiscal_years
    (r"^/api/fiscal-years$", ["POST"], "journal", "C"),
    # fixed_assets
    (r"^/api/fixed-assets$", ["POST"], "journal", "C"),
    (r"^/api/fixed-assets/[^/]+$", ["DELETE"], "journal", "D"),
    (r"^/api/fixed-assets/[^/]+$", ["PATCH"], "journal", "U"),
    (r"^/api/fixed-assets/[^/]+/activate$", ["POST"], "journal", "P"),
    (r"^/api/fixed-assets/[^/]+/dispose$", ["POST"], "journal", "P"),
    (r"^/api/fixed-assets/[^/]+/maintenance$", ["POST"], "journal", "P"),
    (r"^/api/fixed-assets/[^/]+/sell$", ["POST"], "journal", "P"),
    (r"^/api/fixed-assets/categories$", ["POST"], "journal", "C"),
    (r"^/api/fixed-assets/categories/[^/]+$", ["DELETE"], "journal", "D"),
    (r"^/api/fixed-assets/categories/[^/]+$", ["PATCH"], "journal", "U"),
    # intercompany
    (r"^/api/intercompany/transactions$", ["POST"], "journal", "C"),
    (r"^/api/intercompany/transactions/[^/]+$", ["PATCH"], "journal", "U"),
    (r"^/api/intercompany/transactions/[^/]+/confirm$", ["POST"], "journal", "A"),
    (r"^/api/intercompany/transactions/[^/]+/reject$", ["POST"], "journal", "V"),
    # items
    (r"^/api/items/[^/]+/duplicate$", ["POST"], "item", "P"),
    (r"^/api/items/[^/]+/status$", ["PATCH"], "item", "U"),
    (r"^/api/items/[^/]+/stock-adjustment$", ["POST"], "item", "P"),
    (r"^/api/items/[^/]+/stock-transfer$", ["POST"], "item", "P"),
    (r"^/api/items/bulk-import$", ["POST"], "item", "C"),
    (r"^/api/items/categories$", ["POST"], "item", "C"),
    (r"^/api/items/categories$", ["DELETE"], "item", "D"),
    # -- Sapu baseline (rute master-data/settings harian Admin; modul resolve utk ADMIN) --
    (r"^/api/tax-codes/[^/]+$", ["PATCH"], "tax", "U"),
    (r"^/api/tax-codes/[^/]+$", ["DELETE"], "tax", "D"),
    (r"^/api/tax-groups/[^/]+$", ["PATCH"], "tax", "U"),
    (r"^/api/tax-groups/[^/]+$", ["DELETE"], "tax", "D"),
    (r"^/api/storage-locations$", ["POST"], "warehouse", "C"),
    (r"^/api/storage-locations/[^/]+$", ["PATCH"], "warehouse", "U"),
    (r"^/api/storage-locations/[^/]+$", ["DELETE"], "warehouse", "D"),
    (r"^/api/employees/[^/]+/salary-config$", ["PUT"], "employee", "U"),
    (r"^/api/tables/", ["POST"], "tables", "C"),
    (r"^/api/tables/", ["PUT"], "tables", "U"),
    (r"^/api/reports/accounting-settings$", ["PATCH"], "report", "U"),
    (r"^/api/reports/aging-snapshot$", ["POST"], "report", "C"),
    (r"^/api/settings/aging-snapshot$", ["POST"], "report", "C"),
    (r"^/api/settings/accounting$", ["POST"], "tenant_settings", "U"),
    # -- [A] modul dihibahkan ADMIN (V258) --
    (r"^/api/branches", ["POST"], "branch", "C"),
    (r"^/api/branches", ["PUT", "PATCH"], "branch", "U"),
    (r"^/api/branches", ["DELETE"], "branch", "D"),
    (r"^/api/cost-centers", ["POST"], "cost_center", "C"),
    (r"^/api/cost-centers", ["PUT", "PATCH"], "cost_center", "U"),
    (r"^/api/cost-centers", ["DELETE"], "cost_center", "D"),
    (r"^/api/price-lists", ["POST"], "price_list", "C"),
    (r"^/api/price-lists", ["PUT", "PATCH"], "price_list", "U"),
    (r"^/api/price-lists", ["DELETE"], "price_list", "D"),
    (r"^/api/currencies", ["POST"], "currency", "C"),
    (r"^/api/currencies", ["PUT", "PATCH"], "currency", "U"),
    (r"^/api/currencies", ["DELETE"], "currency", "D"),
    (r"^/api/recipes", ["POST"], "recipe", "C"),
    (r"^/api/recipes", ["PUT", "PATCH"], "recipe", "U"),
    (r"^/api/recipes", ["DELETE"], "recipe", "D"),
    (r"^/api/kds", ["POST"], "kds", "C"),
    (r"^/api/kds", ["PUT", "PATCH"], "kds", "U"),
    (r"^/api/kds", ["DELETE"], "kds", "D"),
    (r"^/api/proformas", ["POST"], "proforma", "C"),
    (r"^/api/proformas", ["PUT", "PATCH"], "proforma", "U"),
    (r"^/api/proformas", ["DELETE"], "proforma", "D"),
    (r"^/api/item-batches", ["POST"], "item_batch", "C"),
    (r"^/api/item-batches", ["PUT", "PATCH"], "item_batch", "U"),
    (r"^/api/item-batches", ["DELETE"], "item_batch", "D"),
    (r"^/api/item-serials", ["POST"], "item_batch", "C"),
    (r"^/api/item-serials", ["PUT", "PATCH"], "item_batch", "U"),
    (r"^/api/item-serials", ["DELETE"], "item_batch", "D"),
    (r"^/api/product-djp-mapping", ["POST"], "tax", "C"),
    (r"^/api/product-djp-mapping", ["PUT", "PATCH"], "tax", "U"),
    (r"^/api/product-djp-mapping", ["DELETE"], "tax", "D"),
    # -- [C] tim (team_management -> USER_MANAGEMENT U; guard hierarki di handler) --
    (r"^/api/team-members/[^/]+/role$", ["PATCH"], "team_management", "U"),
    (r"^/api/team-members/[^/]+/overrides$", ["PATCH"], "team_management", "U"),
    (r"^/api/team-members/invitations/[^/]+$", ["DELETE"], "team_management", "U"),
    (r"^/api/items/units$", ["POST"], "item", "C"),
    # nsfp
    (r"^/api/nsfp-ranges$", ["POST"], "tax", "C"),
    (r"^/api/nsfp-ranges/[^/]+$", ["PATCH"], "tax", "U"),
    # opening_balance
    (r"^/api/opening-balance$", ["POST"], "journal", "C"),
    (r"^/api/opening-balance$", ["PUT"], "journal", "U"),
    (r"^/api/opening-balance/validate$", ["POST"], "journal", "R"),
    # payroll_runs
    (r"^/api/payroll/[^/]+/calculate$", ["POST"], "payroll", "R"),
    # periods
    (r"^/api/periods/[^/]+$", ["PUT"], "journal", "U"),
    # production
    (r"^/api/production$", ["POST"], "item", "C"),
    (r"^/api/production/[^/]+$", ["DELETE"], "item", "D"),
    (r"^/api/production/[^/]+$", ["PATCH"], "item", "U"),
    (r"^/api/production/[^/]+/cancel$", ["POST"], "item", "V"),
    (r"^/api/production/[^/]+/complete$", ["POST"], "item", "P"),
    (r"^/api/production/[^/]+/issue-materials$", ["POST"], "item", "P"),
    (r"^/api/production/[^/]+/labor$", ["POST"], "item", "P"),
    (r"^/api/production/[^/]+/release$", ["POST"], "item", "P"),
    (r"^/api/production/[^/]+/report-output$", ["POST"], "item", "P"),
    (r"^/api/production/[^/]+/start$", ["POST"], "item", "P"),
    # production_costing
    (r"^/api/production-costing/allocate-overhead$", ["POST"], "journal", "P"),
    (r"^/api/production-costing/cost-pools$", ["POST"], "journal", "C"),
    (r"^/api/production-costing/cost-pools/[^/]+/record-actual$", ["POST"], "journal", "P"),
    (r"^/api/production-costing/standard-costs$", ["POST"], "journal", "C"),
    (r"^/api/production-costing/standard-costs/calculate-from-bom/[^/]+$", ["POST"], "journal", "P"),
    # purchase_orders
    (r"^/api/purchase-orders$", ["POST"], "purchase_order", "C"),
    (r"^/api/purchase-orders/[^/]+$", ["DELETE"], "purchase_order", "D"),
    (r"^/api/purchase-orders/[^/]+$", ["PATCH"], "purchase_order", "U"),
    (r"^/api/purchase-orders/[^/]+/cancel$", ["POST"], "purchase_order", "V"),
    (r"^/api/purchase-orders/[^/]+/receive$", ["POST"], "purchase_order", "P"),
    (r"^/api/purchase-orders/[^/]+/send$", ["POST"], "purchase_order", "P"),
    (r"^/api/purchase-orders/[^/]+/to-bill$", ["POST"], "purchase_order", "P"),
    # recurring_bills
    (r"^/api/recurring-bills$", ["POST"], "purchase_invoice", "C"),
    (r"^/api/recurring-bills/[^/]+$", ["DELETE"], "purchase_invoice", "D"),
    (r"^/api/recurring-bills/[^/]+$", ["PATCH"], "purchase_invoice", "U"),
    (r"^/api/recurring-bills/[^/]+/generate$", ["POST"], "purchase_invoice", "P"),
    (r"^/api/recurring-bills/[^/]+/pause$", ["POST"], "purchase_invoice", "P"),
    (r"^/api/recurring-bills/[^/]+/resume$", ["POST"], "purchase_invoice", "P"),
    (r"^/api/recurring-bills/process-due$", ["POST"], "purchase_invoice", "P"),
    # recurring_invoices
    (r"^/api/recurring-invoices$", ["POST"], "sales_invoice", "C"),
    (r"^/api/recurring-invoices/[^/]+/generate$", ["POST"], "sales_invoice", "P"),
    (r"^/api/recurring-invoices/[^/]+/pause$", ["POST"], "sales_invoice", "P"),
    (r"^/api/recurring-invoices/[^/]+/resume$", ["POST"], "sales_invoice", "P"),
    (r"^/api/recurring-invoices/process-due$", ["POST"], "sales_invoice", "P"),
    # sales_invoices
    (r"^/api/sales-invoices/[^/]+/attachments$", ["POST"], "sales_invoice", "C"),
    (r"^/api/sales-invoices/[^/]+/attachments/[^/]+$", ["DELETE"], "sales_invoice", "D"),
    # sales_receipts
    (r"^/api/sales-receipts$", ["POST"], "sales_invoice", "C"),
    # stock_adjustments
    (r"^/api/stock-adjustments/[^/]+$", ["DELETE"], "item", "D"),
    (r"^/api/stock-adjustments/[^/]+$", ["PATCH"], "item", "U"),
    # stock_transfers
    (r"^/api/stock-transfers$", ["POST"], "item", "C"),
    (r"^/api/stock-transfers/[^/]+$", ["DELETE"], "item", "D"),
    (r"^/api/stock-transfers/[^/]+$", ["PATCH"], "item", "U"),
    (r"^/api/stock-transfers/[^/]+/cancel$", ["POST"], "item", "V"),
    (r"^/api/stock-transfers/[^/]+/receive$", ["POST"], "item", "P"),
    (r"^/api/stock-transfers/[^/]+/ship$", ["POST"], "item", "P"),
    # tax_invoices
    (r"^/api/tax-invoices/[^/]+/status$", ["PATCH"], "tax", "U"),
    # vendors
    (r"^/api/vendors/[^/]+/opening-balance$", ["POST"], "supplier", "C"),
    (r"^/api/vendors/[^/]+/status$", ["PATCH"], "supplier", "U"),
    (r"^/api/vendors/merge$", ["POST"], "supplier", "C"),
    # === READ authorization (STEP 1, 2026-09-20) ===
    # 283 authenticated GET routes mapped to their resource's module (governing rule:
    # a GET inherits the module its own WRITE routes use). Behaviour: a user WITHOUT the
    # module now gets 403 on these reads; module-holders and OWNER (bypass) are unaffected.
    # Enumeration was introspection-proven complete (app.routes == OpenAPI). documents/
    # document-intake are deliberately NOT here (multi-doctype; see READ_DEFAULT_OPEN_ALLOWLIST).
    (r"^/api/accounts/[^/]+/balance$", ["GET"], "chart_of_accounts", "R"),
    (r"^/api/accounts/[^/]+/journal\-entries$", ["GET"], "chart_of_accounts", "R"),
    (r"^/api/approval\-delegates$", ["GET"], "approval_inbox", "R"),
    (r"^/api/approval\-requests$", ["GET"], "approval_inbox", "R"),
    (r"^/api/approval\-requests/pending$", ["GET"], "approval_inbox", "R"),
    (r"^/api/approval\-requests/statistics$", ["GET"], "approval_inbox", "R"),
    (r"^/api/approval\-requests/submitted$", ["GET"], "approval_inbox", "R"),
    (r"^/api/approval\-requests/turnaround\-time$", ["GET"], "approval_inbox", "R"),
    (r"^/api/approval\-requests/[^/]+$", ["GET"], "approval_inbox", "R"),
    (r"^/api/approval\-workflows$", ["GET"], "approval_inbox", "R"),
    (r"^/api/approval\-workflows/[^/]+$", ["GET"], "approval_inbox", "R"),
    (r"^/api/audit\-logs$", ["GET"], "reports", "R"),
    (r"^/api/audit\-logs/entity/[^/]+/[^/]+$", ["GET"], "reports", "R"),
    (r"^/api/audit\-logs/search$", ["GET"], "reports", "R"),
    (r"^/api/audit\-logs/user/[^/]+$", ["GET"], "reports", "R"),
    (r"^/api/audit\-logs/[^/]+$", ["GET"], "reports", "R"),
    (r"^/api/audit/changes\-report$", ["GET"], "reports", "R"),
    (r"^/api/audit/failed\-logins$", ["GET"], "reports", "R"),
    (r"^/api/audit/login\-history$", ["GET"], "reports", "R"),
    (r"^/api/audit/retention\-policies$", ["GET"], "reports", "R"),
    (r"^/api/audit/sensitive\-access$", ["GET"], "reports", "R"),
    (r"^/api/audit/summary$", ["GET"], "reports", "R"),
    (r"^/api/audit/suspicious\-activity$", ["GET"], "reports", "R"),
    (r"^/api/audit/user\-activity\-report$", ["GET"], "reports", "R"),
    (r"^/api/bank\-reconciliation/accounts$", ["GET"], "kas_bank", "R"),
    (r"^/api/bank\-reconciliation/history/[^/]+$", ["GET"], "kas_bank", "R"),
    (r"^/api/bank\-reconciliation/sessions$", ["GET"], "kas_bank", "R"),
    (r"^/api/bank\-reconciliation/sessions/[^/]+$", ["GET"], "kas_bank", "R"),
    (r"^/api/bank\-reconciliation/sessions/[^/]+/statements$", ["GET"], "kas_bank", "R"),
    (r"^/api/bank\-reconciliation/sessions/[^/]+/summary$", ["GET"], "kas_bank", "R"),
    (r"^/api/bank\-reconciliation/sessions/[^/]+/transactions$", ["GET"], "kas_bank", "R"),
    (r"^/api/bank\-transfers$", ["GET"], "kas_bank", "R"),
    (r"^/api/bank\-transfers/summary$", ["GET"], "kas_bank", "R"),
    (r"^/api/bank\-transfers/[^/]+$", ["GET"], "kas_bank", "R"),
    (r"^/api/bank\-transfers/[^/]+/journal\-entries$", ["GET"], "kas_bank", "R"),
    (r"^/api/bill\-payments/vendors/[^/]+/open\-bills$", ["GET"], "send_payment", "R"),
    (r"^/api/bill\-payments/[^/]+/activities$", ["GET"], "send_payment", "R"),
    (r"^/api/bill\-payments/[^/]+/attachments$", ["GET"], "send_payment", "R"),
    (r"^/api/bill\-payments/[^/]+/documents$", ["GET"], "send_payment", "R"),
    (r"^/api/bill\-payments/[^/]+/journal\-entries$", ["GET"], "send_payment", "R"),
    (r"^/api/bill\-payments/[^/]+/transactions$", ["GET"], "send_payment", "R"),
    (r"^/api/bills/presets/[^/]+/apply$", ["GET"], "purchase_invoice", "R"),
    (r"^/api/bills/v2/[^/]+$", ["GET"], "purchase_invoice", "R"),
    (r"^/api/bills/[^/]+/activity$", ["GET"], "purchase_invoice", "R"),
    (r"^/api/bills/[^/]+/attachments$", ["GET"], "purchase_invoice", "R"),
    (r"^/api/bills/[^/]+/attachments/[^/]+/download$", ["GET"], "purchase_invoice", "R"),
    (r"^/api/bills/[^/]+/journals$", ["GET"], "purchase_invoice", "R"),
    (r"^/api/bills/[^/]+/pdf$", ["GET"], "purchase_invoice", "R"),
    (r"^/api/branches$", ["GET"], "branch", "R"),
    (r"^/api/branches/comparison$", ["GET"], "branch", "R"),
    (r"^/api/branches/ranking$", ["GET"], "branch", "R"),
    (r"^/api/branches/transfers$", ["GET"], "warehouse", "R"),
    (r"^/api/branches/transfers/[^/]+$", ["GET"], "warehouse", "R"),
    (r"^/api/branches/tree$", ["GET"], "branch", "R"),
    (r"^/api/branches/users/[^/]+/branches$", ["GET"], "branch", "R"),
    (r"^/api/branches/[^/]+$", ["GET"], "branch", "R"),
    (r"^/api/branches/[^/]+/permissions$", ["GET"], "branch", "R"),
    (r"^/api/branches/[^/]+/summary$", ["GET"], "branch", "R"),
    (r"^/api/branches/[^/]+/trial\-balance$", ["GET"], "branch", "R"),
    (r"^/api/cheques$", ["GET"], "kas_bank", "R"),
    (r"^/api/cheques/aging$", ["GET"], "kas_bank", "R"),
    (r"^/api/cheques/bounced$", ["GET"], "kas_bank", "R"),
    (r"^/api/cheques/by\-customer/[^/]+$", ["GET"], "kas_bank", "R"),
    (r"^/api/cheques/by\-vendor/[^/]+$", ["GET"], "kas_bank", "R"),
    (r"^/api/cheques/due\-today$", ["GET"], "kas_bank", "R"),
    (r"^/api/cheques/pending$", ["GET"], "kas_bank", "R"),
    (r"^/api/cheques/summary$", ["GET"], "kas_bank", "R"),
    (r"^/api/cheques/upcoming$", ["GET"], "kas_bank", "R"),
    (r"^/api/cheques/[^/]+$", ["GET"], "kas_bank", "R"),
    (r"^/api/consolidation/groups$", ["GET"], "reports", "R"),
    (r"^/api/consolidation/groups/[^/]+$", ["GET"], "reports", "R"),
    (r"^/api/consolidation/groups/[^/]+/intercompany$", ["GET"], "reports", "R"),
    (r"^/api/consolidation/groups/[^/]+/mappings$", ["GET"], "reports", "R"),
    (r"^/api/consolidation/runs$", ["GET"], "reports", "R"),
    (r"^/api/consolidation/runs/[^/]+$", ["GET"], "reports", "R"),
    (r"^/api/consolidation/runs/[^/]+/balance\-sheet$", ["GET"], "reports", "R"),
    (r"^/api/consolidation/runs/[^/]+/eliminations$", ["GET"], "reports", "R"),
    (r"^/api/consolidation/runs/[^/]+/export$", ["GET"], "reports", "R"),
    (r"^/api/consolidation/runs/[^/]+/income\-statement$", ["GET"], "reports", "R"),
    (r"^/api/consolidation/runs/[^/]+/trial\-balance$", ["GET"], "reports", "R"),
    (r"^/api/cost\-centers$", ["GET"], "cost_center", "R"),
    (r"^/api/cost\-centers/comparison$", ["GET"], "cost_center", "R"),
    (r"^/api/cost\-centers/tree$", ["GET"], "cost_center", "R"),
    (r"^/api/cost\-centers/[^/]+$", ["GET"], "cost_center", "R"),
    (r"^/api/cost\-centers/[^/]+/summary$", ["GET"], "cost_center", "R"),
    (r"^/api/cost\-centers/[^/]+/transactions$", ["GET"], "cost_center", "R"),
    (r"^/api/currencies$", ["GET"], "currency", "R"),
    (r"^/api/currencies/exchange\-rates$", ["GET"], "currency", "R"),
    (r"^/api/currencies/exchange\-rates/convert$", ["GET"], "currency", "R"),
    (r"^/api/currencies/exchange\-rates/latest$", ["GET"], "currency", "R"),
    (r"^/api/currencies/forex/gain\-loss$", ["GET"], "currency", "R"),
    (r"^/api/currencies/[^/]+$", ["GET"], "currency", "R"),
    (r"^/api/customer\-deposits/[^/]+/attachments$", ["GET"], "customer_deposit", "R"),
    (r"^/api/customers/[^/]+/activity$", ["GET"], "customer", "R"),
    (r"^/api/customers/[^/]+/available\-deposits$", ["GET"], "customer", "R"),
    (r"^/api/customers/[^/]+/balance$", ["GET"], "customer", "R"),
    (r"^/api/customers/[^/]+/credit$", ["GET"], "customer", "R"),
    (r"^/api/customers/[^/]+/journal\-entries$", ["GET"], "customer", "R"),
    (r"^/api/customers/[^/]+/open\-invoices$", ["GET"], "customer", "R"),
    (r"^/api/customers/[^/]+/transactions$", ["GET"], "customer", "R"),
    (r"^/api/deliveries$", ["GET"], "sales_invoice", "R"),
    (r"^/api/deliveries/summary$", ["GET"], "sales_invoice", "R"),
    (r"^/api/deliveries/[^/]+$", ["GET"], "sales_invoice", "R"),
    (r"^/api/deliveries/[^/]+/pdf$", ["GET"], "sales_invoice", "R"),
    (r"^/api/djp/kode\-barang\-jasa$", ["GET"], "tax", "R"),
    (r"^/api/djp/kode\-transaksi$", ["GET"], "tax", "R"),
    (r"^/api/djp/satuan\-ukur$", ["GET"], "tax", "R"),
    (r"^/api/efaktur/exports$", ["GET"], "tax", "R"),
    (r"^/api/employees/[^/]+/salary\-config$", ["GET"], "employee", "R"),
    (r"^/api/expense\-claims$", ["GET"], "expense", "R"),
    (r"^/api/expense\-claims/approval\-stats$", ["GET"], "expense", "R"),
    (r"^/api/expense\-claims/summary$", ["GET"], "expense", "R"),
    (r"^/api/expense\-claims/[^/]+$", ["GET"], "expense", "R"),
    (r"^/api/expense\-policy$", ["GET"], "expense", "R"),
    (r"^/api/expenses/[^/]+/attachments$", ["GET"], "expense", "R"),
    (r"^/api/expenses/[^/]+/journal\-entries$", ["GET"], "expense", "R"),
    (r"^/api/financial\-ratios$", ["GET"], "reports", "R"),
    (r"^/api/financial\-ratios/alerts$", ["GET"], "reports", "R"),
    (r"^/api/financial\-ratios/benchmark$", ["GET"], "reports", "R"),
    (r"^/api/financial\-ratios/calculate$", ["GET"], "reports", "R"),
    (r"^/api/financial\-ratios/compare\-periods$", ["GET"], "reports", "R"),
    (r"^/api/financial\-ratios/dashboard$", ["GET"], "reports", "R"),
    (r"^/api/financial\-ratios/definitions$", ["GET"], "reports", "R"),
    (r"^/api/financial\-ratios/definitions/[^/]+$", ["GET"], "reports", "R"),
    (r"^/api/financial\-ratios/history$", ["GET"], "reports", "R"),
    (r"^/api/financial\-ratios/trend$", ["GET"], "reports", "R"),
    (r"^/api/fiscal\-years$", ["GET"], "period", "R"),
    (r"^/api/fiscal\-years/[^/]+$", ["GET"], "period", "R"),
    (r"^/api/fixed\-assets$", ["GET"], "journal", "R"),
    (r"^/api/fixed\-assets/by\-category$", ["GET"], "journal", "R"),
    (r"^/api/fixed\-assets/by\-location$", ["GET"], "journal", "R"),
    (r"^/api/fixed\-assets/categories$", ["GET"], "journal", "R"),
    (r"^/api/fixed\-assets/depreciation\-due$", ["GET"], "journal", "R"),
    (r"^/api/fixed\-assets/maintenance\-due$", ["GET"], "journal", "R"),
    (r"^/api/fixed\-assets/register$", ["GET"], "journal", "R"),
    (r"^/api/fixed\-assets/[^/]+$", ["GET"], "journal", "R"),
    (r"^/api/fixed\-assets/[^/]+/depreciation\-schedule$", ["GET"], "journal", "R"),
    (r"^/api/fixed\-assets/[^/]+/maintenance$", ["GET"], "journal", "R"),
    (r"^/api/insight$", ["GET"], "reports", "R"),
    (r"^/api/intercompany/aging$", ["GET"], "journal", "R"),
    (r"^/api/intercompany/balances$", ["GET"], "journal", "R"),
    (r"^/api/intercompany/balances/[^/]+$", ["GET"], "journal", "R"),
    (r"^/api/intercompany/report$", ["GET"], "journal", "R"),
    (r"^/api/intercompany/transactions$", ["GET"], "journal", "R"),
    (r"^/api/intercompany/transactions/[^/]+$", ["GET"], "journal", "R"),
    (r"^/api/intercompany/unreconciled$", ["GET"], "journal", "R"),
    (r"^/api/intercompany/variances$", ["GET"], "journal", "R"),
    (r"^/api/inventory/categories$", ["GET"], "item", "R"),
    (r"^/api/inventory/low\-stock$", ["GET"], "item", "R"),
    (r"^/api/inventory/product\-margins$", ["GET"], "item", "R"),
    (r"^/api/inventory/products$", ["GET"], "item", "R"),
    (r"^/api/inventory/products/[^/]+$", ["GET"], "item", "R"),
    (r"^/api/inventory/products/[^/]+/stock\-card$", ["GET"], "item", "R"),
    (r"^/api/inventory/slow\-moving\-products$", ["GET"], "item", "R"),
    (r"^/api/inventory/summary$", ["GET"], "item", "R"),
    (r"^/api/inventory/top\-products$", ["GET"], "item", "R"),
    (r"^/api/invoices/purchase$", ["GET"], "purchase_invoice", "R"),
    (r"^/api/item\-batches$", ["GET"], "item_batch", "R"),
    (r"^/api/item\-batches/expired$", ["GET"], "item_batch", "R"),
    (r"^/api/item\-batches/expiring$", ["GET"], "item_batch", "R"),
    (r"^/api/item\-batches/items/[^/]+/batches/available$", ["GET"], "item_batch", "R"),
    (r"^/api/item\-batches/[^/]+$", ["GET"], "item_batch", "R"),
    (r"^/api/item\-serials$", ["GET"], "item_batch", "R"),
    (r"^/api/item\-serials/items/[^/]+/serials/available$", ["GET"], "item_batch", "R"),
    (r"^/api/item\-serials/search$", ["GET"], "item_batch", "R"),
    (r"^/api/item\-serials/warehouses/[^/]+/serials$", ["GET"], "item_batch", "R"),
    (r"^/api/item\-serials/[^/]+$", ["GET"], "item_batch", "R"),
    (r"^/api/item\-serials/[^/]+/history$", ["GET"], "item_batch", "R"),
    (r"^/api/items/accounts/cogs$", ["GET"], "item", "R"),
    (r"^/api/items/accounts/inventory$", ["GET"], "item", "R"),
    (r"^/api/items/accounts/inventory_asset$", ["GET"], "item", "R"),
    (r"^/api/items/accounts/purchase$", ["GET"], "item", "R"),
    (r"^/api/items/accounts/sales$", ["GET"], "item", "R"),
    (r"^/api/items/[^/]+/activity$", ["GET"], "item", "R"),
    (r"^/api/items/[^/]+/history$", ["GET"], "item", "R"),
    (r"^/api/items/[^/]+/journal\-entries$", ["GET"], "item", "R"),
    (r"^/api/items/[^/]+/related$", ["GET"], "item", "R"),
    (r"^/api/items/[^/]+/transactions$", ["GET"], "item", "R"),
    (r"^/api/journals/by\-account/[^/]+$", ["GET"], "journal", "R"),
    (r"^/api/journals/by\-source/[^/]+/[^/]+$", ["GET"], "journal", "R"),
    (r"^/api/kasbank/accounts$", ["GET"], "kas_bank", "R"),
    (r"^/api/kasbank/accounts/[^/]+/summary$", ["GET"], "kas_bank", "R"),
    (r"^/api/kasbank/accounts/[^/]+/transactions$", ["GET"], "kas_bank", "R"),
    (r"^/api/kasbank/stats$", ["GET"], "kas_bank", "R"),
    (r"^/api/kds/alerts$", ["GET"], "kds", "R"),
    (r"^/api/kds/display/[^/]+$", ["GET"], "kds", "R"),
    (r"^/api/kds/metrics$", ["GET"], "kds", "R"),
    (r"^/api/kds/orders$", ["GET"], "kds", "R"),
    (r"^/api/kds/orders/[^/]+$", ["GET"], "kds", "R"),
    (r"^/api/kds/stations$", ["GET"], "kds", "R"),
    (r"^/api/kds/stations/[^/]+$", ["GET"], "kds", "R"),
    (r"^/api/members/list$", ["GET"], "team_management", "R"),
    (r"^/api/members/search$", ["GET"], "team_management", "R"),
    (r"^/api/members/[^/]+$", ["GET"], "team_management", "R"),
    (r"^/api/nsfp\-ranges$", ["GET"], "tax", "R"),
    (r"^/api/nsfp\-ranges/usage$", ["GET"], "tax", "R"),
    (r"^/api/opening\-balance$", ["GET"], "journal", "R"),
    (r"^/api/opening\-balance/summary$", ["GET"], "journal", "R"),
    (r"^/api/opening\-balance/[^/]+$", ["GET"], "journal", "R"),
    (r"^/api/payment\-out$", ["GET"], "send_payment", "R"),
    (r"^/api/payment\-requests/[^/]+/journal\-entries$", ["GET"], "payment_request", "R"),
    (r"^/api/payroll\-config$", ["GET"], "payroll", "R"),
    (r"^/api/payroll\-config/$", ["GET"], "payroll", "R"),
    (r"^/api/payroll\-config/reports/monthly\-recap$", ["GET"], "payroll", "R"),
    (r"^/api/payroll\-payments/by\-payroll/[^/]+$", ["GET"], "payroll", "R"),
    (r"^/api/payroll/[^/]+/slips$", ["GET"], "payroll", "R"),
    (r"^/api/price\-lists$", ["GET"], "price_list", "R"),
    (r"^/api/price\-lists/dropdown$", ["GET"], "price_list", "R"),
    (r"^/api/price\-lists/[^/]+$", ["GET"], "price_list", "R"),
    (r"^/api/product\-djp\-mapping$", ["GET"], "tax", "R"),
    (r"^/api/production$", ["GET"], "item", "R"),
    (r"^/api/production\-costing/cost\-pools$", ["GET"], "work_order", "R"),
    (r"^/api/production\-costing/standard\-costs$", ["GET"], "work_order", "R"),
    (r"^/api/production\-costing/variance/[^/]+$", ["GET"], "work_order", "R"),
    (r"^/api/production/active$", ["GET"], "item", "R"),
    (r"^/api/production/orders$", ["GET"], "item", "R"),
    (r"^/api/production/schedule$", ["GET"], "item", "R"),
    (r"^/api/production/[^/]+$", ["GET"], "item", "R"),
    (r"^/api/production/[^/]+/cost\-analysis$", ["GET"], "item", "R"),
    (r"^/api/proformas$", ["GET"], "proforma", "R"),
    (r"^/api/proformas/[^/]+$", ["GET"], "proforma", "R"),
    (r"^/api/proformas/[^/]+/pdf$", ["GET"], "proforma", "R"),
    (r"^/api/purchase\-orders$", ["GET"], "purchase_order", "R"),
    (r"^/api/purchase\-orders/pending$", ["GET"], "purchase_order", "R"),
    (r"^/api/purchase\-orders/summary$", ["GET"], "purchase_order", "R"),
    (r"^/api/purchase\-orders/vendor/[^/]+$", ["GET"], "purchase_order", "R"),
    (r"^/api/purchase\-orders/[^/]+$", ["GET"], "purchase_order", "R"),
    (r"^/api/receive\-payments/[^/]+/attachments$", ["GET"], "receive_payment", "R"),
    (r"^/api/receive\-payments/[^/]+/journal\-entries$", ["GET"], "receive_payment", "R"),
    (r"^/api/receive\-payments/[^/]+/pdf$", ["GET"], "receive_payment", "R"),
    (r"^/api/recipes$", ["GET"], "recipe", "R"),
    (r"^/api/recipes/categories$", ["GET"], "recipe", "R"),
    (r"^/api/recipes/menu\-items$", ["GET"], "recipe", "R"),
    (r"^/api/recipes/menu\-items/[^/]+$", ["GET"], "recipe", "R"),
    (r"^/api/recipes/modifiers$", ["GET"], "recipe", "R"),
    (r"^/api/recipes/recipes$", ["GET"], "recipe", "R"),
    (r"^/api/recipes/recipes/[^/]+$", ["GET"], "recipe", "R"),
    (r"^/api/recipes/recipes/[^/]+/costing$", ["GET"], "recipe", "R"),
    (r"^/api/recurring\-bills$", ["GET"], "purchase_invoice", "R"),
    (r"^/api/recurring\-bills/due$", ["GET"], "purchase_invoice", "R"),
    (r"^/api/recurring\-bills/stats$", ["GET"], "purchase_invoice", "R"),
    (r"^/api/recurring\-bills/[^/]+$", ["GET"], "purchase_invoice", "R"),
    (r"^/api/recurring\-bills/[^/]+/history$", ["GET"], "purchase_invoice", "R"),
    (r"^/api/recurring\-expenses$", ["GET"], "expense", "R"),
    (r"^/api/recurring\-expenses/summary$", ["GET"], "expense", "R"),
    (r"^/api/recurring\-expenses/[^/]+$", ["GET"], "expense", "R"),
    (r"^/api/recurring\-invoices$", ["GET"], "sales_invoice", "R"),
    (r"^/api/recurring\-invoices/due$", ["GET"], "sales_invoice", "R"),
    (r"^/api/recurring\-invoices/[^/]+$", ["GET"], "sales_invoice", "R"),
    (r"^/api/recurring\-invoices/[^/]+/history$", ["GET"], "sales_invoice", "R"),
    (r"^/api/sales\-invoices/[^/]+/applicable\-deposits$", ["GET"], "sales_invoice", "R"),
    (r"^/api/sales\-invoices/[^/]+/attachments$", ["GET"], "sales_invoice", "R"),
    (r"^/api/sales\-invoices/[^/]+/attachments/[^/]+/download$", ["GET"], "sales_invoice", "R"),
    (r"^/api/sales\-invoices/[^/]+/fulfillments$", ["GET"], "sales_invoice", "R"),
    (r"^/api/sales\-receipts$", ["GET"], "receive_payment", "R"),
    (r"^/api/sales\-receipts/daily\-summary$", ["GET"], "receive_payment", "R"),
    (r"^/api/sales\-receipts/[^/]+$", ["GET"], "receive_payment", "R"),
    (r"^/api/salespersons$", ["GET"], "sales_invoice", "R"),
    (r"^/api/stock\-transfers$", ["GET"], "item", "R"),
    (r"^/api/stock\-transfers/in\-transit$", ["GET"], "item", "R"),
    (r"^/api/stock\-transfers/[^/]+$", ["GET"], "item", "R"),
    (r"^/api/storage\-locations$", ["GET"], "warehouse", "R"),
    (r"^/api/storage\-locations/dropdown$", ["GET"], "warehouse", "R"),
    (r"^/api/storage\-locations/tree$", ["GET"], "warehouse", "R"),
    (r"^/api/storage\-locations/[^/]+$", ["GET"], "warehouse", "R"),
    (r"^/api/suppliers/all$", ["GET"], "supplier", "R"),
    (r"^/api/suppliers/search$", ["GET"], "supplier", "R"),
    (r"^/api/tables$", ["GET"], "tables", "R"),
    (r"^/api/tables/areas$", ["GET"], "tables", "R"),
    (r"^/api/tables/floor\-plan/[^/]+$", ["GET"], "tables", "R"),
    (r"^/api/tables/reservations$", ["GET"], "tables", "R"),
    (r"^/api/tables/reservations/[^/]+$", ["GET"], "tables", "R"),
    (r"^/api/tables/sessions$", ["GET"], "tables", "R"),
    (r"^/api/tables/stats$", ["GET"], "tables", "R"),
    (r"^/api/tables/tables$", ["GET"], "tables", "R"),
    (r"^/api/tables/tables/[^/]+$", ["GET"], "tables", "R"),
    (r"^/api/tables/waitlist$", ["GET"], "tables", "R"),
    (r"^/api/team\-members/roles/list$", ["GET"], "team_management", "R"),
    (r"^/api/vendors/[^/]+/activity$", ["GET"], "supplier", "R"),
    (r"^/api/vendors/[^/]+/balance$", ["GET"], "supplier", "R"),
    (r"^/api/vendors/[^/]+/journal\-entries$", ["GET"], "supplier", "R"),
    (r"^/api/vendors/[^/]+/open\-bills$", ["GET"], "supplier", "R"),
    (r"^/api/vendors/[^/]+/transactions$", ["GET"], "supplier", "R"),
    # Kasbon (employee advances) — module payroll (V281)
    (r"^/api/employee-advances$", ["POST"], "payroll", "C"),
    (r"^/api/employee-advances$", ["GET"], "payroll", "R"),
    (r"^/api/employee-advances/balances$", ["GET"], "payroll", "R"),
    (r"^/api/employee-advances/[^/]+/void$", ["POST"], "payroll", "V"),
]

# READ default-open allowlist — for the STEP 2 default-closed flip (not yet built).
# documents + document-intake are MULTI-DOCTYPE reads whose correct gate is per-doctype
# handler-side READ filtering (mirrors the per-doctype write check in WRITE_EXEMPT); that
# is deferred to its own unit. Listed explicitly so step 2 does NOT silently 403 the
# owner's Dokumen screen for staff. Added 2026-09-20.
READ_DEFAULT_OPEN_ALLOWLIST = [
    r"^/api/documents(/|$)",
    r"^/api/document-intake(/|$)",
    # --- STEP 2 leave-open set (added 2026-09-21), reviewed route-by-route with MASTER ---
    # Self-service: read of the caller's OWN profile / devices / sessions.
    r"^/api/user/",
    r"^/api/devices(/|$)",
    r"^/api/session/",
    # Pre-provision: invited / onboarding user acting BEFORE a role exists.
    r"^/api/invite/",
    r"^/api/onboarding/",
    # Chat: reads are session-scoped to the caller; business actions forward the JWT to the
    # target module (enforced there), mirroring WRITE_EXEMPT.
    r"^/api/v3/chat/",
    r"^/chat/",
    r"^/api/setup/chat",
    r"^/api/tenant/[^/]+/chat",
    # Tenant branding/info: broadly read (logo shown to everyone).
    r"^/api/tenant/profile",
    r"^/api/tenant/[^/]+/info",
    # Cross-module surfaces that enforce per-module READ authz IN THE HANDLER (policy.can per
    # module, OWNER bypass, fail-closed) -- same shape as documents. Verified enforced
    # 2026-09-21: search (routers/search.py per-group _allowed) + SSE stream
    # (events.py _allowed_from_ctx). Middleware leaves them open; the handler filters results.
    r"^/api/search",
    r"^/api/events/stream",
]

# Routes that don't require permission checks
SKIP_PATTERNS = [
    r"^/api/auth",
    r"^/api/health",
    r"^/api/qr-auth",
    r"^/api/public",
    r"^/api/docs",
    r"^/api/openapi",
    r"^/api/dashboard",  # Dashboard has own FCL rules
    r"^/api/permissions",  # /me endpoint - self-service
    # 14 Sep 2026: dulu tertelan di komentar baris di atas (tak pernah aktif). Dipatok ke SATU rute yang ada, bukan
    # prefiks: prefiks akan diam-diam melewatkan cek untuk rute tulis peran yang kelak ditambahkan di bawahnya.
    r"^/api/team-members/roles/list$",  # Role list
    r"^/favicon",
    r"^/$",
    # 21 Sep 2026 (STEP 2 prep): infra/health probes must be OUT of the auth+permission path
    # entirely so they can NEVER 401 (monitoring + mh-restart.sh probes /healthz). SKIP, not
    # allowlist -- allowlist is after-auth and would 401 an unauthenticated probe.
    r"^/healthz$",
    r"^/health(/|$)",
    r"^/metrics$",
    r"^/ready$",
    r"^/version$",
    r"^/api/[^/]+/health(/|$)",
]

# 14 Sep 2026 TAHAP 3: WRITE tak terpetakan -> 403 (default tertutup). Himpunan ini adalah WRITE yang SENGAJA tak
# butuh izin modul, dengan alasan per baris. READ tak terpetakan TETAP terbuka (dicatat di tiket). Bukan SKIP:
# permintaan tetap butuh autentikasi; hanya cek IZIN MODUL yang dilewati untuk jalur-jalur ini.
WRITE_EXEMPT = [
    (r"^/api/session/logout", "sesi: pengguna mana pun boleh keluar (bukan tulis bisnis)"),
    (r"^/api/user/", "layanan-diri atas akun sendiri (profil, favorit)"),
    (r"^/api/onboarding/", "penyiapan tenant SEBELUM peran diprovisikan"),
    (r"^/api/invite/[^/]+/(accept|decline)$", "pengguna yang diundang bertindak sebelum punya peran"),
    (r"^/api/devices?($|/)", "manajemen perangkat/sesi milik sendiri"),
    (r"^/api/uploads/", "unggah berkas mentah; aksi bisnis hasilnya dicek di rute-nya sendiri"),
    # Chat: endpoint chat TIDAK menulis data istimewa sendiri. Aksi keuangan dieksekusi dengan MENERUSKAN JWT
    # pengguna ke endpoint kernel (is_direct), tempat izin modul TUJUAN ditegakkan. Lihat TIKET-sweep-izin (c).
    (r"^/api/v3/chat/", "chat v3: aksi diteruskan ber-JWT ke modul tujuan (dicek di sana)"),
    (r"^/chat/", "chat (jalur lama, ber-JWT ke modul tujuan)"),
    (r"^/api/setup/chat", "chat penyiapan (ber-JWT)"),
    (r"^/api/tenant/[^/]+/chat", "chat per-tenant (ber-JWT)"),
    (r"^/[^/]+/chat$", "chat per-tenant publik (ber-JWT)"),
    # [B] intake/dokumen: gate lolos, izin ditegakkan di handler (anggota aktif + modul-tujuan per doc_type tersimpan)
    (r"^/api/document-intake/upload$", "intake: anggota aktif dicek di handler"),
    (r"^/api/document-intake/execute-batch$", "intake: izin per-item dicek di handler"),
    (r"^/api/document-intake/document/[^/]+/(confirm|execute|reject|retry)$", "intake: izin modul-tujuan/anggota dicek di handler"),
    (r"^/api/documents/upload$", "unggah dokumen: anggota aktif dicek di handler"),
    (r"^/api/documents/[^/]+/attach$", "lampir dokumen: anggota aktif dicek di handler"),
]


from ..services.role_resolution import MSG_INACTIVE, MSG_NOT_PROVISIONED


class PermissionMiddleware(BaseHTTPMiddleware):
    """
    Middleware that enforces RBAC permissions based on route patterns.
    """

    def __init__(self, app, skip_permission_check: bool = False):
        super().__init__(app)
        self.skip_permission_check = skip_permission_check
        self._compiled_routes = [
            (re.compile(pattern), methods, module, action)
            for pattern, methods, module, action in ROUTE_PERMISSIONS
        ]
        self._compiled_skip = [re.compile(p) for p in SKIP_PATTERNS]
        self._compiled_write_exempt = [re.compile(p) for p, _ in WRITE_EXEMPT]
        self._compiled_read_open = [re.compile(p) for p in READ_DEFAULT_OPEN_ALLOWLIST]
        self._write_methods = {"POST", "PUT", "PATCH", "DELETE"}

    async def dispatch(self, request: Request, call_next):
        # Skip OPTIONS requests (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)

        # Skip if permission checking is disabled
        if self.skip_permission_check:
            return await call_next(request)

        path = request.url.path
        method = request.method

        # Check if route should skip permission check
        for pattern in self._compiled_skip:
            if pattern.match(path):
                return await call_next(request)

        # Find matching permission rule
        required_permission = self._find_permission(path, method)

        if required_permission:
            module, action = required_permission

            # Check if user is authenticated
            if not hasattr(request.state, "user") or not request.state.user:
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": "Authentication required",
                        "code": "UNAUTHENTICATED",
                    },
                )

            user = request.state.user

            try:
                policy_engine = get_policy_engine()

                # Build user context
                context = await policy_engine.get_user_context(
                    user_id=user["user_id"],
                    tenant_id=user["tenant_id"],
                    subscription_role=user.get("role", "USER"),
                )

                # Keanggotaan dicabut -> jawaban yang JELAS, bukan "izin
                # kurang". Bedanya penting bagi pengguna: "kamu tak punya izin
                # untuk ini" menyuruh orang minta izin tambahan; "aksesmu
                # dinonaktifkan" menyuruh orang menghubungi pemilik bisnis.
                if not context.membership_active:
                    logger.warning(
                        f"Akses ditolak (keanggotaan nonaktif): "
                        f"user={user['user_id']} tenant={user['tenant_id']} path={path}"
                    )
                    return JSONResponse(
                        status_code=403,
                        content={
                            "detail": {
                                "error_code": "MEMBERSHIP_INACTIVE",
                                "message": MSG_INACTIVE,
                            }
                        },
                    )

                # LAPIS 2 dari 2. can() juga menolak keadaan ini (return False),
                # tapi jawaban generik "izin kurang" salah menggambarkannya:
                # orangnya tidak kekurangan izin, ia tidak punya keanggotaan.
                #
                # 409, BUKAN 403 — konsisten dengan jawaban yang SUDAH tayang:
                # require_active_membership (pagar dashboard) mengembalikan 409
                # ROLE_NOT_PROVISIONED untuk pengguna yang sama. Kalau di sini
                # 403, satu pengguna mendapat DUA jawaban berbeda tergantung
                # rute — kelas cacat yang sama dengan 'active' vs 'ACTIVE' dan
                # 'INACTIVE' tanpa aturan: dua penulis memilih berbeda.
                if context.business_role_id is None and context.membership_active:
                    logger.warning(
                        f"Akses ditolak (tanpa baris peran): "
                        f"user={user['user_id']} tenant={user['tenant_id']} path={path}"
                    )
                    return JSONResponse(
                        status_code=403,
                        content={
                            "detail": {
                                "error_code": "MEMBERSHIP_INACTIVE",
                                "message": MSG_NOT_PROVISIONED,
                            }
                        },
                    )

                # Check permission
                allowed = await policy_engine.can(context, action, module)

                if not allowed:
                    logger.warning(
                        f"Permission denied: user={user['user_id']} "
                        f"path={path} method={method} "
                        f"module={module} action={action} "
                        f"role={context.business_role_code}"
                    )

                    action_names = {
                        "C": "create",
                        "R": "view",
                        "U": "update",
                        "D": "delete",
                        "V": "void",
                        "A": "approve",
                        "P": "post",
                        "E": "export",
                    }

                    return JSONResponse(
                        status_code=403,
                        content={
                            "error": "Permission denied",
                            "message": f"You don't have permission to {action_names.get(action, action)} {module.replace('_', ' ')}",
                            "code": "PERMISSION_DENIED",
                            "required_module": module,
                            "required_action": action,
                        },
                    )

                # Add context to request state for downstream use
                request.state.user["business_role_code"] = context.business_role_code
                request.state.user["business_role_id"] = context.business_role_id
                request.state.user["visibility_levels"] = context.visibility_levels
                request.state.user["approval_limit"] = context.approval_limit

            except Exception as e:
                # 14 Sep 2026 (sweep izin tahap 1): dulu FAIL-OPEN — galat apa pun di pemeriksa (engine belum siap,
                # DB putus) MENGIZINKAN permintaan yang seharusnya dicek. Kini ditolak. 72 jam log sebelum perubahan:
                # 0 kejadian "Permission check error", jadi tak ada alur hidup yang bergantung pada lolos-karena-galat.
                logger.error(
                    f"Permission check error -> DITOLAK: path={path} method={method} "
                    f"module={module} action={action} err={type(e).__name__}: {e}"
                )
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "Permission check failed",
                        "message": "Pemeriksaan izin gagal; coba lagi sebentar lagi.",
                        "code": "PERMISSION_CHECK_ERROR",
                    },
                )

        # 14 Sep 2026 TAHAP 3: default TERTUTUP untuk WRITE. Rute tulis TANPA pola & TIDAK di WRITE_EXEMPT:
        # OWNER tetap lolos (konsisten dgn bypass can()), non-owner -> 403 PERMISSION_UNMAPPED. READ tetap terbuka.
        elif method in self._write_methods and not any(p.match(path) for p in self._compiled_write_exempt):
            if not hasattr(request.state, "user") or not request.state.user:
                return JSONResponse(status_code=401, content={"error": "Authentication required", "code": "UNAUTHENTICATED"})
            user = request.state.user
            try:
                context = await get_policy_engine().get_user_context(
                    user_id=user["user_id"], tenant_id=user["tenant_id"], subscription_role=user.get("role", "USER"),
                )
                if context.business_role_code == "OWNER":
                    request.state.user["business_role_code"] = context.business_role_code
                    request.state.user["business_role_id"] = context.business_role_id
                    return await call_next(request)
                if not context.membership_active:
                    return JSONResponse(status_code=403, content={"detail": {"error_code": "MEMBERSHIP_INACTIVE", "message": MSG_INACTIVE}})
                logger.warning(
                    f"WRITE tak terpetakan ditolak (default tertutup): user={user['user_id']} "
                    f"path={path} method={method} role={context.business_role_code}"
                )
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "Permission denied",
                        "message": "Aksi ini belum diberi izin untuk peran Anda. Hubungi pemilik usaha.",
                        "code": "PERMISSION_UNMAPPED",
                    },
                )
            except Exception as e:  # noqa: BLE001
                logger.error(f"PERMISSION_UNMAPPED check error -> DITOLAK: path={path} method={method} err={type(e).__name__}: {e}")
                return JSONResponse(status_code=403, content={"error": "Permission check failed", "code": "PERMISSION_CHECK_ERROR"})

        # 21 Sep 2026 STEP 2: default-CLOSED for READ. Unmatched GET/HEAD NOT in the READ
        # leave-open allowlist -> OWNER passes (consistent with can() bypass), non-owner -> 403
        # PERMISSION_DENIED (the code the FE r105 access-state renders). Infra/health + self are
        # already returned by SKIP_PATTERNS above; documents/chat/search/etc stay open via the
        # allowlist. Mirrors the WRITE default-closed branch.
        elif method in ("GET", "HEAD") and not any(p.match(path) for p in self._compiled_read_open):
            if not hasattr(request.state, "user") or not request.state.user:
                return JSONResponse(status_code=401, content={"error": "Authentication required", "code": "UNAUTHENTICATED"})
            user = request.state.user
            try:
                context = await get_policy_engine().get_user_context(
                    user_id=user["user_id"], tenant_id=user["tenant_id"], subscription_role=user.get("role", "USER"),
                )
                if context.business_role_code == "OWNER":
                    request.state.user["business_role_code"] = context.business_role_code
                    request.state.user["business_role_id"] = context.business_role_id
                    return await call_next(request)
                if not context.membership_active:
                    return JSONResponse(status_code=403, content={"detail": {"error_code": "MEMBERSHIP_INACTIVE", "message": MSG_INACTIVE}})
                logger.warning(
                    f"READ tak terpetakan ditolak (default tertutup STEP 2): user={user['user_id']} "
                    f"path={path} method={method} role={context.business_role_code}"
                )
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "Permission denied",
                        "message": "Anda belum diberi izin melihat data ini. Hubungi pemilik usaha.",
                        "code": "PERMISSION_DENIED",
                        "required_module": None,
                        "required_action": "R",
                    },
                )
            except Exception as e:  # noqa: BLE001
                logger.error(f"READ default-closed check error -> DITOLAK: path={path} method={method} err={type(e).__name__}: {e}")
                return JSONResponse(status_code=403, content={"error": "Permission check failed", "code": "PERMISSION_CHECK_ERROR"})

        return await call_next(request)

    def _find_permission(self, path: str, method: str) -> Optional[Tuple[str, str]]:
        """Find the permission requirement for a given path and method."""
        for pattern, methods, module, action in self._compiled_routes:
            if method in methods and pattern.match(path):
                return (module, action)
        return None


def create_permission_middleware(skip_permission_check: bool = False):
    """Factory function to create permission middleware."""
    return lambda app: PermissionMiddleware(app, skip_permission_check)
