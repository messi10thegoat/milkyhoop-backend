"""
Router: Unified Agent Chat (v3 Architecture)

Single LLM agent loop with 37 tools (35 read + 2 action).
Replaces: action_chat.py pipeline (intent classifier → planner → enrichment).
Keeps: ActionValidator + ActionExecutor unchanged (called by tools).

Endpoints:
  POST /api/v3/chat/message    - Send text message (agent loop)
  POST /api/v3/chat/confirm    - Confirm pending action → execute
  POST /api/v3/chat/cancel     - Cancel pending action
  GET  /api/v3/chat/status/{id} - Poll action status
  GET  /api/v3/chat/history    - Get conversation history
"""

import hashlib
import io
import json
import logging
import os
import uuid as uuid_mod
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from pydantic import BaseModel, Field
from typing import Literal, Dict, Any, List

from ..services.unified_agent.session_orchestrator import SessionAwareAgent
from ..services.unified_agent.orchestrator import AgentResponse, TenantContext as OrchestratorTenantContext
from ..services.unified_agent.tool_executor import ToolExecutor, TenantContext
from ..services.action_validator_client import get_action_validator_client
from ..services.action_executor_client import get_action_executor_client
from ..services.unified_agent.session_manager import SessionManager, StateUpdateHooks
from ..services.unified_agent.db_utils import get_session_db_pool
from ..services.unified_agent.fsm import FSMState

# Chat history persistence via gRPC
import grpc
try:
    from backend.api_gateway.libs.milkyhoop_protos import (
        conversation_service_pb2,
        conversation_service_pb2_grpc,
    )
    CONVERSATION_SERVICE_AVAILABLE = False  # [PHASE A] Disabled - using session_manager instead
except ImportError:
    CONVERSATION_SERVICE_AVAILABLE = False

logger = logging.getLogger("unified_chat")
router = APIRouter()

# Singleton agent instance (stateless, safe to reuse)
_agent = SessionAwareAgent()


# ─── File Upload Constants & Helpers ──────────────────────────────────────────

UPLOAD_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
UPLOAD_MAX_FILES = 5
UPLOAD_ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".ofx", ".pdf"}
UPLOAD_BASE_DIR = "/tmp/milkyhoop_uploads"

# Import resolve_file_ref from utils (re-export for backward compatibility)
from ..utils.file_ref import resolve_file_ref  # noqa: F401

async def _validate_upload_files(files: List[UploadFile]) -> List[str]:
    """Validate uploaded files at boundary. Returns list of errors."""
    errors = []
    if len(files) > UPLOAD_MAX_FILES:
        errors.append(f"Maksimal {UPLOAD_MAX_FILES} file per pesan.")
        return errors

    for f in files:
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext not in UPLOAD_ALLOWED_EXTENSIONS:
            allowed = ", ".join(sorted(UPLOAD_ALLOWED_EXTENSIONS))
            errors.append(
                f"Tipe file \'{ext}\' tidak didukung untuk \'{f.filename}\'. "
                f"Didukung: {allowed}"
            )
            continue

        content = await f.read()
        await f.seek(0)
        if len(content) > UPLOAD_MAX_FILE_SIZE:
            size_mb = len(content) / (1024 * 1024)
            errors.append(
                f"File \'{f.filename}\' terlalu besar ({size_mb:.1f}MB). Maksimal 10MB."
            )

    return errors


async def _store_upload_file(
    file: UploadFile, tenant_id: str, pool
) -> dict:
    """
    Store uploaded file with SHA-256 dedup + session-level advisory lock.
    Returns file metadata dict.
    """
    content = await file.read()
    await file.seek(0)

    file_hash = hashlib.sha256(content).hexdigest()
    ext = os.path.splitext(file.filename or "")[1].lower()

    store_dir = os.path.join(UPLOAD_BASE_DIR, tenant_id, "chat")
    store_path = os.path.join(store_dir, f"{file_hash}{ext}")

    file_meta = {
        "file_hash": file_hash,
        "filename": file.filename,
        "size": len(content),
        "extension": ext,
        "content_type": file.content_type,
        "stored_path": store_path,
    }

    # Fast path: file already exists (same content uploaded before)
    if os.path.exists(store_path):
        logger.info(f"[FileUpload] Dedup hit: {file.filename} -> {file_hash[:12]}")
        return file_meta

    # Session-level advisory lock (released explicitly, not tied to transaction)
    lock_key = f"CHAT_FILE:{tenant_id}:{file_hash}"
    try:
        await pool.execute("SELECT pg_advisory_lock(hashtext($1))", lock_key)

        # Double-check after acquiring lock
        if not os.path.exists(store_path):
            os.makedirs(store_dir, exist_ok=True)
            with open(store_path, "wb") as fh:
                fh.write(content)
            logger.info(
                f"[FileUpload] Stored: {file.filename} -> {store_path} "
                f"({len(content)} bytes, hash={file_hash[:12]})"
            )
    finally:
        # Release lock immediately — BEFORE any parsing begins
        await pool.execute("SELECT pg_advisory_unlock(hashtext($1))", lock_key)

    return file_meta


