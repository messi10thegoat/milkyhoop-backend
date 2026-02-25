"""
Workflow Engine — Deterministic state machine for chat workflows.

Architecture: Agentic-Deterministic
  - LLM = Interpreter (understand intent, extract data, narrate)
  - Code = Controller (state transitions, deterministic flow)

The engine manages workflow state in PostgreSQL (chat_workflow_state table)
and uses internal HTTP calls to existing REST endpoints.
"""

import logging
from dataclasses import dataclass, field
from datetime import date as date_type
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Callable, Awaitable

import httpx

logger = logging.getLogger("unified_agent.workflow_engine")
logger.setLevel(logging.INFO)
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
    """Gate: do we have a file_ref OR user said no file?"""
    if ctx.data.get("file_ref") or ctx.data.get("no_file"):
        return (True, "")
    return (False, "file_ref or no_file")


async def check_review_complete(ctx: WorkflowContext, user_data: dict) -> Tuple[bool, str]:
    """Gate: have all review items been processed?"""
    if ctx.data.get("review_complete"):
        return (True, "")
    unmatched = ctx.data.get("unmatched_count", 0)
    reviewed = ctx.data.get("reviewed_count", 0)
    # 0 unmatched = nothing to review, advance to BALANCE_PROOF for actual balance check
    if unmatched == 0 or reviewed >= unmatched:
        ctx.data["review_complete"] = True
        return (True, "")
    remaining = max(0, unmatched - reviewed)
    return (False, f"{remaining} item belum di-review")


async def check_can_complete(ctx: WorkflowContext, user_data: dict) -> Tuple[bool, str]:
    """Gate: only finalize if balance proof says can_complete=True."""
    proof = ctx.data.get("balance_proof") or ctx.data.get("summary")
    if not proof:
        return (False, "Perlu balance proof terlebih dahulu")
    if proof.get("can_complete", False):
        return (True, "")
    blockers = proof.get("completion_blockers", [])
    msg = "; ".join(blockers) if blockers else "Rekonsiliasi belum bisa diselesaikan"
    return (False, msg)


# ─── Auto-Execute Functions ──────────────────────────────────────────────

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
    """Auto: create recon session + import file (if provided)."""
    account_id = ctx.data.get("account_id", "")
    balance = ctx.data.get("statement_ending_balance")
    file_ref = ctx.data.get("file_ref")
    no_file = ctx.data.get("no_file", False)

    today = date_type.today().isoformat()
    first_of_month = date_type.today().replace(day=1).isoformat()

    # Create session
    body = {
        "account_id": account_id,
        "statement_date": today,
        "statement_start_date": ctx.data.get("statement_start_date", first_of_month),
        "statement_end_date": ctx.data.get("statement_end_date", today),
        "statement_beginning_balance": ctx.data.get("statement_beginning_balance", 0),
        "statement_ending_balance": balance,
        "mode": "manual" if no_file else "import",
    }

    try:
        # If existing session, reuse it
        if ctx.data.get("recon_session_id"):
            session_id = ctx.data["recon_session_id"]
        else:
            result = await call_internal("POST", "/api/bank-reconciliation/sessions", body)
            session_id = result.get("id", result.get("session_id", ""))
            ctx.data["recon_session_id"] = session_id

        # Import file if provided — use execute_tool callback (handles file upload + column detection)
        if file_ref and not no_file:
            execute_tool = getattr(ctx, '_execute_tool', None)
            if execute_tool:
                import_result = await execute_tool("import_bank_statement", {
                    "session_id": session_id,
                    "file_ref": file_ref,
                })
                if not import_result.get("success", True):
                    return {"error": import_result.get("error", "Import failed"), "session_id": session_id}
                ctx.data["import_result"] = import_result
                return {
                    "session_id": session_id,
                    "import_result": import_result,
                    "message": "Session dibuat dan file berhasil diimport."
                }
            else:
                logger.warning("No execute_tool callback for import — skipping file import")

        return {
            "session_id": session_id,
            "message": f"Session rekonsiliasi dibuat (mode: {'manual' if no_file else 'import'})."
        }
    except Exception as e:
        logger.error(f"Auto create session/import failed: {e}")
        return {"error": str(e), "message": f"Gagal membuat session: {e}"}


async def auto_match(ctx: WorkflowContext, call_internal) -> Dict[str, Any]:
    """Auto: run agentic reconciliation matching."""
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
        return result
    except Exception as e:
        logger.error(f"Auto match failed: {e}")
        return {"error": str(e), "message": f"Gagal menjalankan auto-match: {e}"}


