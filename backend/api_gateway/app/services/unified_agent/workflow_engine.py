"""
Workflow Engine — Deterministic state machine for chat workflows.

Architecture: Agentic-Deterministic
  - LLM = Interpreter (understand intent, extract data, narrate)
  - Code = Controller (state transitions, deterministic flow)

The engine manages workflow state in PostgreSQL (chat_workflow_state table)
and uses internal HTTP calls to existing REST endpoints.
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import date as date_type
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Callable, Awaitable

import httpx

logger = logging.getLogger("unified_agent.workflow_engine")
logger.setLevel(logging.INFO)

# FIX_WF_STALE_REUSE (2026-06-15): idle TTL for crud_form reuse. Matches the
# established 30-min sticky-period notion used elsewhere in the pipeline. A
# workflow whose row hasn't advanced within this window is treated as abandoned
# (frontend reuses one conversation for days) so a fresh request starts clean
# instead of merging onto stale payload.
WORKFLOW_IDLE_TTL_SECONDS = int(os.environ.get("WORKFLOW_IDLE_TTL_SECONDS", "1800"))

# Prune transient data after leaving a state to keep ctx.data lean.
# Keys listed are removed after advancing FROM that state.
# NEVER prune: recon_session_id, account_id, statement_ending_balance, unmatched_count, reviewed_count
PRUNE_AFTER_STATE = {
    "SHOW_SUMMARY": ["import_result", "match_result"],
    "REVIEWING": ["prematched"],
    "BALANCE_PROOF": ["summary", "review_preview"],
    "COMPLETED": ["balance_proof"],
}
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)


# ─── State Enum ───────────────────────────────────────────────────────────

class ReconState(str, Enum):
    IDENTIFY_ACCOUNT = "IDENTIFY_ACCOUNT"
    CHECK_EXISTING = "CHECK_EXISTING"
    NEED_BALANCE = "NEED_BALANCE"
    NEED_FILE = "NEED_FILE"
    IMPORTING = "IMPORTING"
    MATCHING = "MATCHING"
    SHOW_SUMMARY = "SHOW_SUMMARY"
    REVIEWING = "REVIEWING"
    BALANCE_PROOF = "BALANCE_PROOF"
    FINALIZE = "FINALIZE"
    COMPLETED = "COMPLETED"


class DocReviewState(str, Enum):
    """Document review workflow states."""
    FETCH_DOCUMENT = "FETCH_DOCUMENT"
    PRESENT_DRAFT = "PRESENT_DRAFT"
    AWAITING_DECISION = "AWAITING_DECISION"
    POSTING = "POSTING"
    POSTED = "POSTED"
    POSTING_FAILED = "POSTING_FAILED"
    COMPLETED = "COMPLETED"


class InvoicePaymentState(str, Enum):
    """Invoice + immediate payment workflow states."""
    CREATE_INVOICE = "CREATE_INVOICE"
    AWAIT_INVOICE_CONFIRM = "AWAIT_INVOICE_CONFIRM"
    CREATE_PAYMENT = "CREATE_PAYMENT"
    AWAIT_PAYMENT_CONFIRM = "AWAIT_PAYMENT_CONFIRM"
    COMPLETED = "COMPLETED"


class MonthlyClosingState(str, Enum):
    """Monthly closing workflow states."""
    CHECK_DRAFTS = "CHECK_DRAFTS"
    CHECK_RECONCILIATION = "CHECK_RECONCILIATION"
    GENERATE_REPORTS = "GENERATE_REPORTS"
    PRESENT_SUMMARY = "PRESENT_SUMMARY"
    CLOSE_PERIOD = "CLOSE_PERIOD"
    AWAIT_CLOSE_CONFIRM = "AWAIT_CLOSE_CONFIRM"
    COMPLETED = "COMPLETED"


class CrudFormState(str, Enum):
    """CRUD form multi-turn workflow states."""
    COLLECTING = "COLLECTING"
    PROPOSING = "PROPOSING"
    COMPLETED = "COMPLETED"


# ─── Data Classes ─────────────────────────────────────────────────────────

@dataclass
class StepResult:
    """Result of a workflow process/resume call."""
    advanced: bool = False
    new_state: str = ""
    llm_instruction: str = ""
    auto_executed: bool = False
    auto_results: Optional[Dict[str, Any]] = None
    direct_action: Optional[Dict[str, Any]] = None
    completed: bool = False


@dataclass
class WorkflowContext:
    """Persistent workflow state."""
    workflow_id: str = ""
    workflow_type: str = "bank_reconciliation"
    tenant_id: str = ""
    user_id: str = ""
    chat_session_id: str = ""
    current_state: str = "IDENTIFY_ACCOUNT"
    status: str = "active"
    data: Dict[str, Any] = field(default_factory=dict)
    auth_token: str = ""


# ─── Check Functions (Gate) ───────────────────────────────────────────────

async def check_account_identified(ctx: WorkflowContext, user_data: dict) -> Tuple[bool, str]:
    """Gate: do we have an account_id?"""
    if ctx.data.get("account_id"):
        return (True, "")
    return (False, "account_id")


async def check_always_pass(ctx: WorkflowContext, user_data: dict) -> Tuple[bool, str]:
    """Gate that always passes — for auto-execute states."""
    return (True, "")


async def check_has_balance(ctx: WorkflowContext, user_data: dict) -> Tuple[bool, str]:
    """Gate: do we have statement_ending_balance?"""
    if ctx.data.get("statement_ending_balance") is not None:
        return (True, "")
    return (False, "statement_ending_balance")


async def check_has_file_or_nofile(ctx: WorkflowContext, user_data: dict) -> Tuple[bool, str]:
    """Gate: do we have a file_ref OR user said no file?
    If file_ref present, validates file actually exists on disk.
    Clears stale file_ref and asks user to re-upload if missing.
    """
    if ctx.data.get("no_file"):
        return (True, "")
    file_ref = ctx.data.get("file_ref")
    if not file_ref:
        return (False, "file_ref or no_file")
    # Validate file exists on disk
    if file_ref.startswith("chat_upload:"):
        hash_ext = file_ref.split(":", 1)[1]
        tenant_id = ctx.tenant_id or ""
        filepath = os.path.join("/tmp/milkyhoop_uploads", tenant_id, "chat", hash_ext)
        if not os.path.exists(filepath):
            ctx.data.pop("file_ref", None)
            logger.warning(
                f"[RECON] File not found: {filepath}. "
                f"Clearing file_ref, asking user to re-upload."
            )
            return (False, "File tidak ditemukan (mungkin sudah expired). Silakan upload ulang.")
    return (True, "")


async def check_review_complete(ctx: WorkflowContext, user_data: dict) -> Tuple[bool, str]:
    """Gate: have all review items been processed?
    Hybrid: optimistic counter first, DB reconciliation when counter says 'done'.
    This prevents desync where reviewed_count >= unmatched_count but DB still has unmatched items.
    """
    if ctx.data.get("review_complete"):
        return (True, "")
    unmatched = ctx.data.get("unmatched_count", 0)
    reviewed = ctx.data.get("reviewed_count", 0)

    # Fast path: counter says there's more to review
    if unmatched > 0 and reviewed < unmatched:
        remaining = max(0, unmatched - reviewed)
        return (False, f"{remaining} item belum di-review")

    # Counter says "done" (unmatched==0 OR reviewed>=unmatched) — verify via DB
    session_id = ctx.data.get("recon_session_id", "")
    if session_id and ctx.auth_token:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    f"http://localhost:8000/api/bank-reconciliation/sessions/{session_id}/statements",
                    params={"match_status": "unmatched", "limit": 1},
                    headers={"Authorization": f"Bearer {ctx.auth_token}"},
                )
            if resp.status_code == 200:
                data = resp.json()
                db_unmatched = data.get("total", data.get("count", len(data.get("data", data.get("lines", [])))))
                if db_unmatched > 0:
                    # Counter desynced — correct it and keep reviewing
                    ctx.data["unmatched_count"] = db_unmatched
                    ctx.data["reviewed_count"] = 0  # reset to force re-review
                    logger.warning(f"[RECON] Review counter desync: counter says done but DB has {db_unmatched} unmatched. Corrected.")
                    return (False, f"{db_unmatched} item belum di-review (diverifikasi dari database)")
        except Exception as e:
            logger.warning(f"[RECON] DB verification failed, trusting counter: {e}")

    # DB confirms done (or no session/token for verification)
    ctx.data["review_complete"] = True
    return (True, "")


async def check_can_complete(ctx: WorkflowContext, user_data: dict) -> Tuple[bool, str]:
    """Gate: only finalize if balance proof says can_complete=True AND user explicitly approved."""
    proof = ctx.data.get("balance_proof") or ctx.data.get("summary")
    if not proof:
        return (False, "Perlu balance proof terlebih dahulu")
    if not proof.get("can_complete", False):
        blockers = proof.get("completion_blockers", [])
        msg = "; ".join(blockers) if blockers else "Rekonsiliasi belum bisa diselesaikan"
        return (False, msg)
    if not ctx.data.get("user_approved_complete"):
        return (False, "Menunggu konfirmasi user")
    return (True, "")


# ─── Auto-Execute Functions ──────────────────────────────────────────────

# ============ DOC REVIEW CHECK FUNCTIONS ============

async def check_has_document_id(ctx: WorkflowContext, user_data: dict) -> Tuple[bool, str]:
    """Gate: document_id must be in context data."""
    has_id = bool(ctx.data.get("document_id"))
    return has_id, "" if has_id else "document_id diperlukan"


async def check_has_draft_plan(ctx: WorkflowContext, user_data: dict) -> Tuple[bool, str]:
    """Gate: fetched document must have a draft_plan."""
    doc = ctx.data.get("document")
    has_draft = bool(doc and doc.get("draft_plan"))
    return has_draft, "" if has_draft else "Dokumen belum punya draft plan"


# ============ INVOICE + PAYMENT CHECK FUNCTIONS ============

async def check_has_invoice_data(ctx: WorkflowContext, user_data: dict) -> Tuple[bool, str]:
    """Gate: do we have customer + items for invoice?"""
    has_customer = bool(ctx.data.get("customer_id"))
    has_items = bool(ctx.data.get("items"))
    if has_customer and has_items:
        return (True, "")
    missing = []
    if not has_customer:
        missing.append("customer")
    if not has_items:
        missing.append("items")
    return (False, ", ".join(missing))


async def check_invoice_created(ctx: WorkflowContext, user_data: dict) -> Tuple[bool, str]:
    """Gate: has the invoice been confirmed/created?"""
    if ctx.data.get("invoice_id") and ctx.data.get("invoice_confirmed"):
        return (True, "")
    return (False, "Invoice belum dikonfirmasi")


async def check_payment_data_ready(ctx: WorkflowContext, user_data: dict) -> Tuple[bool, str]:
    """Gate: do we have bank_account for payment?"""
    if ctx.data.get("bank_account_id") and ctx.data.get("invoice_id"):
        return (True, "")
    if not ctx.data.get("bank_account_id"):
        return (False, "bank_account_id")
    return (False, "invoice_id")


# ============ MONTHLY CLOSING CHECK FUNCTIONS ============

async def check_has_period(ctx: WorkflowContext, user_data: dict) -> Tuple[bool, str]:
    """Gate: do we have the target period?"""
    if ctx.data.get("period"):
        return (True, "")
    return (False, "period (format: YYYY-MM)")


async def check_drafts_clear(ctx: WorkflowContext, user_data: dict) -> Tuple[bool, str]:
    """Gate: are there zero draft invoices/bills?"""
    draft_invoices = ctx.data.get("draft_invoice_count", -1)
    draft_bills = ctx.data.get("draft_bill_count", -1)
    if draft_invoices == 0 and draft_bills == 0:
        return (True, "")
    parts = []
    if draft_invoices > 0:
        parts.append(f"{draft_invoices} faktur draft")
    if draft_bills > 0:
        parts.append(f"{draft_bills} tagihan draft")
    if parts:
        joined = ", ".join(parts)
        return (False, f"Masih ada {joined} yang belum diposting")
    return (True, "")


async def check_user_approved_close(ctx: WorkflowContext, user_data: dict) -> Tuple[bool, str]:
    """Gate: user explicitly approved period closing."""
    if ctx.data.get("user_approved_close"):
        return (True, "")
    return (False, "Menunggu konfirmasi user untuk tutup periode")


async def auto_check_existing(ctx: WorkflowContext, call_internal) -> Dict[str, Any]:
    """Auto: check for existing in-progress recon session."""
    account_id = ctx.data.get("account_id", "")
    try:
        result = await call_internal(
            "GET",
            f"/api/bank-reconciliation/sessions?account_id={account_id}&status=in_progress"
        )
        sessions = result.get("data", result) if isinstance(result, dict) else result
        if isinstance(sessions, list) and len(sessions) > 0:
            existing = sessions[0]
            session_id = existing.get("id", existing.get("session_id", ""))
            ctx.data["recon_session_id"] = session_id
            ctx.data["existing_session"] = True
            return {
                "existing_session": True,
                "session_id": session_id,
                "message": f"Ada session rekonsiliasi yang sudah aktif (ID: {session_id}). Melanjutkan session tersebut."
            }
    except Exception as e:
        logger.warning(f"Check existing session failed: {e}")

    ctx.data["existing_session"] = False
    return {"existing_session": False}


async def auto_create_session_and_import(ctx: WorkflowContext, call_internal) -> Dict[str, Any]:
    """Auto: create recon session + import file (if provided).

    IDEMPOTENT — each sub-operation checks if already done before executing.
    Persists ctx immediately after each sub-operation via ctx._save_fn (crash-safe).
    On retry, already-completed sub-operations are skipped.
    """
    account_id = ctx.data.get("account_id", "")
    balance = ctx.data.get("statement_ending_balance")
    file_ref = ctx.data.get("file_ref")
    no_file = ctx.data.get("no_file", False)
    save_fn = getattr(ctx, '_save_fn', None)

    today = date_type.today().isoformat()
    first_of_month = date_type.today().replace(day=1).isoformat()

    try:
        # ── Sub-op 1: Create session (idempotent — skip if recon_session_id exists) ──
        session_id = ctx.data.get("recon_session_id")

        if not session_id:
            body = {
                "account_id": account_id,
                "statement_date": today,
                "statement_start_date": ctx.data.get("statement_start_date", first_of_month),
                "statement_end_date": ctx.data.get("statement_end_date", today),
                "statement_beginning_balance": ctx.data.get("statement_beginning_balance", 0),
                "statement_ending_balance": balance,
                "mode": "manual" if no_file else "import",
            }
            result = await call_internal("POST", "/api/bank-reconciliation/sessions", body)
            session_id = result.get("id", result.get("session_id", ""))
            if not session_id:
                return {"error": f"Session creation failed: {result}", "message": "Gagal membuat session rekonsiliasi."}

            ctx.data["recon_session_id"] = session_id
            if save_fn:
                await save_fn(ctx)  # PERSIST IMMEDIATELY — crash after this = safe on retry
            logger.info(f"[RECON] Session created: {session_id} account={account_id}")

        # ── Sub-op 2: Import file (idempotent — skip if import_result exists) ──
        if file_ref and not no_file and not ctx.data.get("import_result"):
            execute_tool = getattr(ctx, '_execute_tool', None)
            if execute_tool:
                import_result = await execute_tool("import_bank_statement", {
                    "session_id": session_id,
                    "file_ref": file_ref,
                })
                if not import_result.get("success", True):
                    return {"error": import_result.get("error", "Import failed"), "session_id": session_id}

                ctx.data["import_result"] = import_result
                if save_fn:
                    await save_fn(ctx)  # PERSIST IMMEDIATELY
                logger.info(f"[RECON] Import complete: session={session_id}")

                return {
                    "session_id": session_id,
                    "import_result": import_result,
                    "message": "Session dibuat dan file berhasil diimport."
                }
            else:
                logger.warning("No execute_tool callback for import — skipping file import")
        elif no_file and not ctx.data.get("import_result"):
            ctx.data["import_result"] = {"success": True, "mode": "manual", "lines_imported": 0}
            if save_fn:
                await save_fn(ctx)

        return {
            "session_id": session_id,
            "message": f"Session rekonsiliasi dibuat (mode: {'manual' if no_file else 'import'})."
        }
    except Exception as e:
        logger.error(f"Auto create session/import failed: {e}")
        return {"error": str(e), "message": f"Gagal membuat session: {e}"}


async def auto_match(ctx: WorkflowContext, call_internal) -> Dict[str, Any]:
    """Auto: run agentic reconciliation matching.

    Matching is safe to re-run (agentic-reconcile recalculates).
    Persists result via ctx._save_fn for crash safety.
    """
    session_id = ctx.data.get("recon_session_id", "")
    if not session_id:
        return {"error": "No recon session ID", "message": "Session ID tidak ditemukan."}

    try:
        # Use execute_tool callback (agentic_reconcile is a session tool, not REST)
        execute_tool = getattr(ctx, '_execute_tool', None)
        if execute_tool:
            result = await execute_tool("agentic_reconcile", {"session_id": session_id})
        else:
            # Fallback to direct HTTP (agentic-reconcile IS also a REST endpoint)
            result = await call_internal(
                "POST",
                f"/api/bank-reconciliation/sessions/{session_id}/agentic-reconcile",
                {"max_actions": 50, "include_categorize": True}
            )
        ctx.data["match_result"] = result
        save_fn = getattr(ctx, '_save_fn', None)
        if save_fn:
            await save_fn(ctx)
        return result
    except Exception as e:
        logger.error(f"Auto match failed: {e}")
        return {"error": str(e), "message": f"Gagal menjalankan auto-match: {e}"}


async def _prescan_unmatched(
    session_id: str, auth_token: str, max_lines: int = 100
) -> Tuple[Dict[str, int], Dict[str, Dict]]:
    """
    Pre-scan all unmatched statement lines against outstanding bills/invoices.
    Returns (preview_counts, prematched_dict).

    preview_counts: {"bill_match": N, "invoice_match": N, "no_match": N}
    prematched_dict: {line_id: {"type": "bill_match", "data": {...}}, ...}

    Fetches bills + invoices ONCE, then matches each line.
    READ-ONLY — no state mutations beyond return values.
    """
    from decimal import Decimal as D

    base_url = "http://localhost:8000"
    headers = {"Authorization": f"Bearer {auth_token}"}

    # 1. Fetch all unmatched statement lines
    async with httpx.AsyncClient(timeout=15.0) as client:
        lines_resp = await client.get(
            f"{base_url}/api/bank-reconciliation/sessions/{session_id}/statements",
            params={"match_status": "unmatched", "limit": max_lines},
            headers=headers,
        )
    if lines_resp.status_code != 200:
        return {"bill_match": 0, "invoice_match": 0, "no_match": 0}, {}

    lines_data = lines_resp.json()
    lines = lines_data.get("data", lines_data.get("lines", []))
    if not lines:
        return {"bill_match": 0, "invoice_match": 0, "no_match": 0}, {}

    # 2. Fetch outstanding bills + invoices ONCE for all lines
    async def _fetch_all(url: str, statuses: list[str]) -> list[dict]:
        results = []
        for status in statuses:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        f"{base_url}{url}",
                        params={"status": status, "limit": 50},
                        headers=headers,
                    )
                if resp.status_code == 200:
                    data = resp.json()
                    results.extend(data.get("items", data.get("data", data.get("bills", data.get("invoices", [])))))
            except Exception:
                pass
        return results

    bills, invoices = [], []
    # Check if any line is debit (needs bills) or credit (needs invoices)
    has_debit = any(not l.get("is_credit", False) for l in lines)
    has_credit = any(l.get("is_credit", False) for l in lines)

    import asyncio as _aio

    async def _noop():
        return []

    fetched = await _aio.gather(
        _fetch_all("/api/bills", ["unpaid", "partial"]) if has_debit else _noop(),
        _fetch_all("/api/sales-invoices", ["unpaid", "partial"]) if has_credit else _noop(),
        return_exceptions=True,
    )
    bills = fetched[0] if isinstance(fetched[0], list) else []
    invoices = fetched[1] if isinstance(fetched[1], list) else []

    # 3. Match each line
    preview = {"bill_match": 0, "invoice_match": 0, "no_match": 0}
    prematched: Dict[str, Dict] = {}

    for line in lines:
        line_id = line.get("id", "")
        is_credit = line.get("is_credit", False)
        amount = abs(D(str(line.get("amount", 0))))
        desc = (line.get("description") or "").upper()
        ref = (line.get("reference") or "").upper()

        match_type = "no_match"
        match_data: Dict[str, Any] = {}

        if not is_credit and bills:
            # Debit → try bill matching (same logic as tool_executor)
            best = _prescan_match_bill(amount, desc, ref, bills)
            if best:
                match_type = "bill_match"
                match_data = best
        elif is_credit and invoices:
            # Credit → try invoice matching
            best = _prescan_match_invoice(amount, desc, ref, invoices)
            if best:
                match_type = "invoice_match"
                match_data = best

        preview[match_type] += 1
        prematched[line_id] = {"type": match_type, "data": match_data}

    return preview, prematched


def _prescan_match_bill(
    amount, desc: str, ref: str, bills: list[dict]
) -> Optional[Dict]:
    """Match a debit line against bills. Returns best match or None.
    Same logic as tool_executor._match_against_outstanding_bills but synchronous + cached data."""
    from decimal import Decimal as D

    best = None
    best_priority = 99

    for bill in bills:
        bill_number = (bill.get("invoice_number") or "").upper()
        vendor_name = (bill.get("vendor_name") or bill.get("vendor", {}).get("name", "") if isinstance(bill.get("vendor"), dict) else bill.get("vendor_name", "")).upper()
        bill_amount = abs(D(str(bill.get("amount", 0))))
        amount_due = abs(D(str(bill.get("amount_due", 0))))

        confidence = None
        priority = 99

        # Match 1: Reference/description contains bill number
        if bill_number and (bill_number in ref or bill_number in desc):
            if amount == amount_due or amount == bill_amount:
                confidence, priority = "HIGH", 0
            else:
                confidence, priority = "MEDIUM", 1
        # Match 2: Vendor name + amount match
        elif vendor_name and len(vendor_name) > 2 and vendor_name in desc:
            if amount == amount_due or amount == bill_amount:
                confidence, priority = "MEDIUM", 1
        # Match 3: Amount exact match
        elif amount == amount_due and amount_due > 0:
            confidence, priority = "LOW", 2

        if confidence and priority < best_priority:
            best_priority = priority
            best = {
                "bill_id": bill.get("id"),
                "bill_number": bill.get("invoice_number"),
                "vendor_id": bill.get("vendor_id") or (bill.get("vendor", {}).get("id", "") if isinstance(bill.get("vendor"), dict) else ""),
                "vendor_name": bill.get("vendor_name") or (bill.get("vendor", {}).get("name", "") if isinstance(bill.get("vendor"), dict) else ""),
                "bill_amount": int(bill_amount),
                "amount_due": int(amount_due),
                "confidence": confidence,
            }

    return best


def _prescan_match_invoice(
    amount, desc: str, ref: str, invoices: list[dict]
) -> Optional[Dict]:
    """Match a credit line against invoices. Returns best match or None."""
    from decimal import Decimal as D

    best = None
    best_priority = 99

    for inv in invoices:
        inv_number = (inv.get("invoice_number") or "").upper()
        customer_name = (inv.get("customer_name") or inv.get("customer", {}).get("name", "") if isinstance(inv.get("customer"), dict) else inv.get("customer_name", "")).upper()
        inv_amount = abs(D(str(inv.get("amount", inv.get("total_amount", 0)))))
        amount_due = abs(D(str(inv.get("amount_due", 0))))

        confidence = None
        priority = 99

        # Match 1: Reference/description contains invoice number
        if inv_number and (inv_number in ref or inv_number in desc):
            if amount == amount_due or amount == inv_amount:
                confidence, priority = "HIGH", 0
            else:
                confidence, priority = "MEDIUM", 1
        # Match 2: Customer name + amount match
        elif customer_name and len(customer_name) > 2 and customer_name in desc:
            if amount == amount_due or amount == inv_amount:
                confidence, priority = "MEDIUM", 1
        # Match 3: Amount exact match
        elif amount == amount_due and amount_due > 0:
            confidence, priority = "LOW", 2

        if confidence and priority < best_priority:
            best_priority = priority
            best = {
                "invoice_id": inv.get("id"),
                "invoice_number": inv.get("invoice_number"),
                "customer_id": inv.get("customer_id") or (inv.get("customer", {}).get("id", "") if isinstance(inv.get("customer"), dict) else ""),
                "customer_name": inv.get("customer_name") or (inv.get("customer", {}).get("name", "") if isinstance(inv.get("customer"), dict) else ""),
                "amount_due": int(amount_due),
                "confidence": confidence,
            }

    return best


async def auto_get_summary(ctx: WorkflowContext, call_internal) -> Dict[str, Any]:
    """Auto: get reconciliation summary + pre-scan matching preview.

    Summary is a GET (read-only), safe to re-run.
    Pre-scan fetches unmatched lines + outstanding bills/invoices,
    runs matching to build a preview breakdown for narasi.
    Persists result via ctx._save_fn for crash safety.
    """
    session_id = ctx.data.get("recon_session_id", "")
    if not session_id:
        return {"error": "No recon session ID"}

    try:
        result = await call_internal(
            "GET",
            f"/api/bank-reconciliation/sessions/{session_id}/summary"
        )
        ctx.data["summary"] = result

        # Count unmatched items for review tracking
        unmatched = result.get("unmatched_count", result.get("unmatched_statement_lines", 0))
        ctx.data["unmatched_count"] = unmatched
        ctx.data["reviewed_count"] = 0

        # Pre-scan: match unmatched lines against bills/invoices for preview
        if unmatched > 0 and ctx.auth_token:
            try:
                preview, prematched = await _prescan_unmatched(
                    session_id, ctx.auth_token, max_lines=100
                )
                ctx.data["review_preview"] = preview
                ctx.data["prematched"] = prematched
                result["review_preview"] = preview
                logger.info(f"[RECON] Pre-scan: {preview}")
            except Exception as e:
                logger.warning(f"[RECON] Pre-scan failed (non-fatal): {e}")

        save_fn = getattr(ctx, '_save_fn', None)
        if save_fn:
            await save_fn(ctx)

        return result
    except Exception as e:
        logger.error(f"Auto get summary failed: {e}")
        return {"error": str(e)}


def _build_propose_instruction(
    statement_line: dict,
    bill_suggestion: dict | None,
    invoice_suggestion: dict | None,
    category_suggestion: dict | None,
    bank_account_id: str,
    bank_account_name: str,
    session_id: str,
) -> str:
    """
    Build deterministic LLM instruction for proposing a DirectAction.

    COUPLING NOTE: Field names here must match DirectAction registry configs
    in direct_action_registry.py. Update both if either changes.
    """
    line_id = statement_line.get("id", "")
    line_date = statement_line.get("date", "")
    line_desc = statement_line.get("description", "")
    line_amount = statement_line.get("amount", 0)

    if bill_suggestion:
        return (
            "WAJIB: Langsung panggil propose_direct_action dengan action_key='create_bill_payment'. "
            f"Data: vendor_id='{bill_suggestion.get('vendor_id')}', "
            f"bill_id='{bill_suggestion.get('bill_id')}', "
            f"vendor_name='{bill_suggestion.get('vendor_name')}', "
            f"bill_number='{bill_suggestion.get('bill_number')}', "
            f"bill_amount={bill_suggestion.get('bill_amount')}, "
            f"amount_due={bill_suggestion.get('amount_due')}, "
            f"total_amount={bill_suggestion.get('amount_due')}, "
            f"bank_account_id='{bank_account_id}', "
            f"bank_account_name='{bank_account_name}', "
            f"session_id='{session_id}', "
            f"statement_line_id='{line_id}', "
            f"statement_description='{line_desc}', "
            f"payment_date='{line_date}'. "
            f"Confidence: {bill_suggestion.get('confidence')} — {bill_suggestion.get('reason', bill_suggestion.get('match_reason', ''))}. "
            "Narasi singkat: 'Transaksi ini cocok dengan Faktur [nomor] dari [vendor].' "
            "JANGAN tanya 'mau lanjut?' — langsung propose."
        )

    if invoice_suggestion:
        alloc_type = invoice_suggestion.get("allocation_type", "single")

        if alloc_type == "needs_user_input":
            options = invoice_suggestion.get("options", [])
            options_str = "; ".join(
                f"{o.get('invoice_number')} Rp {o.get('amount_due'):,}" for o in options
            )
            return (
                f"Ada beberapa faktur yang mungkin cocok dengan pembayaran Rp {line_amount:,}: "
                f"{options_str}. "
                "Tanyakan ke user: 'Pembayaran ini untuk faktur yang mana?' "
                "Setelah user jawab, propose create_receive_payment dengan allocations yang sesuai."
            )

        # Single or multi allocation — propose directly
        allocations_data = invoice_suggestion.get("allocations")
        if allocations_data:
            alloc_str = str(allocations_data)
        else:
            alloc_str = (
                f"[{{'invoice_id':'{invoice_suggestion.get('invoice_id')}',"
                f"'amount_applied':{invoice_suggestion.get('amount_due')}}}]"
            )

        return (
            "WAJIB: Langsung panggil propose_direct_action dengan action_key='create_receive_payment'. "
            f"Data: customer_id='{invoice_suggestion.get('customer_id')}', "
            f"customer_name='{invoice_suggestion.get('customer_name')}', "
            f"invoice_numbers='{invoice_suggestion.get('invoice_number', '')}', "
            f"total_amount={invoice_suggestion.get('amount_due', line_amount)}, "
            f"allocations={alloc_str}, "
            f"bank_account_id='{bank_account_id}', "
            f"bank_account_name='{bank_account_name}', "
            f"session_id='{session_id}', "
            f"statement_line_id='{line_id}', "
            f"statement_description='{line_desc}', "
            f"payment_date='{line_date}'. "
            f"Confidence: {invoice_suggestion.get('confidence')} — {invoice_suggestion.get('reason', invoice_suggestion.get('match_reason', ''))}. "
            "Narasi: 'Pembayaran ini cocok dengan Faktur [nomor] dari [pelanggan].' "
            "JANGAN tanya — langsung propose."
        )

    if category_suggestion:
        return (
            "WAJIB: Langsung panggil propose_direct_action dengan action_key='categorize_statement'. "
            f"Data: account_id='{category_suggestion.get('account_id')}', "
            f"account_code='{category_suggestion.get('account_code')}', "
            f"account_name='{category_suggestion.get('account_name')}', "
            f"session_id='{session_id}', "
            f"statement_line_id='{line_id}', "
            f"statement_description='{line_desc}', "
            f"amount={line_amount}. "
            f"Pattern matched: {category_suggestion.get('pattern_matched')}. "
            "Narasi: 'Transaksi ini terdeteksi sebagai [kategori].' "
            "JANGAN tanya — langsung propose."
        )

    # No match — let LLM decide (categorize or ask user)
    return (
        f"Tidak ada match otomatis untuk transaksi: '{line_desc}' Rp {line_amount:,}. "
        "Pilihan: "
        "1) Propose categorize_statement jika kamu bisa identifikasi kategorinya dari deskripsi. "
        "2) Tanyakan user apa transaksi ini. "
        "JANGAN describe panjang lebar — langsung propose atau tanya singkat."
    )


def _build_summary_narasi(ctx: WorkflowContext) -> str:
    """Build rich narasi for the first review item, using pre-scan data from ctx.data.
    Only called when reviewed_count == 0 (first item)."""
    summary = ctx.data.get("summary", {})
    rp = ctx.data.get("review_preview", {})
    account_name = ctx.data.get("bank_account_name", "") or ctx.data.get("account_name", "rekening bank")

    total_lines = summary.get("total_statement_lines", summary.get("total_lines", 0))
    matched = summary.get("matched_count", summary.get("auto_matched", 0))
    unmatched = ctx.data.get("unmatched_count", 0)

    parts = []

    # Intro — natural tone
    parts.append(f"Oke, rekening koran sudah diproses. Ada {total_lines} transaksi di {account_name}.")
    if matched > 0:
        parts.append(f"{matched} transaksi langsung cocok dengan data di sistem.")
    if unmatched == 1:
        parts.append(f"Masih ada 1 transaksi yang perlu ditinjau manual.")
    elif unmatched > 1:
        parts.append(f"Masih ada {unmatched} transaksi yang perlu ditinjau manual.")

    # Breakdown from pre-scan
    breakdown_lines = []
    bill_count = rp.get("bill_match", 0)
    invoice_count = rp.get("invoice_match", 0)
    no_match_count = rp.get("no_match", 0)

    if bill_count > 0:
        breakdown_lines.append(f"• {bill_count} kemungkinan cocok dengan tagihan vendor.")
    if invoice_count > 0:
        breakdown_lines.append(f"• {invoice_count} kemungkinan cocok dengan invoice pelanggan.")
    if no_match_count > 0:
        breakdown_lines.append(f"• {no_match_count} perlu kategorisasi manual.")

    if breakdown_lines:
        parts.append("Penilaian awal:\n" + "\n".join(breakdown_lines))

    parts.append("Mari kita review satu per satu.")
    return "\n\n".join(parts)


async def auto_next_review(ctx: WorkflowContext, call_internal) -> Dict[str, Any]:
    """Auto: get next unmatched item, build deterministic DirectAction instruction."""
    session_id = ctx.data.get("recon_session_id", "")
    if not session_id:
        return {"error": "No recon session ID"}

    try:
        execute_tool = getattr(ctx, '_execute_tool', None)
        if not execute_tool:
            return {"error": "No execute_tool callback", "message": "Internal error: cannot call review_next_unmatched"}

        result = await execute_tool("review_next_unmatched", {
            "session_id": session_id,
            "skip": 0,
        })

        data = result.get("data", result) if isinstance(result, dict) else result

        if isinstance(data, dict):
            has_more = data.get("has_more", True)
            if not has_more:
                ctx.data["review_complete"] = True
                return {"review_complete": True, "message": "Semua item sudah di-review."}

        # Context from workflow state
        # "account_id" is set by ReconShortcut + IDENTIFY_ACCOUNT gate
        # "bank_account_id" may be set by other paths — try both
        bank_account_id = ctx.data.get("bank_account_id", "") or ctx.data.get("account_id", "")
        bank_account_name = ctx.data.get("bank_account_name", "") or ctx.data.get("account_name", "")
        statement_line = data.get("statement_line", {}) if isinstance(data, dict) else {}

        # Check prematched cache before live matching
        bill_suggestion = None
        invoice_suggestion = None
        category_suggestion = None
        line_id = statement_line.get("id", "")
        prematched = ctx.data.get("prematched", {})
        cached = prematched.get(line_id) if line_id else None

        if cached and cached.get("type") not in ("no_match", None):
            # Use cached pre-scan result
            cache_type = cached["type"]
            cache_data = cached.get("data", {})
            if cache_type == "bill_match" and cache_data:
                bill_suggestion = cache_data
                logger.info(f"[RECON] Prematched hit for line {line_id[:8]}: bill_match")
            elif cache_type == "invoice_match" and cache_data:
                invoice_suggestion = cache_data
                logger.info(f"[RECON] Prematched hit for line {line_id[:8]}: invoice_match")
        else:
            # No cache or no_match — use live suggestions from tool_executor
            bill_suggestion = data.get("bill_suggestion") if isinstance(data, dict) else None
            invoice_suggestion = data.get("invoice_suggestion") if isinstance(data, dict) else None
            category_suggestion = data.get("category_suggestion") if isinstance(data, dict) else None

        # Layer 2: Build deterministic instruction
        instruction = _build_propose_instruction(
            statement_line=statement_line,
            bill_suggestion=bill_suggestion,
            invoice_suggestion=invoice_suggestion,
            category_suggestion=category_suggestion,
            bank_account_id=bank_account_id,
            bank_account_name=bank_account_name,
            session_id=session_id,
        )

        # Item counter: "Item X/Y"
        reviewed = ctx.data.get("reviewed_count", 0)
        total_unmatched = ctx.data.get("unmatched_count", 0)
        item_number = reviewed + 1
        item_counter = f"Item {item_number}/{total_unmatched}" if total_unmatched > 0 else ""

        # First review item: prepend summary narasi
        if reviewed == 0:
            narasi = _build_summary_narasi(ctx)
            instruction = f"{narasi}\n\n{item_counter}:\n{instruction}"
        elif item_counter:
            instruction = f"{item_counter}:\n{instruction}"

        return {
            "review_item": data,
            "bill_suggestion": bill_suggestion,
            "invoice_suggestion": invoice_suggestion,
            "category_suggestion": category_suggestion,
            "instruction": instruction,
            "item_counter": item_counter,
            # Include workflow context so auto-propose has all it needs
            "session_id": session_id,
            "bank_account_id": bank_account_id,
            "bank_account_name": bank_account_name,
            "reviewed_count": reviewed,
        }
    except Exception as e:
        logger.error(f"Auto next review failed: {e}")
        return {"error": str(e)}


async def auto_balance_proof(ctx: WorkflowContext, call_internal) -> Dict[str, Any]:
    """Auto: generate balance proof from summary."""
    session_id = ctx.data.get("recon_session_id", "")
    if not session_id:
        return {"error": "No recon session ID"}

    try:
        result = await call_internal(
            "GET",
            f"/api/bank-reconciliation/sessions/{session_id}/summary"
        )
        ctx.data["balance_proof"] = result
        return {
            "balance_proof": result,
            "instruction": "Tampilkan balance proof ke user dalam format yang mudah dibaca. "
                           "Bandingkan saldo akhir rekening koran dengan saldo buku setelah penyesuaian."
        }
    except Exception as e:
        logger.error(f"Auto balance proof failed: {e}")
        return {"error": str(e)}


async def auto_finalize(ctx: WorkflowContext, call_internal) -> Dict[str, Any]:
    """Auto: complete the reconciliation session."""
    session_id = ctx.data.get("recon_session_id", "")
    if not session_id:
        return {"error": "No recon session ID"}

    try:
        result = await call_internal(
            "POST",
            f"/api/bank-reconciliation/sessions/{session_id}/complete",
            {}
        )
        ctx.data["finalize_result"] = result
        return {
            "finalized": True,
            "result": result,
            "message": "Rekonsiliasi bank berhasil diselesaikan!"
        }
    except Exception as e:
        logger.error(f"Auto finalize failed: {e}")
        return {"error": str(e)}




async def prompt_finalize(ctx: WorkflowContext, call_internal) -> Dict[str, Any]:
    """Show finalization summary and ask user to confirm completion."""
    bp = ctx.data.get("balance_proof", {})
    matched = bp.get("matched_count", 0)
    total = bp.get("total_statement_lines", 0)
    unmatched = bp.get("unmatched_count", 0)
    difference = bp.get("difference", 0)

    parts = []
    if unmatched == 0:
        parts.append(f"Semua {matched} transaksi sudah cocok.")
    else:
        parts.append(f"{matched} dari {total} transaksi sudah dicocokkan, {unmatched} dilewati.")

    if difference and float(difference) != 0:
        diff_fmt = f"Rp {int(abs(float(difference))):,}".replace(",", ".")
        parts.append(f"Selisih saldo: {diff_fmt}.")

    parts.append("\nSelesaikan rekonsiliasi ini?")

    ctx.data["awaiting_user_approval"] = True

    return {
        "message": " ".join(parts),
        "balance_proof": bp,
        "instruction": " ".join(parts),
    }


# ============ DOC REVIEW AUTO-EXECUTE FUNCTIONS ============

async def auto_fetch_document(ctx: WorkflowContext, call_internal) -> Dict[str, Any]:
    """Fetch document details from document-intake API."""
    doc_id = ctx.data.get("document_id")
    if not doc_id:
        return {
            "error": "document_id missing",
            "instruction": "Dokumen tidak ditemukan. Minta user memberikan ID dokumen yang valid.",
        }

    try:
        resp = await call_internal("GET", f"/api/document-intake/document/{doc_id}")
    except Exception as e:
        return {
            "error": str(e),
            "instruction": f"Gagal mengambil dokumen {doc_id}: {e}",
        }

    if not resp or resp.get("detail"):
        return {
            "error": "not_found",
            "instruction": f"Dokumen {doc_id} tidak ditemukan atau belum siap review.",
        }

    # Store document in context
    doc = resp.get("document") or resp.get("data") or resp
    ctx.data["document"] = doc
    ctx.data["doc_type"] = doc.get("doc_type", "unknown")
    ctx.data["original_filename"] = doc.get("original_filename", "")
    ctx.data["status"] = doc.get("status", "")
    ctx.data["draft_plan"] = doc.get("draft_plan")
    ctx.data["ocr_result"] = doc.get("ocr_result")

    return {"instruction": "Dokumen dimuat.", "document_loaded": True}


async def auto_present_draft(ctx: WorkflowContext, call_internal) -> Dict[str, Any]:
    """Build draft summary and suggest confirm DirectAction."""
    draft = ctx.data.get("draft_plan")
    if not draft:
        return {
            "instruction": "Dokumen ini belum punya draft plan. "
                           "Informasikan user bahwa dokumen masih diproses atau gagal dianalisis."
        }

    journal = draft.get("journal_draft", {})
    lines = journal.get("lines", [])
    desc = journal.get("description", "")
    total_debit = journal.get("total_debit", "0")
    total_credit = journal.get("total_credit", "0")
    is_balanced = journal.get("is_balanced", False)
    counterparty = draft.get("counterparty") or {}
    counterparty_name = counterparty.get("name", "") if isinstance(counterparty, dict) else ""
    confidence = draft.get("overall_confidence", 0)
    warnings = draft.get("warnings", [])
    requires_input = draft.get("requires_user_input", False)

    # Build confirm payload for auto-propose
    conf_pct = int(confidence * 100) if confidence and confidence <= 1 else int(confidence or 0)
    ctx.data["confirm_suggestion"] = {
        "action_key": "confirm_document_draft",
        "payload": {
            "document_id": ctx.data.get("document_id", ""),
            "document_title": desc or ctx.data.get("original_filename", ""),
            "doc_type": ctx.data.get("doc_type", ""),
            "counterparty_name": counterparty_name,
            "journal_description": desc,
            "total_debit": total_debit,
            "total_credit": total_credit,
            "confidence": f"{conf_pct}%",
        }
    }

    # Build lines table for LLM instruction
    lines_table = "| Akun | Kode | Debit | Kredit |\n|------|------|-------|--------|\n"
    for line in lines:
        lines_table += (
            f"| {line.get('account_name', '')} "
            f"| {line.get('account_code', '')} "
            f"| {line.get('debit', '0')} "
            f"| {line.get('credit', '0')} |\n"
        )

    warning_text = ""
    if warnings:
        warning_text = "\n⚠️ Peringatan:\n" + "\n".join(f"- {w}" for w in warnings)

    balance_icon = "✅ Balanced" if is_balanced else "❌ TIDAK BALANCED"
    input_note = "\n⚠️ Dokumen ini butuh input tambahan dari user." if requires_input else ""

    instruction = (
        f"Presentasikan draft jurnal ini ke user:\n\n"
        f"**{desc}**\n"
        f"Tipe: {ctx.data.get('doc_type', '')} | File: {ctx.data.get('original_filename', '')}\n"
        f"Confidence: {conf_pct}%\n"
        f"Pihak: {counterparty_name}\n\n"
        f"{lines_table}\n"
        f"Total: Debit {total_debit} | Kredit {total_credit} | {balance_icon}\n"
        f"{warning_text}{input_note}\n\n"
        f"Propose confirm_document_draft agar user bisa konfirmasi atau tolak."
    )

    return {"instruction": instruction, "confirm_suggestion": ctx.data["confirm_suggestion"]}


# ============ INVOICE + PAYMENT AUTO-EXECUTE ============

async def auto_create_invoice_proposal(ctx: WorkflowContext, call_internal) -> Dict[str, Any]:
    """Build invoice proposal from workflow data."""
    customer_id = ctx.data.get("customer_id", "")
    customer_name = ctx.data.get("customer_name", "")
    items = ctx.data.get("items", [])
    tax_rate = ctx.data.get("tax_rate", 0)
    invoice_date = ctx.data.get("date", date_type.today().isoformat())

    return {
        "confirm_suggestion": {
            "action_key": "create_sales_invoice",
            "payload": {
                "customer_id": customer_id,
                "customer_name": customer_name,
                "items": items,
                "invoice_date": invoice_date,
                "due_date": ctx.data.get("due_date", ""),
                "tax_rate": tax_rate,
                "auto_post": True,
            },
        },
        "instruction": f"Buatkan faktur penjualan untuk {customer_name}.",
    }


async def auto_create_payment_proposal(ctx: WorkflowContext, call_internal) -> Dict[str, Any]:
    """Build payment proposal after invoice is confirmed."""
    invoice_id = ctx.data.get("invoice_id", "")
    invoice_number = ctx.data.get("invoice_number", "")
    customer_id = ctx.data.get("customer_id", "")
    customer_name = ctx.data.get("customer_name", "")
    total_amount = ctx.data.get("invoice_total", 0)
    bank_account_id = ctx.data.get("bank_account_id", "")
    bank_account_name = ctx.data.get("bank_account_name", "")

    return {
        "confirm_suggestion": {
            "action_key": "create_receive_payment",
            "payload": {
                "customer_id": customer_id,
                "customer_name": customer_name,
                "invoice_numbers": invoice_number,
                "total_amount": total_amount,
                "allocations": [{"invoice_id": invoice_id, "amount_applied": total_amount}],
                "bank_account_id": bank_account_id,
                "bank_account_name": bank_account_name,
                "payment_date": date_type.today().isoformat(),
                "payment_method": "bank_transfer",
            },
        },
        "instruction": f"Faktur {invoice_number} sudah dibuat. Sekarang catat pembayarannya.",
    }


# ============ MONTHLY CLOSING AUTO-EXECUTE ============

async def auto_check_drafts(ctx: WorkflowContext, call_internal) -> Dict[str, Any]:
    """Check for draft invoices and bills."""
    try:
        inv_result = await call_internal("GET", "/api/sales-invoices/summary")
        draft_invoices = inv_result.get("draft_count", 0)
        bill_result = await call_internal("GET", "/api/bills/outstanding-summary")
        draft_bills = bill_result.get("breakdown", {}).get("draft", {}).get("count", 0)
        ctx.data["draft_invoice_count"] = draft_invoices
        ctx.data["draft_bill_count"] = draft_bills
        parts = []
        if draft_invoices > 0:
            parts.append(f"{draft_invoices} faktur penjualan masih draft")
        if draft_bills > 0:
            parts.append(f"{draft_bills} faktur pembelian masih draft")
        if parts:
            joined = ", ".join(parts)
            return {
                "draft_invoice_count": draft_invoices,
                "draft_bill_count": draft_bills,
                "instruction": f"Masih ada {joined}. Posting dulu sebelum tutup bulan.",
                "has_drafts": True,
            }
        return {
            "draft_invoice_count": 0,
            "draft_bill_count": 0,
            "instruction": "Tidak ada dokumen draft. Lanjut cek rekonsiliasi.",
            "has_drafts": False,
        }
    except Exception as e:
        logger.warning(f"[CLOSING] Check drafts failed: {e}")
        ctx.data["draft_invoice_count"] = 0
        ctx.data["draft_bill_count"] = 0
        return {"instruction": "Tidak bisa cek draft (lanjut saja).", "has_drafts": False}


async def auto_check_recon(ctx: WorkflowContext, call_internal) -> Dict[str, Any]:
    """Check bank reconciliation status."""
    try:
        bank_result = await call_internal("GET", "/api/bank-accounts")
        accounts = bank_result.get("items", bank_result.get("data", []))
        if not isinstance(accounts, list):
            accounts = []
        unrecon_accounts = []
        for acc in accounts:
            if acc.get("is_active"):
                unrecon_accounts.append(acc.get("account_name", "Unknown"))
        ctx.data["bank_account_count"] = len(unrecon_accounts)
        return {
            "bank_accounts": unrecon_accounts,
            "instruction": f"Ada {len(unrecon_accounts)} rekening bank aktif. Lanjut generate laporan.",
        }
    except Exception as e:
        logger.warning(f"[CLOSING] Check recon failed: {e}")
        return {"instruction": "Tidak bisa cek rekonsiliasi (lanjut saja)."}


async def auto_generate_reports(ctx: WorkflowContext, call_internal) -> Dict[str, Any]:
    """Generate P&L and balance sheet for the period."""
    period = ctx.data.get("period", "")
    try:
        pl_result = await call_internal("GET", f"/api/reports/laba-rugi/{period}")
        revenue = pl_result.get("total_pendapatan", 0)
        expenses = pl_result.get("total_beban", 0)
        net_income = pl_result.get("laba_bersih", 0)
        bs_result = await call_internal("GET", f"/api/reports/neraca/{period}")
        total_assets = bs_result.get("total_aset", 0)
        is_balanced = bs_result.get("is_balanced", True)
        ctx.data["report_summary"] = {
            "revenue": revenue,
            "expenses": expenses,
            "net_income": net_income,
            "total_assets": total_assets,
            "is_balanced": is_balanced,
        }
        balance_status = "Neraca seimbang" if is_balanced else "Neraca TIDAK seimbang"
        def fmt(x):
            return f"Rp {int(x):,}".replace(",", ".")
        return {
            "report_summary": ctx.data["report_summary"],
            "instruction": (
                f"Laporan keuangan periode {period}:\n"
                f"- Pendapatan: {fmt(revenue)}\n"
                f"- Beban: {fmt(expenses)}\n"
                f"- Laba Bersih: {fmt(net_income)}\n"
                f"- Total Aset: {fmt(total_assets)}\n"
                f"- {balance_status}\n\n"
                f"Mau tutup periode {period}?"
            ),
        }
    except Exception as e:
        logger.warning(f"[CLOSING] Generate reports failed: {e}")
        return {"instruction": f"Gagal generate laporan: {e}. Mau coba lagi?"}


async def auto_close_period(ctx: WorkflowContext, call_internal) -> Dict[str, Any]:
    """Close the accounting period."""
    period = ctx.data.get("period", "")
    try:
        result = await call_internal("POST", f"/api/periods/{period}/close", {})
        return {
            "closed": True,
            "instruction": f"Periode {period} berhasil ditutup.",
        }
    except Exception as e:
        logger.warning(f"[CLOSING] Close period failed: {e}")
        return {
            "closed": False,
            "instruction": f"Gagal tutup periode: {e}",
        }


# ─── Transition Table ─────────────────────────────────────────────────────


# ============ CRUD FORM CHECK + AUTO-EXECUTE FUNCTIONS ============

async def check_crud_fields_complete(ctx: WorkflowContext, user_data: dict) -> Tuple[bool, str]:
    """Gate: are all required fields present for the CRUD action?"""
    from .direct_action_registry import validate_payload, get_direct_action

    action_key = ctx.data.get("action_key", "")
    payload = ctx.data.get("payload", {})


    # ── UPDATE flow: different gate logic ──
    if action_key.startswith("update_"):
        phase = ctx.data.get("phase", "")
        if phase in ("showing_current", ""):
            # Check if user provided any field to change (beyond just id/name identifiers)
            changed = {k: v for k, v in payload.items()
                      if k not in ("id", "date", "name", "item_name", "customer_name", "vendor_name", "warehouse_name", "bank_name")
                      and v is not None and v != ""}
            if changed:
                ctx.data["phase"] = "ready"
                return (True, "")
            else:
                entity_name = ctx.data.get("entity_name", "item")
                current = ctx.data.get("current_data", {})
                # Build instruction for LLM clarification
                display_parts = []
                for k, v in current.items():
                    if v is not None and v != "" and v != 0:
                        display_parts.append(f"  - {k}: {v}")
                instruction = f"Data {entity_name} sudah ditampilkan ke user. Tanya: 'Mau ubah yang mana?'"
                return (False, instruction)

    is_valid, missing = validate_payload(action_key, payload)
    if is_valid:
        return (True, "")

    # Build dynamic instruction with collected + missing context
    config = get_direct_action(action_key)
    lines = []

    # Show collected fields
    collected_lines = []
    if config and config.fields:
        for f in config.fields:
            if f.hidden or f.display_only:
                continue
            val = payload.get(f.name)
            if val is not None and str(val).strip():
                collected_lines.append(f"  \u2713 {f.label}: {val}")
    if collected_lines:
        lines.append("Data yang sudah terkumpul:")
        lines.extend(collected_lines)
        lines.append("")

    # Show missing fields with hints
    lines.append("Tanyakan field berikut ke user (natural, singkat, sebutkan data yang sudah ada supaya user tahu konteksnya):")
    if config and config.fields:
        for f in config.fields:
            if f.label in missing or f.name in missing:
                hint_parts = []
                if f.options:
                    hint_parts.append(", ".join(f.options[:6]))
                if f.description:
                    hint_parts.append(f.description)
                hint = " \u2014 ".join(hint_parts) if hint_parts else ""
                line = f"- {f.label}"
                if hint:
                    line += f" ({hint})"
                lines.append(line)
    else:
        for m in missing:
            lines.append(f"- {m}")

    instruction = "\n".join(lines)
    return (False, instruction)


async def auto_propose_direct(ctx: WorkflowContext, call_internal) -> Dict[str, Any]:
    """Auto: propose the CRUD action via tool executor."""
    action_key = ctx.data.get("action_key", "")
    payload = ctx.data.get("payload", {})

    execute_tool = getattr(ctx, "_execute_tool", None)
    if execute_tool:
        result = await execute_tool("propose_direct", {
            "action_key": action_key,
            "payload": payload,
        })
        return {"instruction": "", "propose_result": result}

    return {"error": "No execute_tool callback available"}


RECON_TRANSITIONS = {
    "IDENTIFY_ACCOUNT": {
        "check": check_account_identified,
        "next": "CHECK_EXISTING",
        "not_ready_instruction": "Tanyakan nama atau nomor rekening bank ke user. Gunakan get_bank_accounts untuk lookup.",
    },
    "CHECK_EXISTING": {
        "check": check_always_pass,
        "auto_execute": auto_check_existing,
        "next": "NEED_BALANCE",
        "not_ready_instruction": "",
    },
    "NEED_BALANCE": {
        "check": check_has_balance,
        "next": "NEED_FILE",
        "not_ready_instruction": "Tanyakan saldo akhir rekening koran ke user. Angka ini WAJIB sebelum bisa lanjut.",
    },
    "NEED_FILE": {
        "check": check_has_file_or_nofile,
        "next": "IMPORTING",
        "not_ready_instruction": "Tanya ke user: Upload file rekening koran (CSV/XLSX/OFX), atau lanjut tanpa file (mode manual)?",
    },
    "IMPORTING": {
        "check": check_always_pass,
        "auto_execute": auto_create_session_and_import,
        "next": "MATCHING",
        "not_ready_instruction": "",
    },
    "MATCHING": {
        "check": check_always_pass,
        "auto_execute": auto_match,
        "next": "SHOW_SUMMARY",
        "not_ready_instruction": "",
    },
    "SHOW_SUMMARY": {
        "check": check_always_pass,
        "auto_execute": auto_get_summary,
        "next": "REVIEWING",
        "not_ready_instruction": "",
    },
    "REVIEWING": {
        "check": check_review_complete,
        "not_ready_action": auto_next_review,
        "next": "BALANCE_PROOF",
        "not_ready_instruction": "Masih ada {missing}.",
    },
    "BALANCE_PROOF": {
        "check": check_always_pass,
        "auto_execute": auto_balance_proof,
        "next": "FINALIZE",
        "not_ready_instruction": "",
    },
    "FINALIZE": {
        "check": check_can_complete,
        "auto_execute": auto_finalize,
        "not_ready_action": prompt_finalize,
        "next": "COMPLETED",
        "not_ready_instruction": "Rekonsiliasi belum bisa diselesaikan: {missing}. Selesaikan dulu sebelum finalisasi.",
    },
}


DOC_REVIEW_TRANSITIONS = {
    "FETCH_DOCUMENT": {
        "check": check_has_document_id,
        "auto_execute": auto_fetch_document,
        "next": "PRESENT_DRAFT",
        "not_ready_instruction": "Minta user untuk memberikan ID dokumen yang akan di-review.",
    },
    "PRESENT_DRAFT": {
        "check": check_has_draft_plan,
        "auto_execute": auto_present_draft,
        "next": "AWAITING_DECISION",
        "not_ready_instruction": "Dokumen belum memiliki draft plan. Informasikan user.",
    },
    "AWAITING_DECISION": {
        "check": check_always_pass,
        "next": "POSTING",
        "not_ready_instruction": "",
    },
    "POSTING": {
        "check": check_always_pass,
        "next": "POSTED",
        "not_ready_instruction": "",
    },
    "POSTED": {
        "check": check_always_pass,
        "next": "COMPLETED",
        "not_ready_instruction": "",
    },
    "POSTING_FAILED": {
        "check": check_always_pass,
        "next": None,
        "not_ready_instruction": "Posting gagal. Informasikan user tentang error.",
    },
    "COMPLETED": {
        "check": check_always_pass,
        "next": None,
        "not_ready_instruction": "",
    },
}


INVOICE_PAYMENT_TRANSITIONS = {
    "CREATE_INVOICE": {
        "check": check_has_invoice_data,
        "auto_execute": auto_create_invoice_proposal,
        "next": "AWAIT_INVOICE_CONFIRM",
        "not_ready_instruction": "Data faktur belum lengkap. Butuh: {missing}.",
    },
    "AWAIT_INVOICE_CONFIRM": {
        "check": check_invoice_created,
        "next": "CREATE_PAYMENT",
        "not_ready_instruction": "Menunggu konfirmasi faktur dari user.",
    },
    "CREATE_PAYMENT": {
        "check": check_payment_data_ready,
        "auto_execute": auto_create_payment_proposal,
        "next": "AWAIT_PAYMENT_CONFIRM",
        "not_ready_instruction": "Butuh rekening bank untuk pembayaran. Rekening mana?",
    },
    "AWAIT_PAYMENT_CONFIRM": {
        "check": check_always_pass,
        "next": "COMPLETED",
        "not_ready_instruction": "",
    },
}

MONTHLY_CLOSING_TRANSITIONS = {
    "CHECK_DRAFTS": {
        "check": check_has_period,
        "auto_execute": auto_check_drafts,
        "next": "CHECK_RECONCILIATION",
        "not_ready_instruction": "Periode mana yang mau ditutup? Format: YYYY-MM (contoh: 2026-03).",
    },
    "CHECK_RECONCILIATION": {
        "check": check_drafts_clear,
        "auto_execute": auto_check_recon,
        "next": "GENERATE_REPORTS",
        "not_ready_instruction": "{missing}. Posting semua draft dulu sebelum tutup bulan.",
    },
    "GENERATE_REPORTS": {
        "check": check_always_pass,
        "auto_execute": auto_generate_reports,
        "next": "PRESENT_SUMMARY",
        "not_ready_instruction": "",
    },
    "PRESENT_SUMMARY": {
        "check": check_always_pass,
        "next": "CLOSE_PERIOD",
        "not_ready_instruction": "",
    },
    "CLOSE_PERIOD": {
        "check": check_user_approved_close,
        "auto_execute": auto_close_period,
        "next": "COMPLETED",
        "not_ready_instruction": "Konfirmasi: tutup periode ini? (Setelah ditutup, transaksi di periode ini tidak bisa diubah)",
    },
    "AWAIT_CLOSE_CONFIRM": {
        "check": check_always_pass,
        "next": "COMPLETED",
        "not_ready_instruction": "",
    },
}


CRUD_FORM_TRANSITIONS = {
    "COLLECTING": {
        "check": check_crud_fields_complete,
        "next": "PROPOSING",
        "not_ready_instruction": "",  # Dynamic from check fn
    },
    "PROPOSING": {
        "check": check_always_pass,
        "next": "COMPLETED",
        "not_ready_instruction": "",
    },
}

# ============ WORKFLOW TYPE DISPATCH ============
WORKFLOW_TRANSITIONS = {
    "bank_reconciliation": {
        "transitions": RECON_TRANSITIONS,
        "initial_state": "IDENTIFY_ACCOUNT",
    },
    "document_review": {
        "transitions": DOC_REVIEW_TRANSITIONS,
        "initial_state": "FETCH_DOCUMENT",
    },
    "invoice_and_payment": {
        "transitions": INVOICE_PAYMENT_TRANSITIONS,
        "initial_state": "CREATE_INVOICE",
    },
    "monthly_closing": {
        "transitions": MONTHLY_CLOSING_TRANSITIONS,
        "initial_state": "CHECK_DRAFTS",
    },
    "crud_form": {
        "transitions": CRUD_FORM_TRANSITIONS,
        "initial_state": "COLLECTING",
    },
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Module-level state instructions (extracted for assertion coverage)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

STATE_INSTRUCTIONS = {
    # Bank reconciliation states
    "IDENTIFY_ACCOUNT": "Tanyakan nama/nomor rekening bank ke user.",
    "CHECK_EXISTING": "Memeriksa sesi rekonsiliasi yang sudah ada...",
    "NEED_BALANCE": "Tanyakan saldo akhir rekening koran.",
    "NEED_FILE": "Tanya: upload file rekening koran atau lanjut tanpa file?",
    "IMPORTING": "Mengimpor data rekening koran...",
    "MATCHING": "Mencocokkan transaksi otomatis...",
    "SHOW_SUMMARY": "Tampilkan ringkasan import ke user.",
    "REVIEWING": "Lanjutkan review item berikutnya.",
    "BALANCE_PROOF": "Memeriksa keseimbangan saldo...",
    "FINALIZE": "Menyelesaikan rekonsiliasi...",
    "COMPLETED": "Workflow selesai.",
    # Document review states
    "FETCH_DOCUMENT": "Mengambil detail dokumen...",
    "PRESENT_DRAFT": "Menyajikan draft jurnal...",
    "AWAITING_DECISION": "Menunggu keputusan user untuk konfirmasi atau tolak.",
    "POSTING": "Memposting jurnal ke ledger...",
    "POSTED": "Dokumen berhasil diposting ke ledger.",
    "POSTING_FAILED": "Posting gagal. Periksa error.",
    # Invoice + Payment states
    "CREATE_INVOICE": "Membuat faktur penjualan...",
    "AWAIT_INVOICE_CONFIRM": "Menunggu konfirmasi faktur.",
    "CREATE_PAYMENT": "Menyiapkan pembayaran...",
    "AWAIT_PAYMENT_CONFIRM": "Menunggu konfirmasi pembayaran.",
    # Monthly closing states
    "CHECK_DRAFTS": "Memeriksa dokumen draft...",
    "CHECK_RECONCILIATION": "Memeriksa rekonsiliasi bank...",
    "GENERATE_REPORTS": "Membuat laporan keuangan...",
    "PRESENT_SUMMARY": "Menampilkan ringkasan...",
    "CLOSE_PERIOD": "Menutup periode...",
    "AWAIT_CLOSE_CONFIRM": "Menunggu konfirmasi penutupan.",
    # CRUD form states
    "COLLECTING": "Mengumpulkan data dari user.",
    "PROPOSING": "Menyiapkan konfirmasi.",
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SAFETY: Verify all states have instructions + transitions
# Fails FAST at import time if a state is missing
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _verify_state_instructions_complete():
    """Assert every non-terminal state has an instruction entry."""
    all_recon = set(s.value for s in ReconState)
    all_doc = set(s.value for s in DocReviewState)
    all_inv = set(s.value for s in InvoicePaymentState)
    all_closing = set(s.value for s in MonthlyClosingState)
    all_crud = set(s.value for s in CrudFormState)
    all_states = all_recon | all_doc | all_inv | all_closing | all_crud
    handled = set(STATE_INSTRUCTIONS.keys())
    missing = all_states - handled
    assert not missing, (
        f"[FATAL] STATE_INSTRUCTIONS missing states: {missing}. "
        f"Every state MUST have an instruction entry."
    )

def _verify_transitions_complete():
    """Every non-terminal state must have a transition rule."""
    recon_non_terminal = set(s.value for s in ReconState if s != ReconState.COMPLETED)
    recon_transition_states = set(RECON_TRANSITIONS.keys())
    missing_recon = recon_non_terminal - recon_transition_states
    assert not missing_recon, (
        f"[FATAL] RECON_TRANSITIONS missing states: {missing_recon}. "
        f"Every non-terminal ReconState MUST have a transition entry."
    )
    doc_non_terminal = set(s.value for s in DocReviewState if s != DocReviewState.COMPLETED)
    doc_transition_states = set(DOC_REVIEW_TRANSITIONS.keys())
    missing_doc = doc_non_terminal - doc_transition_states
    assert not missing_doc, (
        f"[FATAL] DOC_REVIEW_TRANSITIONS missing states: {missing_doc}. "
        f"Every non-terminal DocReviewState MUST have a transition entry."
    )

    inv_non_terminal = set(s.value for s in InvoicePaymentState if s != InvoicePaymentState.COMPLETED)
    inv_transition_states = set(INVOICE_PAYMENT_TRANSITIONS.keys())
    missing_inv = inv_non_terminal - inv_transition_states
    assert not missing_inv, (
        f"[FATAL] INVOICE_PAYMENT_TRANSITIONS missing states: {missing_inv}. "
        f"Every non-terminal InvoicePaymentState MUST have a transition entry."
    )
    closing_non_terminal = set(s.value for s in MonthlyClosingState if s != MonthlyClosingState.COMPLETED)
    closing_transition_states = set(MONTHLY_CLOSING_TRANSITIONS.keys())
    missing_closing = closing_non_terminal - closing_transition_states
    assert not missing_closing, (
        f"[FATAL] MONTHLY_CLOSING_TRANSITIONS missing states: {missing_closing}. "
        f"Every non-terminal MonthlyClosingState MUST have a transition entry."
    )

    crud_non_terminal = set(s.value for s in CrudFormState if s != CrudFormState.COMPLETED)
    crud_transition_states = set(CRUD_FORM_TRANSITIONS.keys())
    missing_crud = crud_non_terminal - crud_transition_states
    assert not missing_crud, (
        f"[FATAL] CRUD_FORM_TRANSITIONS missing states: {missing_crud}. "
        f"Every non-terminal CrudFormState MUST have a transition entry."
    )

_verify_state_instructions_complete()
_verify_transitions_complete()


# ─── Workflow Engine ──────────────────────────────────────────────────────

class WorkflowEngine:
    """
    Deterministic workflow controller.

    LLM calls start_workflow with extracted user_data.
    Engine checks gates, auto-executes, chains states, and returns instructions.
    """

    def __init__(self, db_pool, tenant_id: str, user_id: str, auth_token: str, execute_tool=None):
        self.db = db_pool
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.auth_token = auth_token
        self._execute_tool = execute_tool  # Callback to tool executor for complex ops (file import)

    async def process(self, chat_session_id: str, workflow_type: str, user_data: dict) -> StepResult:
        """
        Main entry: advance workflow based on user_data.

        1. Load or create workflow context
        2. Merge new user_data
        3. Check gate for current state
        4. If gate passes: auto-execute (if defined), advance to next state
        5. Chain through auto-execute states
        6. Return StepResult with llm_instruction
        """
        ctx = await self._load_or_create(chat_session_id, workflow_type)
        ctx.auth_token = self.auth_token
        ctx._execute_tool = self._execute_tool  # Pass tool executor callback
        ctx._save_fn = self._save  # Allow auto-execute to persist mid-operation

        # Merge new data (don't overwrite existing with None)
        for k, v in user_data.items():
            if v is not None:
                # FIX_WF_DEEPMERGE (2026-06-15): the `payload` key accumulates the
                # CRUD fields across turns. A slot-fill answer (e.g. only due_date)
                # arrives as a partial payload; the old wholesale assignment
                # clobbered previously-collected fields (dropping items, vendor,
                # etc.). Deep-merge so the answer ADDS fields without dropping prior
                # ones. Incoming non-None sub-values still win. All other keys keep
                # their original replace-on-not-None behavior.
                if k == "payload" and isinstance(v, dict):
                    _existing_payload = ctx.data.get("payload")
                    if isinstance(_existing_payload, dict):
                        _merged_payload = dict(_existing_payload)
                        for _pk, _pv in v.items():
                            if _pv is not None:
                                _merged_payload[_pk] = _pv
                        ctx.data["payload"] = _merged_payload
                        continue
                ctx.data[k] = v

        # Resume from failed: user sending a message = "coba lagi" — reset retry counters
        if ctx.data.get("_failed_at_state"):
            failed_state = ctx.data["_failed_at_state"]
            retry_key = f"_retry_{failed_state}"
            ctx.data.pop(retry_key, None)
            ctx.data.pop("_failed_at_state", None)
            ctx.data.pop("_last_error", None)
            logger.info(f"[RECON] Resuming from failed state={failed_state}, retry counters reset")

        # User sending a message at FINALIZE = explicit approval
        if ctx.current_state == "FINALIZE" and not ctx.data.get("user_approved_complete"):
            ctx.data["user_approved_complete"] = True

        return await self._advance(ctx)

    async def resume(self, chat_session_id: str, workflow_type: str, confirmed_data: dict = None) -> StepResult:
        """
        Resume workflow after DirectAction confirm.
        Called when frontend auto-sends "lanjut" after confirm.
        """
        ctx = await self._load(chat_session_id, workflow_type)
        if not ctx:
            return StepResult(advanced=False, llm_instruction="Tidak ada workflow aktif.")

        ctx.auth_token = self.auth_token
        ctx._execute_tool = self._execute_tool
        ctx._save_fn = self._save  # Allow auto-execute to persist mid-operation

        # Resume from failed: reset retry counters
        if ctx.data.get("_failed_at_state"):
            failed_state = ctx.data["_failed_at_state"]
            retry_key = f"_retry_{failed_state}"
            ctx.data.pop(retry_key, None)
            ctx.data.pop("_failed_at_state", None)
            ctx.data.pop("_last_error", None)
            logger.info(f"[RECON] Resume: retry counters reset for state={failed_state}")

        if confirmed_data:
            for k, v in confirmed_data.items():
                if v is not None:
                    ctx.data[k] = v

        # User responding at FINALIZE = explicit approval
        if ctx.current_state == "FINALIZE" and not ctx.data.get("user_approved_complete"):
            ctx.data["user_approved_complete"] = True

        # After a successful review confirm, increment reviewed_count
        if ctx.current_state == "REVIEWING":
            ctx.data["reviewed_count"] = ctx.data.get("reviewed_count", 0) + 1

        # Document review: after confirm DirectAction → transition through POSTING → POSTED
        if ctx.workflow_type == "document_review" and ctx.current_state == "AWAITING_DECISION":
            exec_result = confirmed_data or {}
            ctx.data["decision"] = exec_result.get("action_key", "confirmed")

            # Check if execution already happened (Phase 8 Option A: immediate)
            if exec_result.get("execution") or exec_result.get("status") == "posted":
                execution = exec_result.get("execution", {})
                ctx.current_state = "POSTED"
                ctx.data["journal_id"] = execution.get("journal_id")
                ctx.data["journal_number"] = execution.get("journal_number")
                llm_msg = f"Dokumen berhasil diposting! Journal #{execution.get('journal_number', '')}."
            elif exec_result.get("execution_error"):
                ctx.current_state = "POSTING_FAILED"
                ctx.data["posting_error"] = exec_result.get("execution_error")
                llm_msg = f"Posting gagal: {exec_result.get('execution_error')}"
            else:
                ctx.current_state = "COMPLETED"
                llm_msg = "Review dokumen selesai."

            completed = ctx.current_state in ("POSTED", "COMPLETED")
            if completed:
                ctx.status = "completed"
            await self._save(ctx)
            return StepResult(
                advanced=True, new_state=ctx.current_state, completed=completed,
                llm_instruction=llm_msg,
            )

        return await self._advance(ctx)

    async def get_state(self, chat_session_id: str, workflow_type: str) -> Optional[WorkflowContext]:
        """Get current workflow state (read-only)."""
        return await self._load(chat_session_id, workflow_type)

    async def cancel(self, chat_session_id: str, workflow_type: str) -> bool:
        """Cancel an active workflow. Returns True if a workflow was cancelled."""
        result = await self.db.execute(
            """UPDATE chat_workflow_state
               SET status = 'cancelled', updated_at = NOW()
               WHERE chat_session_id = $1 AND workflow_type = $2
                 AND status IN ('active', 'failed')""",
            chat_session_id, workflow_type
        )
        cancelled = result and result != "UPDATE 0"
        if cancelled:
            logger.info(f"[RECON] Cancelled workflow chat={chat_session_id} type={workflow_type}")
        return cancelled

    # ── Internal ──────────────────────────────────────────────────────

    MAX_AUTO_RETRIES = 2  # Circuit breaker: max retries per auto-execute state

    async def _run_auto_execute(self, ctx: WorkflowContext, auto_fn, state: str) -> Tuple[Dict[str, Any], bool]:
        """Run auto-execute function with retry tracking and circuit breaker.

        Returns (result_dict, is_error).
        On failure: increments retry counter, persists, logs structured warning.
        On circuit break (max retries): returns error result for LLM to communicate.
        """
        retry_key = f"_retry_{state}"
        current_retries = ctx.data.get(retry_key, 0)

        # Circuit breaker check
        if current_retries >= self.MAX_AUTO_RETRIES:
            last_error = ctx.data.get("_last_error", "Max retries exceeded")
            logger.warning(
                f"[RECON] Circuit breaker: state={state} "
                f"retries={current_retries}/{self.MAX_AUTO_RETRIES} "
                f"error={last_error[:200]}"
            )
            return {
                "error": True,
                "circuit_breaker": True,
                "message": (
                    f"Proses gagal setelah {current_retries} percobaan. "
                    f"Error terakhir: {last_error[:200]}. "
                    f"Mau coba lagi, atau batalkan rekonsiliasi?"
                ),
            }, True

        try:
            result = await auto_fn(ctx, self.call_internal)

            # Check if auto-execute returned an error dict
            if isinstance(result, dict) and result.get("error") and not result.get("session_id"):
                raise Exception(str(result.get("error", "Unknown auto-execute error")))

            # Success — clear retry tracking
            ctx.data.pop(retry_key, None)
            ctx.data.pop("_last_error", None)
            ctx.data.pop("_failed_at_state", None)
            return result, False

        except Exception as e:
            new_count = current_retries + 1
            ctx.data[retry_key] = new_count
            ctx.data["_last_error"] = str(e)[:500]
            ctx.data["_failed_at_state"] = state
            await self._save(ctx)

            fn_name = getattr(auto_fn, '__name__', 'unknown')
            logger.warning(
                f"[RECON] Auto-execute failed: state={state} fn={fn_name} "
                f"retry={new_count}/{self.MAX_AUTO_RETRIES} "
                f"error={str(e)[:200]}"
            )

            if new_count >= self.MAX_AUTO_RETRIES:
                return {
                    "error": True,
                    "circuit_breaker": True,
                    "message": (
                        f"Gagal: {str(e)[:200]}. "
                        f"Sudah dicoba {new_count} kali. "
                        f"Mau coba lagi, atau batalkan rekonsiliasi?"
                    ),
                }, True
            else:
                return {
                    "error": True,
                    "retry": True,
                    "message": (
                        f"Error: {str(e)[:200]}. "
                        f"Percobaan {new_count}/{self.MAX_AUTO_RETRIES}. "
                        f"Sampaikan ke user dan tawarkan coba lagi."
                    ),
                }, True

    async def _advance(self, ctx: WorkflowContext) -> StepResult:
        """Core state machine loop: check gate → auto-execute → advance → chain."""
        if ctx.current_state == "COMPLETED" or ctx.status != "active":
            return StepResult(
                advanced=False, new_state=ctx.current_state, completed=True,
                llm_instruction="Workflow sudah selesai."
            )

        # Generic workflow dispatch
        wf_config = WORKFLOW_TRANSITIONS.get(ctx.workflow_type, WORKFLOW_TRANSITIONS["bank_reconciliation"])
        transitions = wf_config["transitions"]

        transition = transitions.get(ctx.current_state)
        if not transition:
            return StepResult(
                advanced=False, new_state=ctx.current_state,
                llm_instruction=f"State tidak dikenali: {ctx.current_state}"
            )

        # Check gate
        ready, missing = await transition["check"](ctx, ctx.data)

        if not ready:
            # Check for not_ready_action (e.g., REVIEWING fetches next item when gate fails)
            not_ready_fn = transition.get("not_ready_action")
            if not_ready_fn:
                nr_result = await not_ready_fn(ctx, self.call_internal)

                # Re-check gate (action may have resolved it, e.g. no more items)
                ready2, _ = await transition["check"](ctx, ctx.data)
                if ready2:
                    _old = ctx.current_state
                    ctx.current_state = transition["next"]
                    self._prune_state_data(ctx, _old)
                    await self._save(ctx)
                    return await self._try_chain(ctx, nr_result, True)

                # Still not ready — return with action results for LLM
                await self._save(ctx)
                nr_instruction = (nr_result.get("instruction") if isinstance(nr_result, dict) else None) or transition["not_ready_instruction"]
                if isinstance(nr_instruction, str) and "{missing}" in nr_instruction:
                    nr_instruction = nr_instruction.replace("{missing}", str(missing))
                return StepResult(
                    advanced=False, new_state=ctx.current_state,
                    auto_executed=True, auto_results=nr_result,
                    llm_instruction=nr_instruction
                )

            await self._save(ctx)
            instruction = transition["not_ready_instruction"]
            if "{missing}" in instruction:
                instruction = instruction.replace("{missing}", str(missing))
            return StepResult(
                advanced=False, new_state=ctx.current_state,
                llm_instruction=instruction
            )

        # Gate passed — auto-execute if defined (with retry tracking)
        auto_results = None
        auto_executed = False
        if transition.get("auto_execute"):
            auto_results, is_error = await self._run_auto_execute(
                ctx, transition["auto_execute"], ctx.current_state
            )
            auto_executed = True
            if is_error:
                await self._save(ctx)
                return StepResult(
                    advanced=False, new_state=ctx.current_state,
                    auto_executed=True, auto_results=auto_results,
                    llm_instruction=auto_results.get("message", "Terjadi error. Sampaikan ke user.")
                )

        # Advance to next state
        old_state = ctx.current_state
        next_state = transition["next"]
        ctx.current_state = next_state
        self._prune_state_data(ctx, old_state)
        await self._save(ctx)
        logger.info(
            f"[RECON] transition={old_state}\u2192{next_state} "
            f"session={ctx.data.get('recon_session_id', 'n/a')} "
            f"chat={ctx.chat_session_id}"
        )

        # Check if completed
        if next_state == "COMPLETED":
            ctx.status = "completed"
            await self._save(ctx)
            return StepResult(
                advanced=True, new_state=next_state, auto_executed=auto_executed,
                auto_results=auto_results, completed=True,
                llm_instruction="Workflow selesai! Sampaikan hasil ke user."
            )

        # Try chaining: if new state has auto-execute and its gate passes
        return await self._try_chain(ctx, auto_results, auto_executed)

    async def _try_chain(self, ctx: WorkflowContext, prior_results: Optional[Dict], auto_executed: bool) -> StepResult:
        """Chain through auto-execute states until we hit a gate that fails or need user input."""
        max_chain = 10  # Safety: max auto-chain depth (need 7 for full recon: CHECK_EXISTING → ... → REVIEWING)
        chain_results: List[Dict[str, Any]] = []
        if prior_results:
            chain_results.append(prior_results)

        # Generic workflow dispatch
        wf_config = WORKFLOW_TRANSITIONS.get(ctx.workflow_type, WORKFLOW_TRANSITIONS["bank_reconciliation"])
        transitions = wf_config["transitions"]

        for _ in range(max_chain):
            transition = transitions.get(ctx.current_state)
            if not transition:
                break

            # Check gate of current state
            ready, missing = await transition["check"](ctx, ctx.data)
            if not ready:
                # Check for not_ready_action (e.g., REVIEWING fetches next item)
                not_ready_fn = transition.get("not_ready_action")
                if not_ready_fn:
                    nr_result = await not_ready_fn(ctx, self.call_internal)
                    chain_results.append(nr_result)
                    auto_executed = True

                    # Re-check gate — action may have resolved it
                    ready2, _ = await transition["check"](ctx, ctx.data)
                    if ready2:
                        _old = ctx.current_state
                        ctx.current_state = transition["next"]
                        self._prune_state_data(ctx, _old)
                        await self._save(ctx)
                        if ctx.current_state == "COMPLETED":
                            ctx.status = "completed"
                            await self._save(ctx)
                            return StepResult(
                                advanced=True, new_state="COMPLETED",
                                auto_executed=True, auto_results=self._merge_chain_results(chain_results),
                                completed=True, llm_instruction="Rekonsiliasi selesai! Sampaikan hasil ke user."
                            )
                        continue

                    # Still not ready — return with action results
                    await self._save(ctx)
                    nr_instruction = (nr_result.get("instruction") if isinstance(nr_result, dict) else None) or transition["not_ready_instruction"]
                    if isinstance(nr_instruction, str) and "{missing}" in nr_instruction:
                        nr_instruction = nr_instruction.replace("{missing}", str(missing))
                    return StepResult(
                        advanced=True, new_state=ctx.current_state,
                        auto_executed=True, auto_results=self._merge_chain_results(chain_results),
                        llm_instruction=nr_instruction
                    )

                await self._save(ctx)
                instruction = transition["not_ready_instruction"]
                if "{missing}" in instruction:
                    instruction = instruction.replace("{missing}", str(missing))
                return StepResult(
                    advanced=True, new_state=ctx.current_state,
                    auto_executed=auto_executed, auto_results=self._merge_chain_results(chain_results),
                    llm_instruction=instruction
                )

            # Auto-execute if defined (with retry tracking)
            if transition.get("auto_execute"):
                result, is_error = await self._run_auto_execute(
                    ctx, transition["auto_execute"], ctx.current_state
                )
                chain_results.append(result)
                auto_executed = True

                if is_error:
                    await self._save(ctx)
                    return StepResult(
                        advanced=True, new_state=ctx.current_state,
                        auto_executed=True, auto_results=self._merge_chain_results(chain_results),
                        llm_instruction=result.get("message", "Terjadi error. Sampaikan ke user.")
                    )

            # Advance
            old_state = ctx.current_state
            next_state = transition["next"]
            ctx.current_state = next_state
            self._prune_state_data(ctx, old_state)
            await self._save(ctx)
            logger.info(
                f"[RECON] transition={old_state}\u2192{next_state} "
                f"session={ctx.data.get('recon_session_id', 'n/a')} "
                f"chat={ctx.chat_session_id}"
            )

            if next_state == "COMPLETED":
                ctx.status = "completed"
                await self._save(ctx)
                return StepResult(
                    advanced=True, new_state=next_state, auto_executed=auto_executed,
                    auto_results=self._merge_chain_results(chain_results), completed=True,
                    llm_instruction="Workflow selesai! Sampaikan hasil ke user."
                )

            # Early-exit check: if next state has no handler that can make progress,
            # break early and return instruction to user.
            # States WITH auto_execute or not_ready_action → let next iteration handle them.
            next_transition = transitions.get(next_state)
            if next_transition:
                ready, missing = await next_transition["check"](ctx, ctx.data)
                if not ready and not next_transition.get("auto_execute") and not next_transition.get("not_ready_action"):
                    instruction = next_transition["not_ready_instruction"]
                    if "{missing}" in instruction:
                        instruction = instruction.replace("{missing}", str(missing))
                    return StepResult(
                        advanced=True, new_state=next_state,
                        auto_executed=auto_executed, auto_results=self._merge_chain_results(chain_results),
                        llm_instruction=instruction
                    )

        return StepResult(
            advanced=True, new_state=ctx.current_state,
            auto_executed=auto_executed, auto_results=self._merge_chain_results(chain_results),
            llm_instruction=self._build_state_instruction(ctx)
        )

    def _merge_chain_results(self, results: List[Dict[str, Any]]) -> Optional[Dict]:
        """Merge multiple auto-execute results into a single dict."""
        if not results:
            return None
        if len(results) == 1:
            return results[0]
        merged = {}
        for r in results:
            if isinstance(r, dict):
                merged.update(r)
        merged["_chain_count"] = len(results)
        return merged

    def _build_state_instruction(self, ctx: WorkflowContext) -> str:
        """Build a generic instruction for the current state."""
        return STATE_INSTRUCTIONS.get(ctx.current_state, f"State: {ctx.current_state}")

    async def call_internal(self, method: str, path: str, body: dict = None) -> dict:
        """Internal HTTP call to backend REST endpoints."""
        base = "http://localhost:8000"
        headers = {
            "Authorization": f"Bearer {self.auth_token}",
            "X-Tenant-ID": self.tenant_id,
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            if method == "GET":
                r = await client.get(f"{base}{path}", headers=headers)
            elif method == "POST":
                r = await client.post(f"{base}{path}", headers=headers, json=body or {})
            elif method == "PUT":
                r = await client.put(f"{base}{path}", headers=headers, json=body or {})
            elif method == "DELETE":
                r = await client.delete(f"{base}{path}", headers=headers)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            r.raise_for_status()
            return r.json()

    # ── Database Operations ───────────────────────────────────────────

    async def _load_or_create(self, chat_session_id: str, workflow_type: str) -> WorkflowContext:
        """Load existing active workflow, or create new if none active.

        CRITICAL: Must NOT reset data of active workflow on concurrent requests.
        Old pattern (ON CONFLICT DO UPDATE) was destructive — replaced with
        load-first + conditional INSERT/UPDATE.

        Race condition handling:
        - Two concurrent requests both try INSERT → only one succeeds via WHERE NOT EXISTS
        - Loser's INSERT returns no rows → falls through to _load
        - Both end up on same workflow → safe
        """
        import json
        initial_state = WORKFLOW_TRANSITIONS.get(workflow_type, {}).get("initial_state", "IDENTIFY_ACCOUNT")

        # Step 1: Try load existing active/failed workflow
        ctx = await self._load(chat_session_id, workflow_type)
        if ctx:
            return ctx

        # Step 2: No active workflow — try to create new (race-safe)
        try:
            row = await self.db.fetchrow(
                """INSERT INTO chat_workflow_state
                    (tenant_id, user_id, chat_session_id, workflow_type, current_state, status, data)
                SELECT $1, $2, $3, $4, $5, 'active', '{}'::jsonb
                WHERE NOT EXISTS (
                    SELECT 1 FROM chat_workflow_state
                    WHERE chat_session_id = $3
                      AND workflow_type = $4
                      AND status IN ('active', 'failed')
                )
                RETURNING id, current_state, status, data""",
                self.tenant_id, self.user_id, chat_session_id,
                workflow_type, initial_state
            )

            if row:
                return self._row_to_ctx(row, workflow_type, chat_session_id)
        except Exception as _uv_err:
            # UniqueViolation: cancelled/completed row occupies slot — fall through to Step 4
            logger.info(f"[Workflow] INSERT conflict ({_uv_err.__class__.__name__}), will reset existing row")

        # Step 3: Race — another request created it, or INSERT failed due to unique constraint
        ctx = await self._load(chat_session_id, workflow_type)
        if ctx:
            return ctx

        # Step 4: No active row but unique constraint exists (completed/cancelled row).
        # Safe to reset — only update if status is NOT active/failed.
        row = await self.db.fetchrow(
            """UPDATE chat_workflow_state
            SET current_state = $1, status = 'active', data = '{}'::jsonb, updated_at = NOW()
            WHERE chat_session_id = $2
              AND workflow_type = $3
              AND status NOT IN ('active', 'failed')
            RETURNING id, current_state, status, data""",
            initial_state, chat_session_id, workflow_type
        )

        if row:
            return self._row_to_ctx(row, workflow_type, chat_session_id)

        # Final fallback — load whatever exists
        return await self._load(chat_session_id, workflow_type)

    def _row_to_ctx(self, row, workflow_type: str, chat_session_id: str) -> WorkflowContext:
        """Convert a DB row to WorkflowContext."""
        import json
        return WorkflowContext(
            workflow_id=str(row["id"]),
            workflow_type=workflow_type,
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            chat_session_id=chat_session_id,
            current_state=row["current_state"],
            status=row["status"],
            data=json.loads(row["data"]) if isinstance(row["data"], str) else (row["data"] or {}),
        )

    async def _load(self, chat_session_id: str, workflow_type: str) -> Optional[WorkflowContext]:
        """Load active or failed workflow context from database."""
        import json
        row = await self.db.fetchrow(
            """SELECT id, tenant_id, user_id, current_state, status, data, updated_at
               FROM chat_workflow_state
               WHERE chat_session_id = $1 AND workflow_type = $2
                 AND status IN ('active', 'failed')""",
            chat_session_id, workflow_type
        )

        if not row:
            return None

        # FIX_WF_STALE_REUSE (2026-06-15): the UNIQUE (chat_session_id, workflow_type)
        # slot keeps exactly one crud_form per session forever, and the frontend reuses
        # one conversation across days. A long-idle active/COLLECTING row is an
        # ABANDONED workflow, not a live one — reusing its payload as a merge base
        # leaks stale vendor_id / due_date / unrelated party fields into a fresh
        # create request. Match the established 30-min idle TTL: if the row hasn't
        # advanced within that window, treat it as abandoned — cancel it (frees the
        # slot for a brand-new payload via _load_or_create) and return None so callers
        # see "no active workflow". A legitimate same-intent resume happens within
        # seconds and is never affected.
        try:
            from datetime import datetime, timezone
            _wf_updated_at = row["updated_at"]
            if _wf_updated_at is not None:
                if _wf_updated_at.tzinfo is None:
                    _wf_updated_at = _wf_updated_at.replace(tzinfo=timezone.utc)
                _wf_idle_secs = (datetime.now(timezone.utc) - _wf_updated_at).total_seconds()
                if _wf_idle_secs > WORKFLOW_IDLE_TTL_SECONDS:
                    await self.db.execute(
                        """UPDATE chat_workflow_state
                           SET status = 'cancelled', updated_at = NOW()
                           WHERE id = $1 AND status IN ('active', 'failed')""",
                        row["id"],
                    )
                    logger.warning(
                        "[FIX_WF_STALE_REUSE] Abandoned idle %s workflow (idle=%.0fs > %ds), "
                        "cancelled to seed fresh; session=%s state=%s",
                        workflow_type, _wf_idle_secs, WORKFLOW_IDLE_TTL_SECONDS,
                        chat_session_id, row["current_state"],
                    )
                    return None
        except Exception as _stale_err:
            logger.warning("[FIX_WF_STALE_REUSE] staleness check skipped: %s", _stale_err)

        return WorkflowContext(
            workflow_id=str(row["id"]),
            workflow_type=workflow_type,
            tenant_id=row["tenant_id"],
            user_id=str(row["user_id"]),
            chat_session_id=chat_session_id,
            current_state=row["current_state"],
            status=row["status"],
            data=json.loads(row["data"]) if isinstance(row["data"], str) else (row["data"] or {}),
        )

    @staticmethod
    def _prune_state_data(ctx: WorkflowContext, old_state: str) -> None:
        """Remove transient data keys after leaving a state to keep ctx.data lean."""
        keys = PRUNE_AFTER_STATE.get(old_state, [])
        for key in keys:
            removed = ctx.data.pop(key, None)
            if removed is not None:
                logger.info(f"[RECON] Pruned '{key}' from ctx.data after leaving {old_state}")

    async def _save(self, ctx: WorkflowContext) -> None:
        """Save workflow context to database."""
        import json
        await self.db.execute(
            """UPDATE chat_workflow_state
               SET current_state = $1, status = $2, data = $3, updated_at = NOW()
               WHERE id = $4""",
            ctx.current_state, ctx.status, json.dumps(ctx.data),
            __import__('uuid').UUID(ctx.workflow_id)
        )