def _build_file_context(file_metas: List[dict]) -> str:
    """Build file attachment context string for LLM."""
    if not file_metas:
        return ""

    attachments = []
    for fm in file_metas:
        size_kb = fm["size"] / 1024
        size_str = f"{size_kb/1024:.1f}MB" if size_kb >= 1024 else f"{size_kb:.0f}KB"
        ext_label = fm["extension"].upper().lstrip(".")
        # Build opaque file reference (hash only, no server path)
        file_ref = f"chat_upload:{fm.get('file_hash', '')}{fm['extension']}"
        attachments.append(f"[Attached: {fm['filename']}, {size_str}, {ext_label}, file_ref={file_ref}]")

    return "\n".join(attachments)




# ─── Request/Response Models ───────────────────────────────────────────────────

class ChatMessageRequest(BaseModel):
    """Request to send a message to the unified agent."""
    conversation_id: str = Field(..., description="Conversation session ID")
    session_id: Optional[str] = Field(None, description="Session ID for 4-layer memory")
    text: str = Field(..., min_length=1, max_length=2000, description="User message")


class ConfirmActionRequest(BaseModel):
    """Request to confirm a pending action."""
    conversation_id: str = Field(..., description="Conversation session ID")
    session_id: Optional[str] = Field(None, description="Session ID for 4-layer memory")
    pending_action_id: str = Field(..., description="ID of the pending action to confirm")
    doc_status: Literal["DRAFT", "POSTED"] = Field(
        "POSTED",
        description="Document status: DRAFT saves without posting, POSTED creates and posts",
    )


class CancelActionRequest(BaseModel):
    """Request to cancel a pending action."""
    conversation_id: str = Field(..., description="Conversation session ID")
    session_id: Optional[str] = Field(None, description="Session ID for 4-layer memory")
    pending_action_id: str = Field(..., description="ID of the pending action to cancel")


class ChatMessageResponse(BaseModel):
    """Unified response from agent chat endpoints."""
    message_id: str = Field(default_factory=lambda: str(uuid_mod.uuid4()))
    message_type: str = Field(..., description="TEXT | ACTION_PREVIEW | ACTION_RESULT | CLARIFICATION | VALIDATION_ERROR")
    text: Optional[str] = Field(None, description="Narrative text from agent")
    data: Optional[Dict[str, Any]] = Field(None, description="Typed data payload")
    trace_id: Optional[str] = Field(None, description="Trace ID for debugging")
    pending_action_id: Optional[str] = Field(None, description="Pending action ID if applicable")
    # Agent telemetry (optional, useful for debugging)
    iterations: Optional[int] = Field(None, description="Agent loop iterations")
    tool_calls: Optional[List[Dict]] = Field(None, description="Tools called during processing")
    model_used: Optional[str] = Field(None, description="LLM model used")
    latency_ms: Optional[int] = Field(None, description="Total processing time in ms")
    session_id: Optional[str] = Field(None, description="Session ID for conversation continuity")
    workflow_continuation: Optional[bool] = Field(None, description="Auto-continue workflow after confirm")


class ActionStatusResponse(BaseModel):
    """Response for polling action status."""
    pending_action_id: str
    status: str
    message: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


# ─── Auth Helper ───────────────────────────────────────────────────────────────

def _get_user_context(request: Request) -> dict:
    """
    Extract user context from request.state (set by AuthMiddleware).
    Returns dict with tenant_id, user_id, auth_token.
    """
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context: missing tenant_id")

    # Extract bearer token for downstream API calls
    auth_header = request.headers.get("authorization", "")
    auth_token = (
        auth_header.replace("Bearer ", "")
        if auth_header.startswith("Bearer ")
        else ""
    )

    return {
        "tenant_id": tenant_id,
        "user_id": user_id or "",
        "auth_token": auth_token,
        "tenant_name": user.get("tenant_name", tenant_id),
    }


# ─── Chat History Helpers ──────────────────────────────────────────────────────

