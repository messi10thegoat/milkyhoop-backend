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
    (r"^/api/team-members$", ["POST"], "team_management", "C"),
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
    (r"^/api/bpjs", ["GET"], "bpjs", "R"),
    (r"^/api/bpjs", ["POST", "PATCH", "PUT"], "bpjs", "U"),
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
    # AR/AP Aging
    (r"^/api/ar", ["GET"], "ar_aging", "R"),
    (r"^/api/aging/ar", ["GET"], "ar_aging", "R"),
    (r"^/api/aging/ap", ["GET"], "send_payment", "R"),
    (r"^/api/approval-inbox", ["GET"], "approval_inbox", "R"),
    (r"^/api/approvals", ["GET"], "approval_inbox", "R"),
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
    r"^/api/permissions",  # /me endpoint - self-service    r'^/api/team-members/roles',  # Role list
    r"^/favicon",
    r"^/$",
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
                                "error_code": "ROLE_INACTIVE",
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
                        status_code=409,
                        content={
                            "detail": {
                                "error_code": "ROLE_NOT_PROVISIONED",
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
                logger.error(f"Permission check error: {e}")
                # Fail-open for now (log error but allow request)
                # In production, you may want to fail-closed

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