async def auto_get_summary(ctx: WorkflowContext, call_internal) -> Dict[str, Any]:
    """Auto: get reconciliation summary."""
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
        bank_account_id = ctx.data.get("bank_account_id", "")
        bank_account_name = ctx.data.get("account_name", "")
        statement_line = data.get("statement_line", {}) if isinstance(data, dict) else {}

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

        return {
            "review_item": data,
            "bill_suggestion": bill_suggestion,
            "invoice_suggestion": invoice_suggestion,
            "category_suggestion": category_suggestion,
            "instruction": instruction,
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


# ─── Transition Table ─────────────────────────────────────────────────────

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
        "not_ready_instruction": "Masih ada {missing} item untuk di-review.",
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
        "next": "COMPLETED",
        "not_ready_instruction": "Rekonsiliasi belum bisa diselesaikan: {missing}. Selesaikan dulu sebelum finalisasi.",
    },
}


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

        # Merge new data (don't overwrite existing with None)
        for k, v in user_data.items():
            if v is not None:
                ctx.data[k] = v

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

        if confirmed_data:
            for k, v in confirmed_data.items():
                if v is not None:
                    ctx.data[k] = v

        # After a successful review confirm, increment reviewed_count
        if ctx.current_state == "REVIEWING":
            ctx.data["reviewed_count"] = ctx.data.get("reviewed_count", 0) + 1

        return await self._advance(ctx)

    async def get_state(self, chat_session_id: str, workflow_type: str) -> Optional[WorkflowContext]:
        """Get current workflow state (read-only)."""
        return await self._load(chat_session_id, workflow_type)

    # ── Internal ──────────────────────────────────────────────────────

    async def _advance(self, ctx: WorkflowContext) -> StepResult:
        """Core state machine loop: check gate → auto-execute → advance → chain."""
        if ctx.current_state == "COMPLETED" or ctx.status != "active":
            return StepResult(
                advanced=False, new_state=ctx.current_state, completed=True,
                llm_instruction="Workflow sudah selesai."
            )

        transition = RECON_TRANSITIONS.get(ctx.current_state)
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
                    ctx.current_state = transition["next"]
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

        # Gate passed — auto-execute if defined
        auto_results = None
        auto_executed = False
        if transition.get("auto_execute"):
            auto_results = await transition["auto_execute"](ctx, self.call_internal)
            auto_executed = True

        # Advance to next state
        next_state = transition["next"]
        ctx.current_state = next_state
        await self._save(ctx)

        # Check if completed
        if next_state == "COMPLETED":
            ctx.status = "completed"
            await self._save(ctx)
            return StepResult(
                advanced=True, new_state=next_state, auto_executed=auto_executed,
                auto_results=auto_results, completed=True,
                llm_instruction="Rekonsiliasi selesai! Sampaikan hasil ke user."
            )

        # Try chaining: if new state has auto-execute and its gate passes
        return await self._try_chain(ctx, auto_results, auto_executed)

    async def _try_chain(self, ctx: WorkflowContext, prior_results: Optional[Dict], auto_executed: bool) -> StepResult:
        """Chain through auto-execute states until we hit a gate that fails or need user input."""
        max_chain = 5  # Safety: max auto-chain depth
        chain_results: List[Dict[str, Any]] = []
        if prior_results:
            chain_results.append(prior_results)

        for _ in range(max_chain):
            transition = RECON_TRANSITIONS.get(ctx.current_state)
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
                        ctx.current_state = transition["next"]
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

            # Auto-execute if defined
            if transition.get("auto_execute"):
                result = await transition["auto_execute"](ctx, self.call_internal)
                chain_results.append(result)
                auto_executed = True

                # Check for errors in auto-execute result
                if isinstance(result, dict) and result.get("error"):
                    await self._save(ctx)
                    return StepResult(
                        advanced=True, new_state=ctx.current_state,
                        auto_executed=True, auto_results=self._merge_chain_results(chain_results),
                        llm_instruction=f"Terjadi error: {result.get('error')}. Sampaikan ke user."
                    )

            # Advance
            next_state = transition["next"]
            ctx.current_state = next_state
            await self._save(ctx)

            if next_state == "COMPLETED":
                ctx.status = "completed"
                await self._save(ctx)
                return StepResult(
                    advanced=True, new_state=next_state, auto_executed=auto_executed,
                    auto_results=self._merge_chain_results(chain_results), completed=True,
                    llm_instruction="Rekonsiliasi selesai! Sampaikan hasil ke user."
                )

            # Check if next state's gate passes — if yes, continue loop to advance through it
            # If gate fails, stop and return instruction to user
            next_transition = RECON_TRANSITIONS.get(next_state)
            if next_transition:
                ready, missing = await next_transition["check"](ctx, ctx.data)
                if not ready and not next_transition.get("auto_execute"):
                    # Gate failed at a non-auto state — need user input
                    instruction = next_transition["not_ready_instruction"]
                    if "{missing}" in instruction:
                        instruction = instruction.replace("{missing}", str(missing))
                    return StepResult(
                        advanced=True, new_state=next_state,
                        auto_executed=auto_executed, auto_results=self._merge_chain_results(chain_results),
                        llm_instruction=instruction
                    )
                # Gate passed or has auto_execute — continue loop to process next state

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
        state_instructions = {
            "IDENTIFY_ACCOUNT": "Tanyakan nama/nomor rekening bank ke user.",
            "NEED_BALANCE": "Tanyakan saldo akhir rekening koran.",
            "NEED_FILE": "Tanya: upload file rekening koran atau lanjut tanpa file?",
            "REVIEWING": "Lanjutkan review item berikutnya.",
            "COMPLETED": "Rekonsiliasi selesai.",
        }
        return state_instructions.get(ctx.current_state, f"State: {ctx.current_state}")

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
        """Load existing workflow or create new one."""
        ctx = await self._load(chat_session_id, workflow_type)
        if ctx:
            return ctx

        # Create new
        import json
        row = await self.db.fetchrow(
            """INSERT INTO chat_workflow_state
               (tenant_id, user_id, chat_session_id, workflow_type, current_state, status, data)
               VALUES ($1, $2, $3, $4, $5, $6, $7)
               ON CONFLICT (chat_session_id, workflow_type) DO UPDATE
               SET updated_at = NOW()
               RETURNING id, current_state, status, data""",
            self.tenant_id, self.user_id, chat_session_id,
            workflow_type, "IDENTIFY_ACCOUNT", "active", json.dumps({})
        )

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
        """Load workflow context from database."""
        import json
        row = await self.db.fetchrow(
            """SELECT id, tenant_id, user_id, current_state, status, data
               FROM chat_workflow_state
               WHERE chat_session_id = $1 AND workflow_type = $2 AND status = 'active'""",
            chat_session_id, workflow_type
        )

        if not row:
            return None

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