async def _save_to_chat_history(
    user_id: str,
    tenant_id: str,
    message: str,
    response: str,
    intent: str = "unified_agent",
    metadata: dict = None,
) -> bool:
    """Persist message pair to conversation history via gRPC."""
    if not CONVERSATION_SERVICE_AVAILABLE:
        return False
    try:
        channel = grpc.aio.insecure_channel("conversation_service:5002")
        stub = conversation_service_pb2_grpc.ConversationServiceStub(channel)
        req = conversation_service_pb2.SaveMessageRequest(
            user_id=user_id,
            tenant_id=tenant_id,
            message=message,
            response=response,
            intent=intent,
            metadata_json=json.dumps(metadata or {}),
        )
        resp = await stub.SaveMessage(req)
        await channel.close()
        return resp.status == "success"
    except Exception as e:
        logger.warning(f"[ChatHistory] Save failed: {e}")
        return False


async def _get_conversation_history(
    user_id: str,
    tenant_id: str,
    conversation_id: str,
    limit: int = 10,
) -> list:
    """Retrieve recent conversation history as OpenAI message format."""
    if not CONVERSATION_SERVICE_AVAILABLE:
        return []
    try:
        channel = grpc.aio.insecure_channel("conversation_service:5002")
        stub = conversation_service_pb2_grpc.ConversationServiceStub(channel)
        req = conversation_service_pb2.GetChatHistoryRequest(
            user_id=user_id,
            tenant_id=tenant_id,
            limit=limit,
        )
        resp = await stub.GetChatHistory(req)
        await channel.close()

        # Convert to OpenAI message format
        messages = []
        for msg in resp.messages:
            messages.append({"role": "user", "content": msg.message})
            if msg.response:
                messages.append({"role": "assistant", "content": msg.response})
        return messages
    except Exception as e:
        logger.warning(f"[ChatHistory] Fetch failed: {e}")
        return []


# ─── Helper: AgentResponse → ChatMessageResponse ──────────────────────────────

def _to_chat_response(agent_resp) -> ChatMessageResponse:
    """Convert internal AgentResponse or dict to API response model."""
    # Handle both dict (from SessionAwareAgent) and AgentResponse object
    if isinstance(agent_resp, dict):
        message_type = agent_resp.get("message_type")
        content_text = agent_resp.get("content")
        pending_action_id_val = agent_resp.get("pending_action_id")
        preview = agent_resp.get("preview")
        expires_at = agent_resp.get("expires_at")
        errors = agent_resp.get("errors")
        trace_id = agent_resp.get("trace_id")
        iterations = agent_resp.get("iterations")
        tool_calls = agent_resp.get("tool_calls_made")
        model_used = agent_resp.get("model_used")
        latency_ms = agent_resp.get("total_latency_ms")
        session_id = agent_resp.get("session_id")
    else:
        message_type = agent_resp.message_type
        content_text = agent_resp.content
        pending_action_id_val = agent_resp.pending_action_id
        preview = agent_resp.preview
        expires_at = agent_resp.expires_at
        errors = agent_resp.errors
        trace_id = agent_resp.trace_id
        iterations = agent_resp.iterations
        tool_calls = agent_resp.tool_calls_made
        model_used = agent_resp.model_used
        latency_ms = agent_resp.total_latency_ms
        session_id = getattr(agent_resp, "session_id", None)
    
    data = None
    pending_action_id = None

    if message_type == "ACTION_PREVIEW":
        data = {
            "pending_action_id": pending_action_id_val,
            "preview": preview,
            "expires_at": expires_at,
        }
        pending_action_id = pending_action_id_val
    elif message_type == "DIRECT_ACTION_PREVIEW":
        # DirectAction: preview dict already contains all needed data
        data = preview or {}
        pending_action_id = pending_action_id_val or data.get("pending_action_id")
    elif message_type == "VALIDATION_ERROR" and errors:
        data = {"errors": errors}

    return ChatMessageResponse(
        message_type=message_type,
        text=content_text or None,
        data=data,
        trace_id=trace_id,
        pending_action_id=pending_action_id,
        iterations=iterations,
        tool_calls=tool_calls or None,
        model_used=model_used or None,
        latency_ms=latency_ms,
        session_id=session_id,
    )




# =============================================================================
# POST /message — Main entry point: unified agent loop
# =============================================================================

