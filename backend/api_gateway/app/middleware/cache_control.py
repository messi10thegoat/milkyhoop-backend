from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
import re


class CacheControlMiddleware(BaseHTTPMiddleware):
    """
    RFC 7234 compliant cache control middleware.

    Cache policies:
    - Sensitive (auth/user/chat tokens & sessions): no-store, no-cache,
        must-revalidate, private — NEVER cache, even on disk.
    - Transactional ledger data (invoices, payments, journals, deliveries,
        stock moves, payroll, manufacturing): private, no-cache,
        must-revalidate (browser MAY store but MUST revalidate via 304).
    - Mutable master/list data (accounts, items, customers, vendors,
        bank-accounts): private, no-cache, must-revalidate.
    - Reports/dashboard (derived from journals — change on every post):
        private, no-cache, must-revalidate.
    - Chat sessions list: private, 5s (chat list freshness).
    - Default: no-store, must-revalidate.

    Header preserve: if handler already set Cache-Control on the response,
    middleware leaves it alone (router-level overrides win, e.g., PDFs, SSE,
    asset endpoints, chat_history per-response).

    Errors (status >= 400) get the same policy applied via the pattern
    match — there is no early-out for non-2xx responses.

    NOTE (2026-05-28 policy update): SHORT_CACHE and MEDIUM_CACHE buckets
    were demonstrably serving stale list bodies post-mutation. Switched both
    to no-cache,must-revalidate to force revalidation while still allowing
    304 short-circuit for bandwidth.

    NOTE (2026-05-28 follow-up): Transactional bucket (invoices/payments/
    journals/etc.) relaxed from `no-store` to `private, no-cache,
    must-revalidate` once ETagMiddleware shipped. This unlocks 304
    bandwidth savings for the transactional ledger lists which were the
    largest payloads in the app. Auth/user/chat endpoints STAY no-store
    because their bodies contain secrets/PII that must never touch disk
    cache, and 304 negotiation provides no benefit there.
    """

    # Special-case: chat sessions list — short browser cache (matched FIRST)
    CHAT_SESSIONS_PATTERN = r"^/api/v3/chat/sessions"

    # Sensitive: secrets / session tokens / chat content — never cache.
    SENSITIVE_NO_STORE_PATTERNS = [
        r"^/api/auth/",
        r"^/api/user/",
        r"^/chat/",
        r"^/api/v3/chat/",
        r"^/api/action-chat/",
    ]

    # Transactional ledger data — was no-store, now revalidate to leverage
    # ETagMiddleware 304 short-circuit. Bodies are not secret; freshness is
    # guaranteed by must-revalidate + server-side ETag recompute.
    TRANSACTION_PATTERNS = [
        r"^/api/invoices",
        r"^/api/sales-invoices",
        r"^/api/bills",
        r"^/api/payments",
        r"^/api/bill-payments",
        r"^/api/receive-payments",
        r"^/api/credit-notes",
        r"^/api/vendor-credits",
        r"^/api/expenses",
        r"^/api/journals",
        r"^/api/bank-transfers",
        r"^/api/bank-transactions",
        r"^/api/deliveries",
        r"^/api/stock-adjustments",
        r"^/api/stock-transfers",
        r"^/api/payroll/",
        r"^/api/manufacturing/",
    ]

    # Mutable master data — revalidate every request.
    MUTABLE_LIST_PATTERNS = [
        r"^/api/accounts",
        r"^/api/items",
        r"^/api/customers",
        r"^/api/vendors",
        r"^/api/bank-accounts",
    ]

    # Derived data — revalidate every request.
    DERIVED_PATTERNS = [
        r"^/api/reports/",
        r"^/api/dashboard/",
    ]

    NO_CACHE_HEADER = "no-store, no-cache, must-revalidate, private"
    REVALIDATE_HEADER = "private, no-cache, must-revalidate"

    def __init__(self, app: ASGIApp):
        super().__init__(app)
        self.chat_sessions_regex = re.compile(self.CHAT_SESSIONS_PATTERN)
        self.sensitive_regex = re.compile("|".join(self.SENSITIVE_NO_STORE_PATTERNS))
        self.transaction_regex = re.compile("|".join(self.TRANSACTION_PATTERNS))
        self.mutable_list_regex = re.compile("|".join(self.MUTABLE_LIST_PATTERNS))
        self.derived_regex = re.compile("|".join(self.DERIVED_PATTERNS))

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Header preserve guard: handler-level overrides win.
        if "Cache-Control" in response.headers:
            return response

        path = request.url.path

        # 1) chat sessions list — match FIRST (before general /api/v3/chat/)
        if self.chat_sessions_regex.search(path):
            response.headers["Cache-Control"] = "private, max-age=5"

        # 2) Sensitive — auth/user/chat — never cache, never store.
        elif self.sensitive_regex.search(path):
            response.headers["Cache-Control"] = self.NO_CACHE_HEADER
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"

        # 3) Transactional ledger — revalidate (304-capable via ETag).
        elif self.transaction_regex.search(path):
            response.headers["Cache-Control"] = self.REVALIDATE_HEADER

        # 4) Mutable master/list data — revalidate every request.
        elif self.mutable_list_regex.search(path):
            response.headers["Cache-Control"] = self.REVALIDATE_HEADER

        # 5) Derived data (reports / dashboard) — revalidate every request.
        elif self.derived_regex.search(path):
            response.headers["Cache-Control"] = self.REVALIDATE_HEADER

        # 6) Default fallback — safe no-store.
        else:
            response.headers["Cache-Control"] = "no-store, must-revalidate"

        return response