@router.post("/message", response_model=ChatMessageResponse)
async def send_message(request: Request, body: ChatMessageRequest):
    """
    Process a user message through the unified agent loop.

    The agent autonomously:
    1. Reads data via API tools (search, list, detail)
    2. Resolves entities (customer/vendor/item IDs)
    3. Proposes actions via propose_action tool
    4. Returns text answers or ACTION_PREVIEW for confirmation
    """
    ctx = _get_user_context(request)

    # FSM Guard: If awaiting confirmation, warn user
    if body.session_id:
        try:
            db_pool_guard = await get_session_db_pool()
            sm_guard = SessionManager(db_pool=db_pool_guard, tenant_id=ctx["tenant_id"], user_id=ctx["user_id"])
            guard_state = await sm_guard.get_state(body.session_id)
            if guard_state.fsm_state == "AWAITING_CONFIRMATION":
                return ChatMessageResponse(
                    message_type="TEXT",
                    text="Ada aksi yang menunggu konfirmasi. Silakan konfirmasi atau batalkan dulu sebelum mengirim pesan baru.",
                    session_id=body.session_id,
                )
        except Exception:
            pass  # Non-fatal, proceed normally

    # Build tenant context for tool executor
    tenant_context = TenantContext(
        tenant_id=ctx["tenant_id"],
        user_id=ctx["user_id"],
        auth_token=ctx["auth_token"],
        tenant_name=ctx.get("tenant_name", ctx["tenant_id"]),
    )

    # Fetch conversation history for context
    history = await _get_conversation_history(
        user_id=ctx["user_id"],
        tenant_id=ctx["tenant_id"],
        conversation_id=body.conversation_id,
        limit=10,
    )

    # Run the agent loop
    agent_resp = await _agent.process_message(
        user_text=body.text,
        context=tenant_context,
        conversation_history=history,
        session_id=body.session_id,
    )

    # [PHASE A] Message persistence handled by session_orchestrator.py
    # Old gRPC conversation_service disabled - causes user_id schema conflict
    # await _save_to_chat_history(
        # user_id=ctx["user_id"],
        # tenant_id=ctx["tenant_id"],
        # message=body.text,
        # response=(agent_resp.get('content') if isinstance(agent_resp, dict) else agent_resp.content) or "",
        # intent="unified_agent",
        # metadata={
            # "conversation_id": body.conversation_id,
            # "message_type": agent_resp.get("message_type") if isinstance(agent_resp, dict) else agent_resp.message_type,
            # "model_used": agent_resp.get("model_used") if isinstance(agent_resp, dict) else agent_resp.model_used,
            # "iterations": agent_resp.get("iterations") if isinstance(agent_resp, dict) else agent_resp.iterations,
            # "pending_action_id": (agent_resp.get("pending_action_id") if isinstance(agent_resp, dict) else agent_resp.pending_action_id) or None,
        # },
    # )

    # Handle both dict and object responses
    msg_type = agent_resp.get("message_type") if isinstance(agent_resp, dict) else agent_resp.message_type
    iterations = agent_resp.get("iterations", 0) if isinstance(agent_resp, dict) else agent_resp.iterations
    tool_calls = agent_resp.get("tool_calls_made", []) if isinstance(agent_resp, dict) else (agent_resp.tool_calls_made or [])
    model_used = agent_resp.get("model_used") if isinstance(agent_resp, dict) else agent_resp.model_used
    latency = agent_resp.get("total_latency_ms", 0) if isinstance(agent_resp, dict) else agent_resp.total_latency_ms
    
    logger.info(
        f"[UnifiedAgent] user={ctx['user_id']} type={msg_type} "
        f"iterations={iterations} tools={len(tool_calls)} "
        f"model={model_used} latency={latency}ms"
    )

    return _to_chat_response(agent_resp)


# =============================================================================
# POST /message/upload — Send message with file attachments (multipart)
# =============================================================================

@router.post("/message/upload", response_model=ChatMessageResponse)
async def send_message_with_files(
    request: Request,
    conversation_id: str = Form(..., description="Conversation session ID"),
    text: str = Form(..., min_length=1, max_length=2000, description="User message"),
    session_id: Optional[str] = Form(None, description="Session ID for 4-layer memory"),
    files: List[UploadFile] = File(default=[], description="Attached files (max 5, max 10MB each)"),
):
    """
    Process a user message with file attachments through the unified agent loop.

    Accepts multipart/form-data with text + files.
    Files are validated at boundary, stored with SHA-256 dedup,
    and their metadata is injected into LLM context.
    """
    ctx = _get_user_context(request)

    # ── File Validation (at boundary, reject early) ──
    actual_files = [f for f in files if f.filename]  # Filter empty file fields
    if actual_files:
        validation_errors = await _validate_upload_files(actual_files)
        if validation_errors:
            return ChatMessageResponse(
                message_type="VALIDATION_ERROR",
                text="\n".join(validation_errors),
                data={"errors": validation_errors},
            )

    # ── File Storage (SHA-256 dedup + advisory lock) ──
    file_metas = []
    if actual_files:
        try:
            pool = await get_session_db_pool()
            for f in actual_files:
                meta = await _store_upload_file(f, ctx["tenant_id"], pool)
                file_metas.append(meta)
        except Exception as e:
            logger.error(f"[FileUpload] Storage failed: {e}")
            return ChatMessageResponse(
                message_type="VALIDATION_ERROR",
                text=f"Gagal menyimpan file: {str(e)}",
                data={"errors": [str(e)]},
            )

    # ── Enrich user text with file context for LLM ──
    enriched_text = text
    if file_metas:
        file_context = _build_file_context(file_metas)
        enriched_text = f"{text}\n\n{file_context}"

    # ── FSM Guard (same as /message) ──
    if session_id:
        try:
            db_pool_guard = await get_session_db_pool()
            sm_guard = SessionManager(
                db_pool=db_pool_guard,
                tenant_id=ctx["tenant_id"],
                user_id=ctx["user_id"],
            )
            guard_state = await sm_guard.get_state(session_id)
            if guard_state.fsm_state == "AWAITING_CONFIRMATION":
                return ChatMessageResponse(
                    message_type="TEXT",
                    text="Ada aksi yang menunggu konfirmasi. Silakan konfirmasi atau batalkan dulu sebelum mengirim pesan baru.",
                    session_id=session_id,
                )
        except Exception:
            pass

    # ── Build context & run agent (same as /message) ──
    tenant_context = TenantContext(
        tenant_id=ctx["tenant_id"],
        user_id=ctx["user_id"],
        auth_token=ctx["auth_token"],
        tenant_name=ctx.get("tenant_name", ctx["tenant_id"]),
    )

    history = await _get_conversation_history(
        user_id=ctx["user_id"],
        tenant_id=ctx["tenant_id"],
        conversation_id=conversation_id,
        limit=10,
    )

    # Pass enriched text (with file metadata) to agent
    agent_resp = await _agent.process_message(
        user_text=enriched_text,
        context=tenant_context,
        conversation_history=history,
        session_id=session_id,
    )

    # ── Logging ──
    msg_type = agent_resp.get("message_type") if isinstance(agent_resp, dict) else agent_resp.message_type
    iterations = agent_resp.get("iterations", 0) if isinstance(agent_resp, dict) else agent_resp.iterations
    tool_calls = agent_resp.get("tool_calls_made", []) if isinstance(agent_resp, dict) else (agent_resp.tool_calls_made or [])
    model_used = agent_resp.get("model_used") if isinstance(agent_resp, dict) else agent_resp.model_used
    latency = agent_resp.get("total_latency_ms", 0) if isinstance(agent_resp, dict) else agent_resp.total_latency_ms

    logger.info(
        f"[UnifiedAgent+Files] user={ctx['user_id']} type={msg_type} "
        f"files={len(file_metas)} iterations={iterations} tools={len(tool_calls)} "
        f"model={model_used} latency={latency}ms"
    )

    # ── Build response with file references ──
    response = _to_chat_response(agent_resp)

    # Attach file metadata to response data (for downstream tools)
    if file_metas and response.data is None:
        response.data = {}
    if file_metas:
        response.data = response.data or {}
        response.data["uploaded_files"] = [
            {
                "filename": fm["filename"],
                "size": fm["size"],
                "extension": fm["extension"],
                "file_hash": fm["file_hash"],
                "stored_path": fm["stored_path"],
            }
            for fm in file_metas
        ]

    return response


async def _confirm_direct_action(
    pending_action_id: str, tenant_id: str, user_id: str,
    pool, http_request
) -> ChatMessageResponse:
    """Execute a direct action by calling the REST endpoint."""
    import httpx
    from ..services.unified_agent.direct_action_registry import get_direct_action

    # Fetch pending action
    row = await pool.fetchrow(
        """SELECT action_id, action_plan, status, expires_at
           FROM pending_actions WHERE id = $1 AND tenant_id = $2""",
        uuid_mod.UUID(pending_action_id), tenant_id
    )

    if not row:
        return ChatMessageResponse(
            message_type="ACTION_RESULT",
            text="Action tidak ditemukan.",
            data={"success": False}
        )

    if row["status"] != "PENDING":
        return ChatMessageResponse(
            message_type="ACTION_RESULT",
            text=f"Action sudah {row['status'].lower()}.",
            data={"success": False}
        )

    from datetime import datetime, timezone
    if row["expires_at"] and row["expires_at"].replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        await pool.execute(
            "UPDATE pending_actions SET status = 'EXPIRED' WHERE id = $1",
            uuid_mod.UUID(pending_action_id)
        )
        return ChatMessageResponse(
            message_type="ACTION_RESULT",
            text="Action sudah kedaluwarsa. Silakan buat ulang.",
            data={"success": False}
        )

    action_key = row["action_id"]
    action_plan_raw = row["action_plan"]
    payload = json.loads(action_plan_raw) if isinstance(action_plan_raw, str) else action_plan_raw
    config = get_direct_action(action_key)

    if not config:
        return ChatMessageResponse(
            message_type="ACTION_RESULT",
            text=f"Konfigurasi untuk '{action_key}' tidak ditemukan.",
            data={"success": False}
        )

    # Mark as executing
    await pool.execute(
        "UPDATE pending_actions SET status = 'EXECUTING', confirmed_at = NOW() WHERE id = $1",
        uuid_mod.UUID(pending_action_id)
    )

    # Forward tenant JWT for auth
    auth_header = http_request.headers.get("authorization", "")
    base_url = "http://localhost:8000"  # internal API

    try:
        # --- Bug Fix 1: Resolve path parameters (e.g. {id}) from payload ---
        endpoint = config.rest_endpoint
        if '{' in endpoint:
            try:
                endpoint = endpoint.format(**payload)
            except KeyError:
                if '{id}' in endpoint:
                    entity_id = payload.get('id') or payload.get('account_id') or payload.get(f'{config.entity_type}_id', '')
                    endpoint = endpoint.replace('{id}', str(entity_id))

        # Strip display_only fields (context for user, not needed by REST endpoint)
        display_only_names = {f.name for f in config.fields if f.display_only}
        clean_payload = {k: v for k, v in payload.items() if k not in display_only_names}

        # Bill payment: transform flat payload → allocations[] format for POST /api/bill-payments
        if action_key == "create_bill_payment":
            clean_payload = {
                "vendor_id": payload.get("vendor_id"),
                "payment_date": payload.get("payment_date"),
                "payment_method": payload.get("payment_method", "bank_transfer"),
                "bank_account_id": payload.get("bank_account_id"),
                "total_amount": int(payload.get("total_amount", 0)),
                "allocations": [
                    {
                        "bill_id": payload.get("bill_id"),
                        "amount_applied": int(payload.get("total_amount", 0)),
                    }
                ],
                "reference_number": payload.get("bill_number", ""),
                "notes": f"Pembayaran dari rekonsiliasi bank — {payload.get('bill_number', '')}",
            }

        elif action_key == "create_receive_payment":
            allocations = payload.get("allocations", [])
            if isinstance(allocations, str):
                import json as json_lib
                allocations = json_lib.loads(allocations)
            clean_payload = {
                "customer_id": payload["customer_id"],
                "payment_date": payload["payment_date"],
                "payment_method": payload.get("payment_method", "bank_transfer"),
                "bank_account_id": payload["bank_account_id"],
                "total_amount": int(payload.get("total_amount", 0)),
                "allocations": allocations,
                "reference_number": payload.get("invoice_numbers", payload.get("reference_number", "")),
                "notes": "Pembayaran dari rekonsiliasi bank",
            }
            if payload.get("session_id"):
                clean_payload["session_id"] = payload["session_id"]
            if payload.get("statement_line_id"):
                clean_payload["statement_line_id"] = payload["statement_line_id"]

        # For DELETE/path-param requests, strip ID fields from body to avoid endpoint rejections
        request_body = clean_payload
        if config.rest_method.upper() == 'DELETE':
            id_keys = {'id', 'account_id', f'{config.entity_type}_id'}
            request_body = {k: v for k, v in clean_payload.items() if k not in id_keys}
            if not request_body:
                request_body = None  # Send no body for empty DELETE

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.request(
                method=config.rest_method,
                url=f"{base_url}{endpoint}",
                json=request_body,
                headers={
                    "Authorization": auth_header,
                    "Content-Type": "application/json",
                    "X-Tenant-ID": tenant_id,
                },
            )

        if response.status_code in (200, 201):
            result_data = response.json()
            entity_id = result_data.get("id", result_data.get("data", {}).get("id", ""))

            await pool.execute(
                """UPDATE pending_actions
                   SET status = 'COMPLETED', executed_at = NOW(),
                       result = $1
                   WHERE id = $2""",
                json.dumps(result_data), uuid_mod.UUID(pending_action_id)
            )

            success_msg = config.get_success_message(payload)

            # Recon actions: signal frontend to auto-continue workflow
            recon_actions = {"confirm_single_match", "categorize_statement", "create_bill_payment", "create_receive_payment"}
            is_recon = action_key in recon_actions

            return ChatMessageResponse(
                message_type="ACTION_RESULT",
                text=success_msg,
                data={
                    "success": True,
                    "action_type": action_key.upper(),
                    "entity_id": str(entity_id),
                    "entity_type": config.entity_type,
                },
                workflow_continuation=True if is_recon else None,
            )
        else:
            error_detail = response.text
            try:
                error_json = response.json()
                error_detail = error_json.get("detail", error_json.get("message", response.text))
            except Exception:
                pass

            await pool.execute(
                """UPDATE pending_actions
                   SET status = 'FAILED', executed_at = NOW(),
                       error_message = $1
                   WHERE id = $2""",
                str(error_detail), uuid_mod.UUID(pending_action_id)
            )

            return ChatMessageResponse(
                message_type="ACTION_RESULT",
                text=f"Gagal: {error_detail}",
                data={"success": False}
            )

    except Exception as e:
        await pool.execute(
            """UPDATE pending_actions
               SET status = 'FAILED', executed_at = NOW(),
                   error_message = $1
               WHERE id = $2""",
            str(e), uuid_mod.UUID(pending_action_id)
        )
        return ChatMessageResponse(
            message_type="ACTION_RESULT",
            text=f"Error: {str(e)}",
            data={"success": False}
        )


# =============================================================================
# POST /confirm — Confirm pending action → execute via ActionExecutor
# =============================================================================

@router.post("/confirm", response_model=ChatMessageResponse)
async def confirm_action(request: Request, body: ConfirmActionRequest):
    """
    Confirm and execute a pending action.
    Delegates to ActionExecutor (gRPC) — same as action_chat.py.
    """
    ctx = _get_user_context(request)

    try:
        # Check if this is a direct action
        try:
            da_pool = await get_session_db_pool()
            is_direct = await da_pool.fetchval(
                "SELECT is_direct FROM pending_actions WHERE id = $1 AND tenant_id = $2",
                uuid_mod.UUID(body.pending_action_id), ctx["tenant_id"]
            )
        except Exception as da_err:
            logger.warning(f"[Confirm] Direct action check failed (non-fatal): {da_err}")
            is_direct = False

        if is_direct:
            # Direct action — execute via REST
            da_pool2 = await get_session_db_pool()
            return await _confirm_direct_action(
                body.pending_action_id, ctx["tenant_id"], ctx["user_id"],
                da_pool2, request
            )

        # FSM: AWAITING_CONFIRMATION -> EXECUTING
        try:
            if body.session_id:
                db_pool = await get_session_db_pool()
                sm_fsm = SessionManager(db_pool=db_pool, tenant_id=ctx["tenant_id"], user_id=ctx["user_id"])
                await sm_fsm.transition_fsm(body.session_id, FSMState.EXECUTING.value)
        except Exception as fsm_err:
            logger.warning(f"[Confirm] FSM transition failed (non-fatal): {fsm_err}")

        executor = get_action_executor_client()
        result = await executor.execute_action(
            pending_action_id=body.pending_action_id,
            doc_status=body.doc_status,
            tenant_id=ctx["tenant_id"],
            user_id=ctx["user_id"],
        )

        success = result.get("success", False)

        if success:
            # === Update Layer 2 session state via hooks ===
            try:
                if body.session_id:
                    db_pool = await get_session_db_pool()
                    sm = SessionManager(db_pool=db_pool, tenant_id=ctx["tenant_id"], user_id=ctx["user_id"])
                    # Get action_type from current session state (set during propose)
                    state = await sm.get_state(body.session_id)
                    action_type = state.last_action_type or "UNKNOWN"
                    await StateUpdateHooks.after_confirm(
                        sm, body.session_id, action_type, result
                    )
                    logger.info(
                        f"[Confirm] Layer 2 updated: session={body.session_id[:8]} "
                        f"action={action_type} status=confirmed"
                    )
            except Exception as hook_err:
                logger.warning(f"[Confirm] Layer 2 hook failed (non-fatal): {hook_err}")

            text = (
                "Dokumen berhasil disimpan sebagai draft."
                if body.doc_status == "DRAFT"
                else "Transaksi berhasil diposting."
            )
            return ChatMessageResponse(
                message_type="ACTION_RESULT",
                text=text,
                data=result.get("data"),
                pending_action_id=body.pending_action_id,
                trace_id=str(uuid_mod.uuid4()),
            )
        else:
            # FSM: EXECUTING -> FAILED -> IDLE
            try:
                if body.session_id:
                    db_pool_fail = await get_session_db_pool()
                    sm_fail = SessionManager(db_pool=db_pool_fail, tenant_id=ctx["tenant_id"], user_id=ctx["user_id"])
                    await sm_fail.transition_fsm(body.session_id, FSMState.FAILED.value)
                    await sm_fail.transition_fsm(body.session_id, FSMState.IDLE.value)
            except Exception as fsm_err:
                logger.warning(f"[Confirm] FSM fail transition (non-fatal): {fsm_err}")

            return ChatMessageResponse(
                message_type="VALIDATION_ERROR",
                text=result.get("error", "Eksekusi gagal. Silakan coba lagi."),
                data={"errors": result.get("errors", [])},
                pending_action_id=body.pending_action_id,
                trace_id=str(uuid_mod.uuid4()),
            )

    except Exception as e:
        logger.exception(f"[Confirm] Failed for {body.pending_action_id}")
        raise HTTPException(status_code=500, detail=f"Execution failed: {str(e)}")


# =============================================================================
# POST /cancel — Cancel pending action
# =============================================================================

@router.post("/cancel", response_model=ChatMessageResponse)
async def cancel_action(request: Request, body: CancelActionRequest):
    """Cancel a pending action. Clears the pending state."""
    ctx = _get_user_context(request)

    try:
        executor = get_action_executor_client()
        result = await executor.cancel_action(
            pending_action_id=body.pending_action_id,
            tenant_id=ctx["tenant_id"],
            user_id=ctx["user_id"],
        )

        # FSM: AWAITING_CONFIRMATION -> IDLE
        try:
            if body.session_id:
                db_pool_fsm = await get_session_db_pool()
                sm_fsm = SessionManager(db_pool=db_pool_fsm, tenant_id=ctx["tenant_id"], user_id=ctx["user_id"])
                await sm_fsm.transition_fsm(body.session_id, FSMState.IDLE.value)
        except Exception as fsm_err:
            logger.warning(f"[Cancel] FSM transition failed (non-fatal): {fsm_err}")

        # === Update Layer 2 session state via hooks ===
        try:
            if body.session_id:
                db_pool = await get_session_db_pool()
                sm = SessionManager(db_pool=db_pool, tenant_id=ctx["tenant_id"], user_id=ctx["user_id"])
                # Get action_type from current session state (set during propose)
                state = await sm.get_state(body.session_id)
                action_type = state.last_action_type or "UNKNOWN"
                await StateUpdateHooks.after_reject(
                    sm, body.session_id, action_type
                )
                logger.info(
                    f"[Cancel] Layer 2 updated: session={body.session_id[:8]} "
                    f"action={action_type} status=rejected"
                )
        except Exception as hook_err:
            logger.warning(f"[Cancel] Layer 2 hook failed (non-fatal): {hook_err}")

        return ChatMessageResponse(
            message_type="TEXT",
            text="Aksi dibatalkan.",
            pending_action_id=body.pending_action_id,
            trace_id=str(uuid_mod.uuid4()),
        )

    except Exception as e:
        logger.exception(f"[Cancel] Failed for {body.pending_action_id}")
        raise HTTPException(status_code=500, detail=f"Cancel failed: {str(e)}")


# =============================================================================
# GET /status/{pending_action_id} — Poll action status
# =============================================================================

@router.get("/status/{pending_action_id}", response_model=ActionStatusResponse)
async def get_action_status(request: Request, pending_action_id: str):
    """Poll the status of a pending action."""
    ctx = _get_user_context(request)

    try:
        executor = get_action_executor_client()
        result = await executor.get_action_status(
            action_id=pending_action_id,
            tenant_id=ctx["tenant_id"],
        )

        return ActionStatusResponse(
            pending_action_id=pending_action_id,
            status=result.get("status", "UNKNOWN"),
            message=result.get("message"),
            data=result.get("data"),
        )

    except Exception as e:
        logger.exception(f"[Status] Failed for {pending_action_id}")
        raise HTTPException(status_code=500, detail=f"Status check failed: {str(e)}")


# =============================================================================
# GET /history — Get conversation history
# =============================================================================

# DEPRECATED: Replaced by chat_history.py
# @router.get("/history")
async def _get_history_old(
    request: Request,
    conversation_id: str = "",
    limit: int = 20,
):
    """Retrieve conversation history for the current user."""
    ctx = _get_user_context(request)

    if not CONVERSATION_SERVICE_AVAILABLE:
        return {"messages": [], "warning": "Conversation service unavailable"}

    try:
        channel = grpc.aio.insecure_channel("conversation_service:5002")
        stub = conversation_service_pb2_grpc.ConversationServiceStub(channel)
        req = conversation_service_pb2.GetChatHistoryRequest(
            user_id=ctx["user_id"],
            tenant_id=ctx["tenant_id"],
            limit=limit,
        )
        resp = await stub.GetChatHistory(req)
        await channel.close()

        messages = []
        for msg in resp.messages:
            messages.append({
                "id": msg.message_id,
                "role": "user",
                "content": msg.message,
                "timestamp": msg.timestamp,
            })
            if msg.response:
                messages.append({
                    "id": f"{msg.message_id}_resp",
                    "role": "assistant",
                    "content": msg.response,
                    "timestamp": msg.timestamp,
                })

        return {"messages": messages, "count": len(messages)}

    except Exception as e:
        logger.warning(f"[History] Fetch failed: {e}")
        return {"messages": [], "error": str(e)}
