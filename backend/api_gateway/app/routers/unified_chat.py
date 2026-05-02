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

import base64
import hashlib
import asyncpg
import json
import logging
import os
from io import BytesIO
import uuid as uuid_mod
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from typing import Literal, Dict, Any, List
import asyncio as _asyncio_stream
import json as _json_stream

from ..services.unified_agent.session_orchestrator import SessionAwareAgent
from ..services.unified_agent.telemetry import record_telemetry
from ..services.unified_agent.tool_executor import ToolExecutor, TenantContext
from ..services.unified_agent.orchestrator import _strip_draft_void_rows
from ..services.action_executor_client import get_action_executor_client
from ..services.action_service import (
    ActionService,
    CONFIRM_KEYWORDS,
    CANCEL_KEYWORDS,
    detect_edit_intent,
)
from ..services.unified_agent.session_manager import SessionManager, StateUpdateHooks
from ..services.unified_agent.db_utils import get_session_db_pool
from ..services.unified_agent.fsm import FSMState
from ..services.unified_agent.tutorial_progress import (
    get_active_tutorial,
    advance_tutorial as _advance_tutorial_step,
)
from ..services.unified_agent.tutorial_registry import (
    get_tutorial_step as _get_tutorial_step,
)

# Chat history persistence via gRPC
import grpc

try:
    from backend.api_gateway.libs.milkyhoop_protos import (
        conversation_service_pb2,
        conversation_service_pb2_grpc,
    )

    CONVERSATION_SERVICE_AVAILABLE = (
        False  # [PHASE A] Disabled - using session_manager instead
    )
except ImportError:
    CONVERSATION_SERVICE_AVAILABLE = False

logger = logging.getLogger("unified_chat")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _uc_handler = logging.StreamHandler()
    _uc_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_uc_handler)


class _VisionGateSkip(Exception):
    """Raised by Vision Gate to skip document pipeline for non-financial images."""

    pass


router = APIRouter()

# Singleton agent instance (stateless, safe to reuse)
_agent = SessionAwareAgent()


# ─── File Upload Constants & Helpers ──────────────────────────────────────────

UPLOAD_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
UPLOAD_MAX_FILES = 5
UPLOAD_ALLOWED_EXTENSIONS = {
    ".csv",
    ".xlsx",
    ".xls",
    ".ofx",
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".heic",
    ".heif",
}
UPLOAD_BASE_DIR = "/tmp/milkyhoop_uploads"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
VISION_MAX_DIMENSION = 1024  # Max px on longest side for vision API

# Import resolve_file_ref from utils (re-export for backward compatibility)
from ..utils.file_ref import resolve_file_ref  # noqa: F401, E402


async def _save_chat_attachments(
    pool,
    tenant_id: str,
    session_id: str,
    file_metas: list,
) -> list:
    """
    Save attachment records to chat_attachments table.
    Finds the latest user message in the session and links attachments to it.
    Returns list of attachment dicts for the API response.
    """
    if not file_metas or not session_id:
        return []

    try:
        # Find the most recent user message in this session
        message_row = await pool.fetchrow(
            """
            SELECT id FROM chat_messages
            WHERE session_id = $1::uuid AND tenant_id = $2 AND role = 'user'
            ORDER BY created_at DESC LIMIT 1
            """,
            session_id,
            tenant_id,
        )
        if not message_row:
            logger.warning(
                "[Attachments] No user message found for session %s", session_id
            )
            return []

        message_id = message_row["id"]
        attachments = []

        for fm in file_metas:
            stored_path = fm.get("stored_path", "")
            # Build a storage_key relative to UPLOAD_BASE_DIR
            if stored_path.startswith(UPLOAD_BASE_DIR):
                storage_key = stored_path[len(UPLOAD_BASE_DIR) :].lstrip("/")
            else:
                storage_key = stored_path

            att_id = str(uuid_mod.uuid4())
            await pool.execute(
                """
                INSERT INTO chat_attachments (id, tenant_id, message_id, file_name, content_type, file_size, storage_key)
                VALUES ($1::uuid, $2, $3::uuid, $4, $5, $6, $7)
                """,
                att_id,
                tenant_id,
                str(message_id),
                fm.get("filename", "unknown"),
                fm.get("content_type", "application/octet-stream"),
                fm.get("size", 0),
                storage_key,
            )
            attachments.append(
                {
                    "id": att_id,
                    "file_name": fm.get("filename", "unknown"),
                    "content_type": fm.get("content_type", "application/octet-stream"),
                    "file_size": fm.get("size", 0),
                    "storage_key": storage_key,
                }
            )

        logger.info(
            "[Attachments] Saved %d attachments for message %s",
            len(attachments),
            str(message_id),
        )
        return attachments
    except Exception as e:
        logger.error("[Attachments] Failed to save: %s", e, exc_info=True)
        return []


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
                f"Tipe file '{ext}' tidak didukung untuk '{f.filename}'. "
                f"Didukung: {allowed}"
            )
            continue

        content = await f.read()
        await f.seek(0)
        if len(content) > UPLOAD_MAX_FILE_SIZE:
            size_mb = len(content) / (1024 * 1024)
            errors.append(
                f"File '{f.filename}' terlalu besar ({size_mb:.1f}MB). Maksimal 10MB."
            )

    return errors


async def _store_upload_file(file: UploadFile, tenant_id: str, pool) -> dict:
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
    file_already_exists = os.path.exists(store_path)
    if file_already_exists:
        logger.info(f"[FileUpload] Dedup hit: {file.filename} -> {file_hash[:12]}")
    else:
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

    # Insert into documents table for entity linking (Phase A — audit trail)
    try:
        relative_path = (
            store_path[len(UPLOAD_BASE_DIR) :].lstrip("/")
            if store_path.startswith(UPLOAD_BASE_DIR)
            else store_path
        )
        async with pool.acquire() as _doc_conn:
            async with _doc_conn.transaction():
                await _doc_conn.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")
                # Check if document already exists by SHA-256 (dedup)
                existing = await _doc_conn.fetchrow(
                    "SELECT id FROM documents WHERE tenant_id = $1 AND checksum_sha256 = $2 AND deleted_at IS NULL LIMIT 1",
                    tenant_id,
                    file_hash,
                )
                if existing:
                    file_meta["document_id"] = str(existing["id"])
                    logger.info(
                        f"[FileUpload] Document dedup: {file_hash[:12]} -> {existing['id']}"
                    )
                else:
                    # file_url points to chat file-serving endpoint (tenant-isolated)
                    file_url_path = (
                        f"/api/v3/chat/files/{tenant_id}/chat/{file_hash}{ext}"
                    )
                    doc_id = await _doc_conn.fetchval(
                        """INSERT INTO documents (
                            tenant_id, file_name, original_name, file_type, file_extension,
                            file_size, storage_type, file_path, file_url, category, checksum_sha256, source
                        ) VALUES ($1, $2, $3, $4, $5, $6, 'local', $7, $8, 'receipt', $9, 'chat')
                        RETURNING id""",
                        tenant_id,
                        file.filename or f"upload{ext}",
                        file.filename,
                        file.content_type,
                        ext,
                        len(content),
                        relative_path,
                        file_url_path,
                        file_hash,
                    )
                    file_meta["document_id"] = str(doc_id)
                    logger.info(f"[FileUpload] Document created: {doc_id}")
    except Exception as _doc_err:
        logger.warning(
            f"[FileUpload] documents table insert failed (non-blocking): {_doc_err}"
        )

    return file_meta


def _resize_image_for_vision(image_bytes: bytes, content_type: str) -> tuple:
    """
    Resize image to max VISION_MAX_DIMENSION px on longest side.
    Returns (resized_bytes, mime_type). Always outputs JPEG for compression.
    Graceful fallback: if Pillow fails, return original bytes.
    """
    try:
        from PIL import Image

        img = Image.open(BytesIO(image_bytes))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > VISION_MAX_DIMENSION:
            ratio = VISION_MAX_DIMENSION / max(w, h)
            new_size = (int(w * ratio), int(h * ratio))
            img = img.resize(new_size, Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=75)
        return buf.getvalue(), "image/jpeg"
    except Exception as e:
        logger.warning(f"[chat] Image resize failed, using original: {e}")
        return image_bytes, content_type


def _build_image_content_blocks(text: str, file_metas: List[dict]):
    """
    Build OpenAI vision content blocks if any uploaded files are images.
    Returns None if no images (caller should use plain text).
    Images are resized to max 1024px and JPEG-compressed before base64.
    """
    image_metas = [
        fm for fm in file_metas if fm.get("extension", "").lower() in IMAGE_EXTENSIONS
    ]
    if not image_metas:
        return None

    non_image_metas = [fm for fm in file_metas if fm not in image_metas]
    file_context = _build_file_context(non_image_metas) if non_image_metas else ""
    full_text = f"{text}\n\n{file_context}".strip() if file_context else text

    blocks = [{"type": "text", "text": full_text}]

    for fm in image_metas:
        stored_path = fm.get("stored_path", "")
        if not stored_path or not os.path.exists(stored_path):
            continue
        try:
            with open(stored_path, "rb") as f:
                raw_bytes = f.read()
            resized_bytes, mime = _resize_image_for_vision(
                raw_bytes, fm.get("content_type", "image/jpeg")
            )
            b64 = base64.b64encode(resized_bytes).decode("utf-8")
            blocks.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime};base64,{b64}",
                        "detail": "high",
                    },
                }
            )
        except Exception as e:
            logger.warning(f"[chat] Failed to read image {stored_path}: {e}")

    return blocks if len(blocks) > 1 else None


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
        attachments.append(
            f"[Attached: {fm['filename']}, {size_str}, {ext_label}, file_ref={file_ref}]"
        )

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
    pending_action_id: str = Field(
        ..., description="ID of the pending action to confirm"
    )
    doc_status: Literal["DRAFT", "POSTED"] = Field(
        "POSTED",
        description="Document status: DRAFT saves without posting, POSTED creates and posts",
    )
    payload_overrides: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional field overrides to merge into payload before execution",
    )


class CancelActionRequest(BaseModel):
    """Request to cancel a pending action."""

    conversation_id: str = Field(..., description="Conversation session ID")
    session_id: Optional[str] = Field(None, description="Session ID for 4-layer memory")
    pending_action_id: str = Field(
        ..., description="ID of the pending action to cancel"
    )
    is_edit: bool = Field(
        False, description="If true, preserve pending_payload and set editing_mode"
    )


class EditActionRequest(BaseModel):
    """Request to edit a pending action's payload via natural-language patch.

    Bucket 3 Step 2 (2026-04-29): parity with confirm/cancel for mid-flow edits.
    """

    conversation_id: str = Field(..., description="Conversation session ID")
    session_id: Optional[str] = Field(None, description="Session ID for 4-layer memory")
    pending_action_id: str = Field(..., description="ID of the pending action to edit")
    text: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Natural-language edit instruction",
    )


class ChatMessageResponse(BaseModel):
    """Unified response from agent chat endpoints."""

    message_id: str = Field(default_factory=lambda: str(uuid_mod.uuid4()))
    message_type: str = Field(
        ...,
        description="TEXT | ACTION_PREVIEW | ACTION_RESULT | CLARIFICATION | VALIDATION_ERROR | CHART",
    )
    text: Optional[str] = Field(None, description="Narrative text from agent")
    data: Optional[Dict[str, Any]] = Field(None, description="Typed data payload")
    trace_id: Optional[str] = Field(None, description="Trace ID for debugging")
    pending_action_id: Optional[str] = Field(
        None, description="Pending action ID if applicable"
    )
    # Agent telemetry (optional, useful for debugging)
    iterations: Optional[int] = Field(None, description="Agent loop iterations")
    tool_calls: Optional[List[Dict]] = Field(
        None, description="Tools called during processing"
    )
    model_used: Optional[str] = Field(None, description="LLM model used")
    latency_ms: Optional[int] = Field(None, description="Total processing time in ms")
    session_id: Optional[str] = Field(
        None, description="Session ID for conversation continuity"
    )
    workflow_continuation: Optional[bool] = Field(
        None, description="Auto-continue workflow after confirm"
    )
    thinking_stages: Optional[List[str]] = Field(
        None, description="Tool stage labels for UX thinking indicator"
    )


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
        raise HTTPException(
            status_code=401, detail="Invalid user context: missing tenant_id"
        )

    # Extract bearer token for downstream API calls
    auth_header = request.headers.get("authorization", "")
    auth_token = (
        auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else ""
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
        thinking_stages = agent_resp.get("thinking_stages")
        extra_data = agent_resp.get("extra_data")
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
        thinking_stages = getattr(agent_resp, "thinking_stages", None)
        extra_data = getattr(agent_resp, "extra_data", None)

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
    elif message_type == "CHART":
        # Chart visualization: data contains ChartSpec
        data = preview or {}
    elif message_type == "TUTORIAL_STEP":
        # Tutorial step: preview contains TutorialStepData
        data = preview or {}
    elif message_type == "CLARIFICATION":
        data = (
            extra_data
            if (
                extra_data
                and isinstance(extra_data, dict)
                and extra_data.get("options")
            )
            else None
        )
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
        thinking_stages=thinking_stages or None,
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

    # Pending Action Guard: check pending_actions table (single source of truth)
    # No more FSM state sync — pending_actions row is always accurate.
    #
    # Bucket A1 (2026-04-24): when a pending action exists, try to route
    # short natural-language confirm/reject utterances ("betul" / "batal")
    # directly into /confirm or /cancel. Previously such utterances fell
    # through to the NLU pipeline and `after_confirm` never fired, so
    # `action_patterns` was empty forever. Only route on ≤3-token utterances
    # to avoid hijacking "betul saya beli router" (Risk Flag 1).
    if body.session_id and body.text:
        try:
            db_pool_guard = await get_session_db_pool()
            pending_row = await db_pool_guard.fetchrow(
                "SELECT id FROM pending_actions "
                "WHERE conversation_id = $1 AND status = 'PENDING' "
                "  AND expires_at > now() "
                "ORDER BY created_at DESC LIMIT 1",
                body.session_id,
            )
            pending_id = str(pending_row["id"]) if pending_row else None

            if pending_id:
                message_lower = body.text.lower().strip()
                token_count = len(message_lower.split())

                if token_count <= 3 and token_count > 0:
                    tokens = set(message_lower.split())
                    is_confirm = any(kw in tokens for kw in CONFIRM_KEYWORDS)
                    is_cancel = any(kw in tokens for kw in CANCEL_KEYWORDS)

                    if is_confirm and not is_cancel:
                        logger.info(
                            "[BucketA1] Natural-language CONFIRM routed: "
                            "session=%s pending=%s text=%r",
                            body.session_id[:8],
                            pending_id[:8],
                            body.text[:40],
                        )
                        return await confirm_action(
                            request,
                            ConfirmActionRequest(
                                conversation_id=body.conversation_id,
                                session_id=body.session_id,
                                pending_action_id=pending_id,
                            ),
                        )
                    if is_cancel and not is_confirm:
                        logger.info(
                            "[BucketA1] Natural-language CANCEL routed: "
                            "session=%s pending=%s text=%r",
                            body.session_id[:8],
                            pending_id[:8],
                            body.text[:40],
                        )
                        return await cancel_action(
                            request,
                            CancelActionRequest(
                                conversation_id=body.conversation_id,
                                session_id=body.session_id,
                                pending_action_id=pending_id,
                            ),
                        )

                # BucketA1.8 (2026-04-29): mid-flow edit dispatch — parity with A1.7.
                # detect_edit_intent has CONFIRM/CANCEL precedence built-in,
                # so safe to evaluate after the short-token confirm/cancel block.
                # NOTE: token_count <= 3 gate is intentionally NOT applied here —
                # edits are typically 3-6 tokens ("qty jadi 5", "harga jadi 100rb").
                if detect_edit_intent(body.text):
                    logger.info(
                        "[BucketA1.8] NL EDIT routed: session=%s pending=%s text=%r",
                        body.session_id[:8],
                        pending_id[:8],
                        body.text[:60],
                    )
                    return await edit_action(
                        request,
                        EditActionRequest(
                            conversation_id=body.conversation_id,
                            session_id=body.session_id,
                            pending_action_id=pending_id,
                            text=body.text,
                        ),
                    )

                # Fall-through block: pending exists but utterance is not a
                # short confirm/reject — preserve old UX (block new work).
                return ChatMessageResponse(
                    message_type="TEXT",
                    text="Ada aksi yang menunggu konfirmasi. Silakan konfirmasi atau batalkan dulu sebelum mengirim pesan baru.",
                    session_id=body.session_id,
                )
        except HTTPException:
            raise
        except Exception:
            logger.exception("[BucketA1] Pending-action dispatch failed (non-fatal)")
            # Non-fatal — proceed to normal NLU pipeline

    # ── Bank selection re-trigger (from document clarification) ──
    # When user taps a bank option from CLARIFICATION, text = bank_account_id (UUID).
    # Check Layer 2 document_context.pending_bank_selection and re-resolve.
    _retrigger_sid = body.session_id or body.conversation_id
    if _retrigger_sid and body.text:
        try:
            _retrigger_pool = await get_session_db_pool()
            _retrigger_sm = SessionManager(
                db_pool=_retrigger_pool,
                tenant_id=ctx["tenant_id"],
                user_id=ctx["user_id"],
            )
            _retrigger_state = await _retrigger_sm.get_state(_retrigger_sid)
            _doc_ctx = (
                _retrigger_state.document_context if _retrigger_state else None
            ) or {}
            logger.info(
                "[DocRetrigger] doc_ctx keys: %s",
                list(_doc_ctx.keys()) if _doc_ctx else "empty",
            )
            if _doc_ctx.get("pending_bank_selection"):
                _selected_value = body.text.strip()

                # ── Direction selection: value starts with "direction:" ──
                # Direction pills inherit document_context TTL (~10 min).
                # If expired, saved_ocr_data will be None → graceful fallback.
                if _selected_value.startswith("direction:"):
                    _forced_dir = _selected_value.split(":", 1)[1]  # "in" or "out"
                    _saved_ocr = _doc_ctx.get("saved_ocr_data")

                    if _saved_ocr:
                        _saved_ocr["forced_direction"] = _forced_dir

                        # Re-run via DocumentIntakePipeline with forced_direction
                        import os as _v3_dir_os

                        _dir_intake_version = _v3_dir_os.environ.get(
                            "DOC_INTAKE_VERSION", "v2"
                        )
                        if _dir_intake_version == "v3":
                            from ..services.unified_agent.document_intake_v3.pipeline import (
                                DocumentIntakePipelineV3 as _DirPipeline,
                            )
                        else:
                            from ..services.unified_agent.document_intake import (
                                DocumentIntakePipeline as _DirPipeline,
                            )

                        _dir_pool = await get_session_db_pool()
                        _dir_intake = _DirPipeline(_dir_pool, ctx["tenant_id"])
                        try:
                            _dir_result = await _dir_intake.process(
                                _saved_ocr,
                                caption=_saved_ocr.get("user_caption", ""),
                            )
                        except Exception as _dir_err:
                            # V3 skip exceptions — fall back to V2 on re-trigger
                            if _dir_intake_version == "v3":
                                from ..services.unified_agent.document_intake_v3.pipeline import (
                                    _FallbackToGenericChatSkip as _V3GenDir,
                                    _FallbackToV2PreviewSkip as _V3V2Dir,
                                )

                                if isinstance(_dir_err, (_V3GenDir, _V3V2Dir)):
                                    logger.info(
                                        "[DocIntakeV3] re-trigger fallback to V2: %s",
                                        _dir_err,
                                    )
                                    from ..services.unified_agent.document_intake import (
                                        DocumentIntakePipeline as _V2DirPipeline,
                                    )

                                    _dir_intake = _V2DirPipeline(
                                        _dir_pool, ctx["tenant_id"]
                                    )
                                    _dir_result = await _dir_intake.process(
                                        _saved_ocr,
                                        caption=_saved_ocr.get("user_caption", ""),
                                    )
                                else:
                                    raise
                            else:
                                raise
                        _dir_resolved = _dir_result.resolved_action

                        if _dir_resolved and _dir_resolved.needs_clarification:
                            # Needs bank selection now — update state and show bank pills
                            _doc_ctx_updated = {
                                "pending_bank_selection": True,
                                "resolved_action_key": _dir_resolved.action_key,
                                "resolved_payload": _dir_resolved.payload,
                                "doc_type": _saved_ocr.get("doc_type"),
                                "vendor_name": _saved_ocr.get("vendor_name")
                                or _saved_ocr.get("customer_name"),
                                "total_amount": float(
                                    _saved_ocr.get("total_amount", 0) or 0
                                ),
                                "match_label": _dir_result.best_match.label
                                if _dir_result.best_match
                                else None,
                            }
                            await _retrigger_sm.update_state(
                                body.session_id, document_context=_doc_ctx_updated
                            )
                            _cl_options = [
                                {
                                    "label": opt.label,
                                    "value": opt.value,
                                    "description": "",
                                }
                                for opt in _dir_resolved.clarification_options
                            ]
                            return ChatMessageResponse(
                                message_type="CLARIFICATION",
                                text=_dir_resolved.clarification_question,
                                data={
                                    "question": _dir_resolved.clarification_question,
                                    "options": _cl_options,
                                    "allow_freetext": False,
                                },
                                session_id=body.session_id,
                            )
                        elif _dir_resolved and _dir_resolved.action_key:
                            # Action resolved — propose directly
                            _te_dir = ToolExecutor(
                                context=TenantContext(
                                    tenant_id=ctx["tenant_id"],
                                    user_id=ctx["user_id"],
                                    auth_token=ctx["auth_token"],
                                    tenant_name=ctx.get(
                                        "tenant_name", ctx["tenant_id"]
                                    ),
                                ),
                                session_id=body.session_id,
                            )
                            _propose_dir = await _te_dir._execute_propose_direct(
                                {
                                    "action_key": _dir_resolved.action_key,
                                    "payload": _dir_resolved.payload,
                                }
                            )
                            if _propose_dir.get("success"):
                                await _retrigger_sm.update_state(
                                    body.session_id,
                                    document_context={
                                        "pending_bank_selection": False,
                                        "pending_action_id": _propose_dir["data"].get(
                                            "pending_action_id"
                                        ),
                                    },
                                )
                                _dir_label = (
                                    "masuk" if _forced_dir == "in" else "keluar"
                                )
                                return ChatMessageResponse(
                                    message_type="DIRECT_ACTION_PREVIEW",
                                    text=f"Pembayaran {_dir_label} dipilih.",
                                    data=_propose_dir["data"],
                                    pending_action_id=_propose_dir["data"].get(
                                        "pending_action_id"
                                    ),
                                    session_id=body.session_id,
                                )
                            else:
                                await _retrigger_sm.update_state(
                                    body.session_id,
                                    document_context={"pending_bank_selection": False},
                                )
                                return ChatMessageResponse(
                                    message_type="TEXT",
                                    text=_propose_dir.get("error", "Gagal memproses."),
                                    session_id=body.session_id,
                                )
                    else:
                        # No saved OCR — can not re-run, clear state
                        await _retrigger_sm.update_state(
                            body.session_id,
                            document_context={"pending_bank_selection": False},
                        )
                        return ChatMessageResponse(
                            message_type="TEXT",
                            text="Sesi dokumen sudah kedaluwarsa. Silakan upload ulang.",
                            session_id=body.session_id,
                        )

                # ── Bank selection (existing path — UUID value) ──
                _selected_bank_id = _selected_value
                _resolved_payload = _doc_ctx.get("resolved_payload", {})
                _action_key = _doc_ctx.get("resolved_action_key", "")

                if _action_key and _resolved_payload:
                    # Inject selected bank into payload
                    _resolved_payload["bank_account_id"] = _selected_bank_id
                    if "paid_through_id" in _resolved_payload:
                        _resolved_payload["paid_through_id"] = _selected_bank_id

                    # Resolve bank display name
                    _bank_row = await _retrigger_pool.fetchrow(
                        "SELECT account_name, bank_name, account_number FROM bank_accounts "
                        "WHERE id = $1 AND tenant_id = $2",
                        __import__("uuid").UUID(_selected_bank_id),
                        ctx["tenant_id"],
                    )
                    if _bank_row:
                        _bank_display = _bank_row["bank_name"] or ""
                        if _bank_row["account_name"]:
                            _bank_display += (
                                f" - {_bank_row['account_name']}"
                                if _bank_display
                                else _bank_row["account_name"]
                            )
                        _resolved_payload["bank_account_name"] = _bank_display
                        if "paid_through_name" in _resolved_payload:
                            _resolved_payload["paid_through_name"] = _bank_display

                    # Propose the action via ToolExecutor
                    _te_retrigger = ToolExecutor(
                        context=TenantContext(
                            tenant_id=ctx["tenant_id"],
                            user_id=ctx["user_id"],
                            auth_token=ctx["auth_token"],
                            tenant_name=ctx.get("tenant_name", ctx["tenant_id"]),
                        ),
                        session_id=body.session_id,
                    )
                    logger.info(
                        "[DocRetrigger] Calling propose now, key=%s payload_keys=%s",
                        _action_key,
                        list(_resolved_payload.keys()),
                    )
                    try:
                        _propose_result = await _te_retrigger._execute_propose_direct(
                            {
                                "action_key": _action_key,
                                "payload": _resolved_payload,
                            }
                        )
                        logger.info(
                            "[DocRetrigger] propose result: success=%s error=%s",
                            _propose_result.get("success"),
                            _propose_result.get("error", "")[:200],
                        )
                    except Exception as _pe:
                        logger.error(
                            "[DocRetrigger] propose EXCEPTION: %s", _pe, exc_info=True
                        )
                        _propose_result = {"success": False, "error": str(_pe)}
                    if _propose_result.get("success"):
                        _vendor_rt = _doc_ctx.get("vendor_name", "Unknown")
                        _total_rt = _doc_ctx.get("total_amount", 0)
                        _match_label = _doc_ctx.get("match_label", "")
                        _narration_rt = f"Rekening dipilih: **{_bank_display}**."
                        if _match_label:
                            _narration_rt += f" Membayar **{_match_label}**."

                        # Clear pending_bank_selection
                        try:
                            await _retrigger_sm.update_state(
                                body.session_id,
                                document_context={
                                    "pending_bank_selection": False,
                                    "resolved_action_key": _action_key,
                                    "vendor_name": _vendor_rt,
                                    "total_amount": _total_rt,
                                    "pending_action_id": _propose_result["data"].get(
                                        "pending_action_id"
                                    ),
                                },
                            )
                        except Exception:
                            pass

                        return ChatMessageResponse(
                            message_type="DIRECT_ACTION_PREVIEW",
                            text=_narration_rt,
                            data=_propose_result["data"],
                            pending_action_id=_propose_result["data"].get(
                                "pending_action_id"
                            ),
                            session_id=body.session_id,
                        )
                    else:
                        # propose failed — return error message, DON'T fall through to pipeline
                        _err_msg = _propose_result.get(
                            "error", "Gagal memproses dokumen."
                        )
                        # Clear pending state so user can retry
                        try:
                            await _retrigger_sm.update_state(
                                body.session_id,
                                document_context={"pending_bank_selection": False},
                            )
                        except Exception:
                            pass
                        return ChatMessageResponse(
                            message_type="TEXT",
                            text=_err_msg,
                            session_id=body.session_id,
                        )
        except Exception as _retrigger_err:
            logger.debug("[DocRetrigger] Not a bank selection: %s", _retrigger_err)

    # ── Editing Mode Handler: apply field revisions to pending payload ──
    if body.session_id and body.text:
        try:
            from ..services.unified_agent.db_utils import (
                get_session_db_pool as _get_edit_pool,
            )

            _edit_pool = await _get_edit_pool()
            _edit_sm = SessionManager(
                db_pool=_edit_pool, tenant_id=ctx["tenant_id"], user_id=ctx["user_id"]
            )
            _edit_state = await _edit_sm.get_state(body.session_id)
            if _edit_state and _edit_state.editing_mode and _edit_state.pending_payload:
                _payload = dict(_edit_state.pending_payload)
                _intent = _edit_state.pending_intent or ""
                _user_text = body.text.strip().lower()
                logger.info(
                    "[EditMode] Active for session=%s intent=%s text=%s",
                    body.session_id[:8],
                    _intent,
                    _user_text[:60],
                )

                # ── LLM-based field extraction (Gemini Flash Lite, ~200ms) ──
                from ..services.llm import LLMRouter, LLMMessage
                import json as _edit_json

                _edit_router = LLMRouter.from_env()
                _payload_summary = ", ".join(
                    f"{k}={v}"
                    for k, v in _payload.items()
                    if k not in ("_uploaded_document_id",) and v and str(v) != "null"
                )
                _edit_prompt = f"""User mau edit data berikut:
{_payload_summary}

User bilang: "{body.text}"

Ekstrak field mana yang mau diubah dan nilai barunya. Return JSON ONLY:
{{"changes": {{"field_name": new_value, ...}}}}

Rules:
- field_name HARUS salah satu dari: amount, expense_date, description, vendor_name, account_name, reference, paid_through_name
- amount: angka tanpa Rp/titik/koma. "120rb"=120000, "Rp 120.000"=120000, "1.5jt"=1500000
- expense_date: format YYYY-MM-DD. "hari ini"=tanggal hari ini, "kemarin"=tanggal kemarin
- Jika user hanya sebut angka tanpa field, assume amount
- Jika tidak ada perubahan yang jelas, return {{"changes": {{}}}}"""

                _edit_resp = await _edit_router.complete(
                    task_type="extraction",
                    messages=[LLMMessage(role="user", content=_edit_prompt)],
                    temperature=0.0,
                    max_tokens=200,
                )
                _edit_text = (_edit_resp.content or "").strip()
                if _edit_text.startswith("```"):
                    _edit_text = _edit_text.split("\n", 1)[-1].rsplit("```", 1)[0]

                _changes = {}
                try:
                    _parsed = _edit_json.loads(_edit_text)
                    _changes = _parsed.get("changes", {})
                except (ValueError, KeyError):
                    logger.warning("[EditMode] LLM parse failed: %s", _edit_text[:100])

                _changed = bool(_changes)
                if _changed:
                    for _ck, _cv in _changes.items():
                        if _ck in ("amount",) and _cv is not None:
                            _payload[_ck] = float(_cv)
                        elif _cv is not None:
                            _payload[_ck] = str(_cv)
                        logger.info(
                            "[EditMode] LLM extracted: %s = %s", _ck, str(_cv)[:50]
                        )

                if _changed:
                    # Determine action_key from intent
                    _action_key = _intent if _intent else "create_expense"
                    if not _action_key.startswith(
                        "create_"
                    ) and not _action_key.startswith("void_"):
                        _action_key = (
                            f"create_{_action_key}"
                            if "expense" in _action_key
                            else _action_key
                        )

                    # Re-propose with updated payload
                    _te_edit = ToolExecutor(
                        context=TenantContext(
                            tenant_id=ctx["tenant_id"],
                            user_id=ctx["user_id"],
                            auth_token=ctx["auth_token"],
                            tenant_name=ctx.get("tenant_name", ctx["tenant_id"]),
                        ),
                        session_id=body.session_id,
                    )
                    _propose_edit = await _te_edit._execute_propose_direct(
                        {
                            "action_key": _action_key,
                            "payload": _payload,
                        }
                    )

                    if _propose_edit.get("success"):
                        # Save updated payload and clear editing mode
                        await _edit_sm.update_state(
                            body.session_id,
                            editing_mode=False,
                            pending_payload=_payload,
                            pending_intent=_action_key,
                        )
                        return ChatMessageResponse(
                            message_type="DIRECT_ACTION_PREVIEW",
                            text="Sudah diperbarui:",
                            data=_propose_edit["data"],
                            pending_action_id=_propose_edit["data"].get(
                                "pending_action_id"
                            ),
                            session_id=body.session_id,
                        )
                    else:
                        # Propose failed — report error but stay in edit mode
                        return ChatMessageResponse(
                            message_type="TEXT",
                            text=_propose_edit.get(
                                "error", "Gagal memperbarui. Coba lagi."
                            ),
                            session_id=body.session_id,
                        )
                else:
                    # Couldn't parse — ask user to be more specific
                    _field_list = (
                        "jumlah, tanggal, deskripsi, vendor, akun biaya, referensi"
                    )
                    return ChatMessageResponse(
                        message_type="TEXT",
                        text=f"Mau ubah field yang mana? Contoh: 'ubah jumlah jadi 120000'. Field: {_field_list}",
                        session_id=body.session_id,
                    )
        except Exception as _edit_err:
            logger.warning("[EditMode] Error: %s", _edit_err, exc_info=True)

        # ── Workflow continuation shortcut ──
    # If user sends "lanjut" etc. and there's an active workflow in REVIEWING state,
    # bypass LLM and directly resume workflow for reliability + speed.
    if body.session_id and body.text:
        _resume_keywords = {
            "lanjut",
            "next",
            "ok",
            "oke",
            "lanjutkan",
            "berikutnya",
            "terus",
            "ya",
            "iya",
        }
        _text_lower = body.text.strip().lower().rstrip(".!,")
        _is_resume = (
            _text_lower in _resume_keywords
            or "lanjut" in _text_lower
            or "next item" in _text_lower
            or "item berikut" in _text_lower
        )
        if _is_resume:
            try:
                from ..services.unified_agent.workflow_engine import WorkflowEngine

                _wf_pool = await get_session_db_pool()
                _wf_engine = WorkflowEngine(
                    db_pool=_wf_pool,
                    tenant_id=ctx["tenant_id"],
                    user_id=ctx["user_id"],
                    auth_token=ctx["auth_token"],
                )
                _wf_state = await _wf_engine.get_state(
                    body.session_id, "bank_reconciliation"
                )
                logger.info(
                    f"[RECON-LANJUT] conv={body.session_id} "
                    f"state={_wf_state.current_state if _wf_state else 'none'}"
                )
                _shortcut_states = {"REVIEWING", "BALANCE_PROOF", "FINALIZE"}
                if (
                    _wf_state
                    and _wf_state.status == "active"
                    and _wf_state.current_state in _shortcut_states
                ):
                    logger.info(
                        f"[UnifiedChat] Workflow shortcut: resuming REVIEWING for session {body.session_id}"
                    )
                    _tenant_ctx = TenantContext(
                        tenant_id=ctx["tenant_id"],
                        user_id=ctx["user_id"],
                        auth_token=ctx["auth_token"],
                        tenant_name=ctx.get("tenant_name", ctx["tenant_id"]),
                    )
                    _sm = SessionManager(
                        db_pool=_wf_pool,
                        tenant_id=ctx["tenant_id"],
                        user_id=ctx["user_id"],
                    )
                    _te = ToolExecutor(
                        context=_tenant_ctx,
                        session_manager=_sm,
                        session_id=body.session_id,
                        user_text=body.text,
                    )
                    _wf_result = await _te._execute_start_workflow(
                        {
                            "workflow_type": "bank_reconciliation",
                        }
                    )
                    logger.info(
                        f"[WfShortcut] result msg_type={_wf_result.get('message_type') if isinstance(_wf_result, dict) else 'N/A'}"
                    )
                    # Convert ToolExecutor result to ChatMessageResponse
                    _pa_id = None
                    if isinstance(_wf_result, dict):
                        _pa_id = _wf_result.get("pending_action_id") or (
                            _wf_result.get("data", {}).get("pending_action_id")
                            if isinstance(_wf_result.get("data"), dict)
                            else None
                        )
                    if isinstance(_wf_result, dict) and (
                        _pa_id
                        or _wf_result.get("message_type") == "DIRECT_ACTION_PREVIEW"
                    ):
                        return ChatMessageResponse(
                            message_type="DIRECT_ACTION_PREVIEW",
                            text=_wf_result.get("content")
                            or _wf_result.get("text", ""),
                            session_id=body.session_id,
                            pending_action_id=_pa_id,
                            data=_wf_result.get("data"),
                        )
                    else:
                        # Workflow advanced past REVIEWING (BALANCE_PROOF, FINALIZE, COMPLETED)
                        if isinstance(_wf_result, dict) and _wf_result.get("completed"):
                            # Build user-facing completion summary
                            ar = _wf_result.get("auto_results") or {}
                            bp = ar.get("balance_proof") or {}
                            _acct = bp.get("account_name", "")
                            _matched = bp.get("matched_count", 0)
                            _total = bp.get("total_statement_lines", 0)
                            _lines = [
                                f"Rekonsiliasi {_acct} selesai! {_matched} dari {_total} transaksi berhasil dicocokkan."
                            ]
                            return ChatMessageResponse(
                                message_type="TEXT",
                                text="\n".join(_lines),
                                session_id=body.session_id,
                                data=_wf_result.get("auto_results"),
                            )
                        elif (
                            isinstance(_wf_result, dict)
                            and _wf_result.get("current_state") == "FINALIZE"
                        ):
                            # Can't auto-complete — show blockers
                            ar = _wf_result.get("auto_results") or {}
                            bp = ar.get("balance_proof") or {}
                            _blockers = bp.get("completion_blockers", [])
                            _unmatched_ct = bp.get("unmatched_count", 0)
                            _diff = bp.get("difference")
                            _parts = []
                            if _unmatched_ct > 0:
                                _parts.append(
                                    f"masih ada {_unmatched_ct} transaksi yang belum dicocokkan"
                                )
                            if _diff and float(_diff) != 0:
                                _diff_fmt = f"Rp {int(abs(float(_diff))):,}".replace(
                                    ",", "."
                                )
                                _parts.append(f"selisih {_diff_fmt}")
                            if _parts:
                                _lines = [
                                    "Rekonsiliasi belum bisa diselesaikan karena "
                                    + " dan ".join(_parts)
                                    + "."
                                ]
                            else:
                                _lines = ["Rekonsiliasi belum bisa diselesaikan."]
                            _lines.append("")
                            _lines.append(
                                'Selesaikan item yang tersisa dulu, atau ketik "selesai" kalau mau finalisasi dengan selisih.'
                            )
                            return ChatMessageResponse(
                                message_type="TEXT",
                                text="\n".join(_lines),
                                session_id=body.session_id,
                                data=_wf_result.get("auto_results"),
                            )
                        else:
                            _text = ""
                            if isinstance(_wf_result, dict):
                                _text = (
                                    _wf_result.get("text")
                                    or _wf_result.get("content")
                                    or ""
                                )
                            return ChatMessageResponse(
                                message_type="TEXT",
                                text=_text or "Lanjutkan.",
                                session_id=body.session_id,
                                data=_wf_result.get("data")
                                if isinstance(_wf_result, dict)
                                else None,
                            )
            except Exception as e:
                import traceback

                logger.warning(
                    f"[UnifiedChat] Workflow shortcut failed: {e}\n{traceback.format_exc()}"
                )
                pass  # Fall through to normal LLM processing

    # Build tenant context for tool executor
    tenant_context = TenantContext(
        tenant_id=ctx["tenant_id"],
        user_id=ctx["user_id"],
        auth_token=ctx["auth_token"],
        tenant_name=ctx.get("tenant_name", ctx["tenant_id"]),
    )

    # Fetch conversation history for context
    # When session_id is provided, let SessionAwareAgent build 4-layer context
    # (Layer 2 structured state, Layer 3 events, Layer 4 summary)
    history = None
    if not body.session_id:
        history = await _get_conversation_history(
            user_id=ctx["user_id"],
            tenant_id=ctx["tenant_id"],
            conversation_id=body.conversation_id,
            limit=10,
        )

    # ── Tier 2: Preference intent detection (before agent loop) ──
    try:
        from ..services.unified_agent.preference_detector import (
            detect_preference_intent,
        )
        from ..services.unified_agent.preference_manager import PreferenceManager

        _pref_pool = await get_session_db_pool()
        _pref_mgr = PreferenceManager(_pref_pool, ctx["tenant_id"], ctx["user_id"])
        _pref_result = await detect_preference_intent(body.text, _pref_mgr)
        if _pref_result and _pref_result.get("handled"):
            # Store message for history
            if body.session_id:
                try:
                    _pref_sm = SessionManager(
                        db_pool=_pref_pool,
                        tenant_id=ctx["tenant_id"],
                        user_id=ctx["user_id"],
                    )
                    await _pref_sm.store_message(body.session_id, "user", body.text)
                    await _pref_sm.store_message(
                        body.session_id, "assistant", _pref_result["response"]
                    )
                except Exception:
                    pass
            return ChatMessageResponse(
                message_type="TEXT",
                text=_pref_result["response"],
                session_id=body.session_id,
            )
    except Exception as _pref_err:
        logger.warning("[Tier2] Preference detection failed (non-fatal): %s", _pref_err)

    # Run the agent loop
    agent_resp = await _agent.process_message(
        user_text=body.text,
        context=tenant_context,
        conversation_history=history,
        session_id=body.session_id,
    )

    # Ensure session_id is propagated to response (AgentResponse may not set it)
    if (
        body.session_id
        and hasattr(agent_resp, "session_id")
        and not agent_resp.session_id
    ):
        agent_resp.session_id = body.session_id

    # Post-process: strip draft/void rows from tables
    if hasattr(agent_resp, "content") and agent_resp.content:
        agent_resp.content = _strip_draft_void_rows(agent_resp.content)
    elif isinstance(agent_resp, dict) and agent_resp.get("content"):
        agent_resp["content"] = _strip_draft_void_rows(agent_resp["content"])

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
    msg_type = (
        agent_resp.get("message_type")
        if isinstance(agent_resp, dict)
        else agent_resp.message_type
    )
    iterations = (
        agent_resp.get("iterations", 0)
        if isinstance(agent_resp, dict)
        else agent_resp.iterations
    )
    tool_calls = (
        agent_resp.get("tool_calls_made", [])
        if isinstance(agent_resp, dict)
        else (agent_resp.tool_calls_made or [])
    )
    model_used = (
        agent_resp.get("model_used")
        if isinstance(agent_resp, dict)
        else agent_resp.model_used
    )
    latency = (
        agent_resp.get("total_latency_ms", 0)
        if isinstance(agent_resp, dict)
        else agent_resp.total_latency_ms
    )

    # Extract token usage for monitoring
    _usage = (
        agent_resp.get("usage", {})
        if isinstance(agent_resp, dict)
        else getattr(agent_resp, "usage", {}) or {}
    )
    _tin = _usage.get("prompt_tokens", 0)
    _tout = _usage.get("completion_tokens", 0)
    _cached = 0
    _ptd = _usage.get("prompt_tokens_details")
    if isinstance(_ptd, dict):
        _cached = _ptd.get("cached_tokens", 0)

    logger.info(
        f"[UnifiedAgent] user={ctx['user_id']} type={msg_type} "
        f"iterations={iterations} tools={len(tool_calls)} "
        f"model={model_used} latency={latency}ms "
        f"tokens_in={_tin} tokens_out={_tout} cached={_cached}"
    )

    # Phase 4C: Fire-and-forget telemetry recording
    try:
        _cls_intent = _usage.get("classifier_intent", "")
        _cls_conf = _usage.get("classifier_confidence", 0)
        _cls_skip = _usage.get("classifier_skipped", False)
        _cls_tin = _usage.get("classifier_tokens_in", 0)
        _cls_tout = _usage.get("classifier_tokens_out", 0)
        _cls_lat = _usage.get("classifier_latency_ms", 0)
        _cls_fallback = _usage.get("low_confidence_fallback", False)

        _asyncio_stream.create_task(
            record_telemetry(
                db_pool=_agent.db_pool if hasattr(_agent, "db_pool") else None,
                tenant_id=ctx["tenant_id"],
                user_id=ctx["user_id"],
                session_id=body.session_id,
                intent=_cls_intent or msg_type,
                confidence=float(_cls_conf) if _cls_conf else None,
                classifier_skipped=bool(_cls_skip),
                classifier_tokens_in=int(_cls_tin),
                classifier_tokens_out=int(_cls_tout),
                classifier_latency_ms=int(_cls_lat),
                low_confidence_fallback=bool(_cls_fallback),
                tools_loaded=0,
                tools_called=len(tool_calls),
                iteration_count=iterations,
                input_tokens=_tin,
                output_tokens=_tout,
                cached_tokens=_cached,
                total_latency_ms=latency,
                message_type=msg_type,
                model_used=model_used,
            )
        )
    except Exception as _tel_err:
        logger.warning("[TELEMETRY] Failed to record: %s", _tel_err)

    return _to_chat_response(agent_resp)


# =============================================================================
# POST /message/stream — SSE streaming endpoint with thinking steps
# =============================================================================


@router.post("/message/stream")
async def send_message_stream(request: Request, body: ChatMessageRequest):
    """
    SSE streaming endpoint. Sends thinking steps in real-time.
    Same auth/validation as /message, but returns Server-Sent Events.

    Events emitted:
      THINKING_STEP  - Tool execution progress (running/done)
      THINKING_DONE  - Agent finished thinking
      DONE           - Final response (same shape as /message response)
      ERROR          - Processing error
      HEARTBEAT      - Keep-alive ping
    """
    ctx = _get_user_context(request)

    # Pending Action Guard (streaming endpoint)
    # BucketA1.7 (2026-04-25): mirror A1's natural-language confirm/reject
    # dispatch from /message into /message/stream. Without this, "betul"/
    # "batal" after a preview gets blocked by the guard and after_confirm
    # never fires for production users (frontend uses SSE).
    if body.session_id:
        try:
            db_pool_guard = await get_session_db_pool()
            pending_row_stream = await db_pool_guard.fetchrow(
                "SELECT id FROM pending_actions "
                "WHERE conversation_id = $1 AND status = 'PENDING' "
                "  AND expires_at > now() "
                "ORDER BY created_at DESC LIMIT 1",
                body.session_id,
            )
            pending_id_stream = (
                str(pending_row_stream["id"]) if pending_row_stream else None
            )

            if pending_id_stream:
                _confirm_resp_stream = None
                if body.text:
                    message_lower = body.text.lower().strip()
                    token_count = len(message_lower.split())
                    if token_count <= 3 and token_count > 0:
                        tokens = set(message_lower.split())
                        is_confirm = any(kw in tokens for kw in CONFIRM_KEYWORDS)
                        is_cancel = any(kw in tokens for kw in CANCEL_KEYWORDS)
                        try:
                            if is_confirm and not is_cancel:
                                logger.info(
                                    "[BucketA1] Stream NL CONFIRM routed: "
                                    "session=%s pending=%s text=%r",
                                    body.session_id[:8],
                                    pending_id_stream[:8],
                                    body.text[:40],
                                )
                                _confirm_resp_stream = await confirm_action(
                                    request,
                                    ConfirmActionRequest(
                                        conversation_id=body.conversation_id,
                                        session_id=body.session_id,
                                        pending_action_id=pending_id_stream,
                                    ),
                                )
                            elif is_cancel and not is_confirm:
                                logger.info(
                                    "[BucketA1] Stream NL CANCEL routed: "
                                    "session=%s pending=%s text=%r",
                                    body.session_id[:8],
                                    pending_id_stream[:8],
                                    body.text[:40],
                                )
                                _confirm_resp_stream = await cancel_action(
                                    request,
                                    CancelActionRequest(
                                        conversation_id=body.conversation_id,
                                        session_id=body.session_id,
                                        pending_action_id=pending_id_stream,
                                    ),
                                )
                        except (
                            KeyError,
                            TypeError,
                            ValueError,
                            asyncpg.PostgresError,
                            json.JSONDecodeError,
                        ) as _disp_err:
                            logger.error(
                                "[BucketA1] Stream NL dispatch failed: %s",
                                _disp_err,
                                exc_info=True,
                            )
                            _confirm_resp_stream = None

                if _confirm_resp_stream is not None:
                    _resp_payload = (
                        _confirm_resp_stream.model_dump()
                        if hasattr(_confirm_resp_stream, "model_dump")
                        else dict(_confirm_resp_stream)
                    )

                    async def _confirm_dispatch_gen(payload=_resp_payload):
                        yield f"data: {_json_stream.dumps({'event': 'DONE', 'data': payload}, default=str)}\n\n"

                    return StreamingResponse(
                        _confirm_dispatch_gen(),
                        media_type="text/event-stream",
                        headers={
                            "Cache-Control": "no-cache",
                            "Connection": "keep-alive",
                            "X-Accel-Buffering": "no",
                        },
                    )

                # BucketA1.8 (2026-04-29): stream NL edit dispatch — parity with A1.7.
                # No token_count cap (edits are 3-6 tokens). Safe after confirm/cancel
                # because detect_edit_intent enforces CONFIRM/CANCEL precedence internally.
                if body.text and detect_edit_intent(body.text):
                    try:
                        logger.info(
                            "[BucketA1.8] Stream NL EDIT routed: session=%s pending=%s text=%r",
                            body.session_id[:8],
                            pending_id_stream[:8],
                            body.text[:60],
                        )
                        _edit_resp_stream = await edit_action(
                            request,
                            EditActionRequest(
                                conversation_id=body.conversation_id,
                                session_id=body.session_id,
                                pending_action_id=pending_id_stream,
                                text=body.text,
                            ),
                        )
                        _edit_payload = (
                            _edit_resp_stream.model_dump()
                            if hasattr(_edit_resp_stream, "model_dump")
                            else dict(_edit_resp_stream)
                        )

                        async def _edit_dispatch_gen(payload=_edit_payload):
                            yield f"data: {_json_stream.dumps({'event': 'DONE', 'data': payload}, default=str)}\n\n"

                        return StreamingResponse(
                            _edit_dispatch_gen(),
                            media_type="text/event-stream",
                            headers={
                                "Cache-Control": "no-cache",
                                "Connection": "keep-alive",
                                "X-Accel-Buffering": "no",
                            },
                        )
                    except (
                        KeyError,
                        TypeError,
                        ValueError,
                        asyncpg.PostgresError,
                        json.JSONDecodeError,
                    ) as _edit_err:
                        logger.error(
                            "[BucketA1.8] Stream NL EDIT dispatch failed: %s",
                            _edit_err,
                            exc_info=True,
                        )
                        # Fall through to guard TEXT below

                # Fall-through: pending exists but utterance is not a short
                # confirm/reject — preserve old "Ada aksi menunggu" UX.
                async def _guard_gen():
                    _resp = {
                        "event": "DONE",
                        "data": {
                            "message_type": "TEXT",
                            "text": "Ada aksi yang menunggu konfirmasi. Silakan konfirmasi atau batalkan dulu sebelum mengirim pesan baru.",
                            "session_id": body.session_id,
                        },
                    }
                    yield f"data: {_json_stream.dumps(_resp, default=str)}\n\n"

                return StreamingResponse(
                    _guard_gen(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    },
                )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "[BucketA1] Stream pending-action dispatch failed (non-fatal)"
            )

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

    # ── Tier 2: Preference intent detection (streaming endpoint) ──
    try:
        from ..services.unified_agent.preference_detector import (
            detect_preference_intent,
        )
        from ..services.unified_agent.preference_manager import PreferenceManager

        _pref_pool_s = await get_session_db_pool()
        _pref_mgr_s = PreferenceManager(_pref_pool_s, ctx["tenant_id"], ctx["user_id"])
        _pref_result_s = await detect_preference_intent(body.text, _pref_mgr_s)
        if _pref_result_s and _pref_result_s.get("handled"):
            if body.session_id:
                try:
                    _pref_sm_s = SessionManager(
                        db_pool=_pref_pool_s,
                        tenant_id=ctx["tenant_id"],
                        user_id=ctx["user_id"],
                    )
                    await _pref_sm_s.store_message(body.session_id, "user", body.text)
                    await _pref_sm_s.store_message(
                        body.session_id, "assistant", _pref_result_s["response"]
                    )
                except Exception:
                    pass

            async def _pref_sse_gen():
                import json as _pj

                yield f"data: {_pj.dumps({'event': 'FINAL_RESPONSE', 'data': {'message_type': 'TEXT', 'text': _pref_result_s['response'], 'session_id': body.session_id or ''}})}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(_pref_sse_gen(), media_type="text/event-stream")
    except Exception as _pref_err_s:
        logger.warning("[Tier2] Preference detection (stream) failed: %s", _pref_err_s)

    queue: _asyncio_stream.Queue = _asyncio_stream.Queue()

    async def event_callback(event_type: str, data: dict):
        await queue.put({"event": event_type, "data": data})

    async def run_agent():
        try:
            agent_resp = await _agent.process_message(
                user_text=body.text,
                context=tenant_context,
                conversation_history=history,
                session_id=body.session_id,
                event_callback=event_callback,
            )

            # Convert agent response to the same format as /message
            chat_resp = _to_chat_response(agent_resp)
            response_data = {
                "message_id": chat_resp.message_id,
                "message_type": chat_resp.message_type,
                "text": chat_resp.text,
                "data": chat_resp.data,
                "session_id": body.session_id or chat_resp.session_id,
                "pending_action_id": chat_resp.pending_action_id,
                "iterations": chat_resp.iterations,
                "model_used": chat_resp.model_used,
                "latency_ms": chat_resp.latency_ms,
                "thinking_stages": chat_resp.thinking_stages,
            }
            await queue.put({"event": "DONE", "data": response_data})
        except Exception as e:
            import traceback

            traceback.print_exc()
            await queue.put({"event": "ERROR", "data": {"message": str(e)}})

    task = _asyncio_stream.create_task(run_agent())

    async def generate():
        _heartbeat_interval = 30.0  # seconds
        _total_timeout = 120.0  # seconds
        _elapsed = 0.0
        _chunk_timeout = min(_heartbeat_interval, _total_timeout)

        while _elapsed < _total_timeout:
            try:
                event = await _asyncio_stream.wait_for(
                    queue.get(), timeout=_chunk_timeout
                )
            except _asyncio_stream.TimeoutError:
                _elapsed += _chunk_timeout
                yield f"data: {_json_stream.dumps({'event': 'HEARTBEAT', 'data': {}})}\n\n"
                continue

            yield f"data: {_json_stream.dumps(event, default=str)}\n\n"

            if event["event"] in ("DONE", "ERROR"):
                break
        else:
            # Total timeout reached
            yield f"data: {_json_stream.dumps({'event': 'ERROR', 'data': {'message': 'Request timeout'}})}\n\n"

        if not task.done():
            task.cancel()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# =============================================================================
# POST /message/upload — Send message with file attachments (multipart)
# =============================================================================


@router.post("/message/upload", response_model=ChatMessageResponse)
async def send_message_with_files(
    request: Request,
    conversation_id: str = Form(..., description="Conversation session ID"),
    text: str = Form(
        "", max_length=2000, description="User message (optional for file-only uploads)"
    ),
    session_id: Optional[str] = Form(None, description="Session ID for 4-layer memory"),
    files: List[UploadFile] = File(
        default=[], description="Attached files (max 5, max 10MB each)"
    ),
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
    image_content = None
    if file_metas:
        file_context = _build_file_context(file_metas)
        enriched_text = f"{text}\n\n{file_context}"
        # Build vision content blocks if images present
        image_content = _build_image_content_blocks(text, file_metas)

    # ── FSM Guard (same as /message) ──
    if session_id:
        try:
            db_pool_guard = await get_session_db_pool()
            _sm_guard = SessionManager(
                db_pool=db_pool_guard,
                tenant_id=ctx["tenant_id"],
                user_id=ctx["user_id"],
            )
            # BucketA1.7 (2026-04-25): mirror A1's natural-language confirm/
            # reject dispatch from /message into /message/upload. Without this,
            # short "betul"/"batal" replies on file-upload chat get blocked.
            pending_row_upload = await db_pool_guard.fetchrow(
                "SELECT id FROM pending_actions "
                "WHERE conversation_id = $1 AND status = 'PENDING' "
                "  AND expires_at > now() "
                "ORDER BY created_at DESC LIMIT 1",
                session_id,
            )
            pending_id_upload = (
                str(pending_row_upload["id"]) if pending_row_upload else None
            )

            if pending_id_upload:
                if text:
                    message_lower = text.lower().strip()
                    token_count = len(message_lower.split())
                    if token_count <= 3 and token_count > 0:
                        tokens = set(message_lower.split())
                        is_confirm = any(kw in tokens for kw in CONFIRM_KEYWORDS)
                        is_cancel = any(kw in tokens for kw in CANCEL_KEYWORDS)
                        try:
                            if is_confirm and not is_cancel:
                                logger.info(
                                    "[BucketA1] Upload NL CONFIRM routed: "
                                    "session=%s pending=%s text=%r",
                                    session_id[:8],
                                    pending_id_upload[:8],
                                    text[:40],
                                )
                                return await confirm_action(
                                    request,
                                    ConfirmActionRequest(
                                        conversation_id=conversation_id,
                                        session_id=session_id,
                                        pending_action_id=pending_id_upload,
                                    ),
                                )
                            if is_cancel and not is_confirm:
                                logger.info(
                                    "[BucketA1] Upload NL CANCEL routed: "
                                    "session=%s pending=%s text=%r",
                                    session_id[:8],
                                    pending_id_upload[:8],
                                    text[:40],
                                )
                                return await cancel_action(
                                    request,
                                    CancelActionRequest(
                                        conversation_id=conversation_id,
                                        session_id=session_id,
                                        pending_action_id=pending_id_upload,
                                    ),
                                )
                        except (
                            KeyError,
                            TypeError,
                            ValueError,
                            asyncpg.PostgresError,
                            json.JSONDecodeError,
                        ) as _disp_err:
                            logger.error(
                                "[BucketA1] Upload NL dispatch failed: %s",
                                _disp_err,
                                exc_info=True,
                            )

                # BucketA1.8 (2026-04-29): upload NL edit dispatch — parity with A1.7.
                # No token_count cap (edits are 3-6 tokens).
                if text and detect_edit_intent(text):
                    try:
                        logger.info(
                            "[BucketA1.8] Upload NL EDIT routed: session=%s pending=%s text=%r",
                            session_id[:8],
                            pending_id_upload[:8],
                            text[:60],
                        )
                        return await edit_action(
                            request,
                            EditActionRequest(
                                conversation_id=conversation_id,
                                session_id=session_id,
                                pending_action_id=pending_id_upload,
                                text=text,
                            ),
                        )
                    except (
                        KeyError,
                        TypeError,
                        ValueError,
                        asyncpg.PostgresError,
                        json.JSONDecodeError,
                    ) as _edit_err:
                        logger.error(
                            "[BucketA1.8] Upload NL EDIT dispatch failed: %s",
                            _edit_err,
                            exc_info=True,
                        )
                        # Fall through to guard TEXT below

                # Fall-through: pending exists but utterance is not a short
                # confirm/reject — preserve old "Ada aksi menunggu" UX.
                return ChatMessageResponse(
                    message_type="TEXT",
                    text="Ada aksi yang menunggu konfirmasi. Silakan konfirmasi atau batalkan dulu sebelum mengirim pesan baru.",
                    session_id=session_id,
                )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "[BucketA1] Upload pending-action dispatch failed (non-fatal)"
            )

    # ── Recon Upload Shortcut: bypass LLM when CSV+recon text detected ──
    # This prevents conversation history from confusing the LLM into thinking
    # recon is already done, and ensures the workflow always triggers.
    _recon_shortcut_result = None
    _has_bank_file = any(
        fm.get("extension", "").lower() in (".csv", ".xlsx", ".xls", ".ofx")
        for fm in file_metas
    )
    _text_lower = text.strip().lower()
    _is_recon = (
        "rekonsiliasi" in _text_lower
        or "rekon" in _text_lower
        or (
            "rekening koran" in _text_lower
            and ("import" in _text_lower or "upload" in _text_lower)
        )
    )
    if _has_bank_file and _is_recon:
        try:
            logger.info("[ReconShortcut] Detected recon upload — bypassing LLM")
            from ..services.unified_agent.workflow_engine import WorkflowEngine
            from ..services.unified_agent.db_utils import (
                get_session_db_pool as _get_wf_pool,
            )

            _wf_pool = await _get_wf_pool()

            # Ensure we have a session_id
            if not session_id:
                _sm_init = SessionManager(
                    db_pool=_wf_pool, tenant_id=ctx["tenant_id"], user_id=ctx["user_id"]
                )
                # conversation_id may not be a valid UUID — use it if valid, else generate one
                import uuid as _uuid

                try:
                    _conv_uuid = str(_uuid.UUID(conversation_id))
                except (ValueError, AttributeError):
                    _conv_uuid = str(_uuid.uuid4())
                    logger.info(
                        f"[ReconShortcut] conversation_id {conversation_id} is not a UUID, generated {_conv_uuid}"
                    )
                _sess = await _sm_init.get_or_create_session(_conv_uuid)
                session_id = (
                    str(_sess.session_id)
                    if hasattr(_sess, "session_id")
                    else str(_sess)
                )

            _wf_engine = WorkflowEngine(
                db_pool=_wf_pool,
                tenant_id=ctx["tenant_id"],
                user_id=ctx["user_id"],
                auth_token=ctx["auth_token"],
            )

            _tenant_ctx = TenantContext(
                tenant_id=ctx["tenant_id"],
                user_id=ctx["user_id"],
                auth_token=ctx["auth_token"],
                tenant_name=ctx.get("tenant_name", ctx["tenant_id"]),
            )
            _sm = SessionManager(
                db_pool=_wf_pool, tenant_id=ctx["tenant_id"], user_id=ctx["user_id"]
            )
            _te = ToolExecutor(
                context=_tenant_ctx,
                session_manager=_sm,
                session_id=session_id,
                user_text=enriched_text,
            )

            # Cancel any existing active workflow for this session
            try:
                _existing_wf = await _wf_engine.get_state(
                    session_id, "bank_reconciliation"
                )
                if _existing_wf and _existing_wf.status == "active":
                    await _wf_engine.cancel(session_id, "bank_reconciliation")
                    logger.info(
                        f"[ReconShortcut] Cancelled existing workflow for session {session_id}"
                    )
            except Exception:
                pass

            # Pre-identify bank account + balance from user text
            _shortcut_user_data = {}
            # Inject file_ref directly from file_metas (bypass auto-inject)
            if file_metas:
                _fm = file_metas[0]
                _shortcut_user_data[
                    "file_ref"
                ] = f"chat_upload:{_fm.get('file_hash', '')}{_fm['extension']}"
            from ..services.unified_agent.balance_parser import (
                parse_balance as _parse_bal,
            )

            # Parse balance using robust multi-strategy parser (Law 25: Decimal)
            _sc_balance_dec, _sc_bal_found = _parse_bal(text.strip())
            _sc_balance = None
            if _sc_bal_found and _sc_balance_dec is not None:
                _sc_balance = int(_sc_balance_dec)
                _shortcut_user_data["statement_ending_balance"] = _sc_balance
                logger.info(f"[ReconShortcut] Parsed balance: {_sc_balance}")

            # Extract candidate account numbers (8+ digit numbers, not the balance)
            import re as _sc_re

            _sc_nums = _sc_re.findall(r"\d[\d.,]+\d", text.strip())
            _sc_account_number = None
            for _n in _sc_nums:
                _clean = _n.replace(".", "").replace(",", "")
                if _clean.isdigit() and len(_clean) >= 8:
                    if _sc_balance and int(_clean) == _sc_balance:
                        continue  # Skip the balance number
                    _sc_account_number = _clean
                    break

            # Look up bank account by number
            if _sc_account_number:
                try:
                    import httpx as _sc_httpx

                    _sc_headers = {"Authorization": f"Bearer {ctx['auth_token']}"}
                    async with _sc_httpx.AsyncClient(timeout=10.0) as _sc_client:
                        _sc_resp = await _sc_client.get(
                            "http://localhost:8000/api/bank-accounts",
                            headers=_sc_headers,
                        )
                    if _sc_resp.status_code == 200:
                        _sc_banks = _sc_resp.json()
                        _sc_items = _sc_banks.get(
                            "data",
                            _sc_banks.get(
                                "items",
                                _sc_banks if isinstance(_sc_banks, list) else [],
                            ),
                        )
                        if isinstance(_sc_items, list):
                            for _bank in _sc_items:
                                _bank_num = (
                                    str(_bank.get("account_number", ""))
                                    .replace("-", "")
                                    .replace(" ", "")
                                )
                                if (
                                    _sc_account_number in _bank_num
                                    or _bank_num in _sc_account_number
                                ):
                                    _shortcut_user_data["account_id"] = _bank.get("id")
                                    _shortcut_user_data["account_name"] = _bank.get(
                                        "account_name", ""
                                    )
                                    _bank_label = _bank.get("account_name", "unknown")
                                    _bank_id = _bank.get("id", "?")
                                    logger.info(
                                        f"[ReconShortcut] Identified bank: {_bank_label} (id={_bank_id})"
                                    )
                                    break
                except Exception as _sc_err:
                    logger.warning(f"[ReconShortcut] Bank lookup failed: {_sc_err}")

            logger.info(
                f"[RECON-SHORTCUT] conv={session_id} "
                f"account={_shortcut_user_data.get('account_id', 'n/a')} "
                f"balance={_shortcut_user_data.get('statement_ending_balance', 'n/a')} "
                f"file={'yes' if _shortcut_user_data.get('file_ref') else 'no'}"
            )
            _wf_result = await _te._execute_start_workflow(
                {
                    "workflow_type": "bank_reconciliation",
                    "user_data": _shortcut_user_data,
                }
            )

            logger.info(
                f"[ReconShortcut] Result type={_wf_result.get('message_type') if isinstance(_wf_result, dict) else 'N/A'}"
            )

            if (
                isinstance(_wf_result, dict)
                and _wf_result.get("message_type") == "DIRECT_ACTION_PREVIEW"
            ):
                _da_data = _wf_result.get("data", {})
                _pa_id = _da_data.get("pending_action_id", "")
                # Attach uploaded_files metadata
                if isinstance(_da_data, dict):
                    _da_data["uploaded_files"] = [
                        {
                            "filename": fm["filename"],
                            "size": fm["size"],
                            "extension": fm["extension"],
                            "file_hash": fm["file_hash"],
                        }
                        for fm in file_metas
                    ]
                _recon_shortcut_result = ChatMessageResponse(
                    message_type="DIRECT_ACTION_PREVIEW",
                    text=_wf_result.get("content", ""),
                    session_id=session_id,
                    pending_action_id=_pa_id,
                    data=_da_data,
                )
            elif isinstance(_wf_result, dict) and _wf_result.get("llm_instruction"):
                # Workflow gate not ready — translate internal instruction to user-friendly text
                _wf_state = _wf_result.get("current_state", "")
                _user_facing = {
                    "IDENTIFY_ACCOUNT": "Mau rekonsiliasi rekening yang mana? Sebutkan nama atau nomor rekeningnya ya.",
                    "NEED_BALANCE": "Berapa saldo akhir di rekening koran? Angka ini perlu untuk mencocokkan data.",
                    "NEED_FILE": 'Upload file rekening koran (CSV/XLSX/OFX), atau ketik "tanpa file" untuk mode manual.',
                }.get(_wf_state, _wf_result.get("llm_instruction", ""))
                _recon_shortcut_result = ChatMessageResponse(
                    message_type="TEXT",
                    text=_user_facing,
                    session_id=session_id,
                )

        except Exception as _recon_err:
            logger.error(f"[ReconShortcut] Failed: {_recon_err}", exc_info=True)
            # Fall through to normal LLM path

    if _recon_shortcut_result:
        return _recon_shortcut_result

    # ── Document Pipeline: Single gpt-4o vision call for financial docs ──
    import time as _t_mod

    _t_pipeline_start = _t_mod.perf_counter()
    _doc_pipeline_result = None
    if file_metas and not _recon_shortcut_result:
        _has_image = any(
            fm.get("content_type", "").startswith("image/")
            or fm.get("extension", "").lower() == ".pdf"
            for fm in file_metas
        )
        # Skip OCR pipeline if user text has explicit action intent
        # (e.g. "daftarkan vendor", "buat vendor") — let LLM handle with vision
        _text_lower = text.lower().strip() if text else ""
        _has_explicit_intent = any(
            kw in _text_lower
            for kw in [
                "daftarkan vendor",
                "daftarkan customer",
                "daftarkan pelanggan",
                "buat vendor",
                "buat customer",
                "buat pelanggan",
                "tambah vendor",
                "tambah customer",
                "tambah pelanggan",
                "vendor baru",
                "customer baru",
                "pelanggan baru",
                "register vendor",
                "create vendor",
                "create customer",
                "daftarkan item",
                "daftarkan barang",
                "daftarkan jasa",
                "daftarkan produk",
                "buat item",
                "buat barang",
                "buat jasa",
                "buat produk",
                "tambah item",
                "tambah barang",
                "tambah jasa",
                "tambah produk",
                "item baru",
                "barang baru",
                "produk baru",
                "jasa baru",
            ]
        )

        # When user has explicit intent + image: extract text from image via
        # quick OCR and inject into enriched_text so the pipeline can use it
        if _has_image and _has_explicit_intent:
            try:
                _intent_img = next(
                    (
                        fm
                        for fm in file_metas
                        if fm.get("content_type", "").startswith("image/")
                    ),
                    None,
                )
                if _intent_img and _intent_img.get("stored_path"):
                    import base64 as _b64_intent
                    from openai import AsyncOpenAI as _IntentOCR

                    with open(_intent_img["stored_path"], "rb") as _f_intent:
                        _raw = _f_intent.read()
                    _b64_data = _b64_intent.b64encode(_raw).decode()
                    _mime = _intent_img.get("content_type", "image/jpeg")
                    _ocr_client = _IntentOCR()
                    _ocr_resp = await _ocr_client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "Baca semua teks yang terlihat di gambar ini. Keluarkan sebagai plain text, termasuk nama, alamat, telepon, nomor rekening, nama bank, nominal, tanggal, dan informasi lainnya. Output raw text saja, tanpa penjelasan.",
                                    },
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": f"data:{_mime};base64,{_b64_data}",
                                            "detail": "high",
                                        },
                                    },
                                ],
                            }
                        ],
                        max_tokens=500,
                    )
                    _extracted = _ocr_resp.choices[0].message.content.strip()
                    # Guard: model sometimes refuses with "Maaf..." instead of extracting text
                    _refusal_markers = (
                        "maaf",
                        "sorry",
                        "i cannot",
                        "i can't",
                        "tidak dapat",
                        "tidak bisa",
                    )
                    if _extracted and not any(
                        _extracted.lower().startswith(m) for m in _refusal_markers
                    ):
                        enriched_text = f"{text}\n\n[Data dari gambar]:\n{_extracted}"
                        logger.info(
                            f"[IntentOCR] Extracted {len(_extracted)} chars from image for intent: {_extracted[:100]}"
                        )
                        # ── Persist OCR text to session for multi-turn reference ──
                        try:
                            from ..services.unified_agent.session_manager import (
                                SessionManager as _OCR_SM,
                            )
                            from ..services.unified_agent.db_utils import (
                                get_session_db_pool as _ocr_sm_pool,
                            )

                            _ocr_sm_db = await _ocr_sm_pool()
                            _ocr_sm = _OCR_SM(
                                db_pool=_ocr_sm_db,
                                tenant_id=ctx["tenant_id"],
                                user_id=ctx["user_id"],
                            )
                            if session_id:
                                await _ocr_sm.update_state(
                                    session_id,
                                    document_context={
                                        "ocr_text": _extracted,
                                        "source": "intent_ocr",
                                    },
                                )
                                logger.info(
                                    "[IntentOCR] Persisted %d chars to document_context",
                                    len(_extracted),
                                )
                        except Exception as _ocr_persist_err:
                            logger.warning(
                                "[IntentOCR] Persist failed (non-blocking): %s",
                                _ocr_persist_err,
                            )
            except Exception as _iocr_err:
                logger.warning(f"[IntentOCR] Failed: {_iocr_err}")

        if _has_image and not _has_explicit_intent:
            try:
                from ..services.unified_agent.db_utils import (
                    get_session_db_pool as _get_ocr_pool,
                )
                import uuid as _ocr_uuid
                import json as _ocr_json
                from datetime import (
                    datetime as _ocr_dt,
                    timedelta as _ocr_td,
                    timezone as _ocr_tz,
                )
                from openai import AsyncOpenAI as _OCR_OpenAI

                _ocr_pool = await _get_ocr_pool()

                # Ensure session
                if not session_id:
                    _sm_ocr = SessionManager(
                        db_pool=_ocr_pool,
                        tenant_id=ctx["tenant_id"],
                        user_id=ctx["user_id"],
                    )
                    try:
                        _conv_uuid = str(_ocr_uuid.UUID(conversation_id))
                    except (ValueError, AttributeError):
                        _conv_uuid = str(_ocr_uuid.uuid4())
                    _ocr_sess = await _sm_ocr.get_or_create_session(_conv_uuid)
                    session_id = (
                        str(_ocr_sess.session_id)
                        if hasattr(_ocr_sess, "session_id")
                        else str(_ocr_sess)
                    )

                # Read first image file
                _fm = next(
                    (
                        fm
                        for fm in file_metas
                        if fm.get("content_type", "").startswith("image/")
                        or fm.get("extension", "").lower() == ".pdf"
                    ),
                    file_metas[0],
                )
                _stored_path = _fm.get("stored_path", "")
                if _stored_path and os.path.exists(_stored_path):
                    import base64 as _b64

                    with open(_stored_path, "rb") as _img_f:
                        _img_bytes = _img_f.read()
                    _mime = _fm.get("content_type", "image/jpeg")
                    # Resize + JPEG compress to slash OCR latency (vision tokens scale w/ size)
                    try:
                        from PIL import Image as _PILImage
                        import io as _io_resize

                        _img_pil = _PILImage.open(_io_resize.BytesIO(_img_bytes))
                        if _img_pil.mode in ("RGBA", "LA", "P"):
                            _img_pil = _img_pil.convert("RGB")
                        _max_dim = 768
                        if max(_img_pil.size) > _max_dim:
                            _img_pil.thumbnail((_max_dim, _max_dim), _PILImage.LANCZOS)
                        _buf = _io_resize.BytesIO()
                        _img_pil.save(_buf, format="JPEG", quality=75, optimize=True)
                        _img_bytes = _buf.getvalue()
                        _mime = "image/jpeg"
                        logger.info(
                            f"[DocSimple] Image resized to {_img_pil.size}, {len(_img_bytes)} bytes in {(_t_mod.perf_counter() - _t_pipeline_start)*1000:.0f}ms since start"
                        )
                    except Exception as _resize_err:
                        logger.warning(
                            f"[DocSimple] Image resize failed: {_resize_err}"
                        )
                    _img_b64 = _b64.b64encode(_img_bytes).decode()

                    # Single gpt-4o call: OCR + extract + understand user intent
                    _ocr_client = _OCR_OpenAI(
                        api_key=os.environ.get("OPENAI_API_KEY", "")
                    )
                    _ocr_prompt = f"""Ekstrak data dari dokumen finansial Indonesia ini. User caption: "{text}"

Return JSON ONLY:
{{
  "is_financial_document": true,
  "extracted_text": "teks utama yang terbaca dari gambar, apa adanya",
  "doc_type": "expense|bank_transfer|qris|merchant_payment|purchase_invoice|sales_invoice|receipt|unknown",
  "transfer_direction": "masuk|keluar|null",
  "vendor_name": "nama vendor (untuk struk PLN: 'PLN', untuk transfer keluar: nama penerima)",
  "customer_name": "nama customer atau null",
  "document_number": "no faktur/no ref",
  "document_date": "YYYY-MM-DD atau null",
  "total_amount": 0,
  "tax_amount": 0,
  "source_account_number": "nomor rek pengirim atau null",
  "destination_account_number": "nomor rek penerima atau null",
  "reference_note": "berita/keterangan",
  "confidence": 0.9
}}

Aturan:
- Struk PLN: doc_type="expense", total_amount=field "RP BAYAR" (BUKAN RP STROOM/TOKEN, BUKAN total dengan ADMIN BANK), tax_amount=PBJT-TL, vendor_name="PLN"
- QRIS/merchant payment ("Pembayaran QRIS Berhasil", Merchant PAN, Terminal ID): doc_type="qris", NOT bank_transfer. Ini pembayaran ke merchant, bukan transfer antar bank.
- transfer_direction: "masuk" jika uang MASUK ke rekening kita (kita yang menerima, nama penerima = nama bisnis/toko kita, atau caption bilang "dari pelanggan"/"pembayaran masuk"). "keluar" jika uang KELUAR dari rekening kita. null jika tidak bisa ditentukan.
- Bukti transfer: total_amount=Nominal Transfer (BUKAN Total Transaksi yang sudah +biaya admin)
- Semua angka Rupiah tanpa desimal
- is_financial_document: true jika gambar berisi dokumen keuangan (struk, invoice, bukti transfer, kwitansi, nota, faktur, slip gaji). false jika gambar berisi tabel data, screenshot aplikasi, foto produk, chat/pesan, atau konten non-keuangan.
- extracted_text: teks utama yang terbaca dari gambar, apa adanya. WAJIB diisi meskipun is_financial_document=false.
- Jika is_financial_document=false, isi semua field KECUALI extracted_text dan confidence dengan null/0/"unknown"."""

                    # Use Gemini 2.5 Flash for vision OCR (~700ms vs gpt-4o ~2-3s)
                    import httpx as _ocr_httpx

                    _gemini_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get(
                        "GEMINI_API_KEY", ""
                    )
                    _gemini_key = (
                        ""  # Disabled — Gemini Flash too slow + 503 in container
                    )
                    _ocr_text = "{}"
                    _ocr_used_gemini = False
                    if _gemini_key:
                        try:
                            _gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={_gemini_key}"
                            _gemini_payload = {
                                "contents": [
                                    {
                                        "parts": [
                                            {
                                                "text": _ocr_prompt
                                                + "\n\nReturn ONLY valid JSON, no markdown."
                                            },
                                            {
                                                "inline_data": {
                                                    "mime_type": _mime,
                                                    "data": _img_b64,
                                                }
                                            },
                                        ]
                                    }
                                ],
                                "generationConfig": {
                                    "temperature": 0.1,
                                    "maxOutputTokens": 2000,
                                    "responseMimeType": "application/json",
                                },
                            }
                            async with _ocr_httpx.AsyncClient(timeout=4.0) as _gc:
                                _gr = await _gc.post(_gemini_url, json=_gemini_payload)
                                if _gr.status_code == 200:
                                    _gj = _gr.json()
                                    _ocr_text = _gj["candidates"][0]["content"][
                                        "parts"
                                    ][0]["text"]
                                    _ocr_used_gemini = True
                                    logger.info("[DocSimple] OCR via Gemini 2.5 Flash")
                                else:
                                    logger.warning(
                                        "[DocSimple] Gemini OCR failed status=%s, falling back to gpt-4o",
                                        _gr.status_code,
                                    )
                        except Exception as _ge:
                            logger.warning(
                                "[DocSimple] Gemini OCR exception: %s, falling back",
                                _ge,
                            )

                    if not _ocr_used_gemini:
                        import time as _t_mod

                        _t_start = _t_mod.perf_counter()
                        _ocr_response = await _ocr_client.chat.completions.create(
                            model="gpt-4o-mini",
                            messages=[
                                {
                                    "role": "user",
                                    "content": [
                                        {"type": "text", "text": _ocr_prompt},
                                        {
                                            "type": "image_url",
                                            "image_url": {
                                                "url": f"data:{_mime};base64,{_img_b64}",
                                                "detail": "low",
                                            },
                                        },
                                    ],
                                }
                            ],
                            max_tokens=1000,
                            temperature=0.1,
                        )
                        _ocr_text = _ocr_response.choices[0].message.content or "{}"
                    # Strip markdown code blocks if present
                    if _ocr_text.startswith("```"):
                        _ocr_text = _ocr_text.split("\n", 1)[-1].rsplit("```", 1)[0]
                    _ocr_data = _ocr_json.loads(_ocr_text)
                    _ocr_elapsed = _t_mod.perf_counter() - _t_start
                    logger.info(
                        f"[DocSimple] gpt-4o-mini extracted in {_ocr_elapsed*1000:.0f}ms: type={_ocr_data.get('doc_type')} vendor={_ocr_data.get('vendor_name')} total={_ocr_data.get('total_amount')}"
                    )

                    # -- Smart Document Matching (bridge to Financial Intelligence) --
                    _match_result = None
                    try:
                        # Extract bank hint from caption text (e.g. "transfer dari BCA utama")
                        _caption_lower = text.strip().lower() if text else ""
                        _bank_hint = ""
                        import re as _re_bank

                        _bank_patterns = [
                            r"(?:dari|via|lewat|pakai|pake|rekening|rek)\s+(.+?)(?:\s*$|[,.])",
                            r"(?:transfer|bayar|tf)\s+(?:dari|via|lewat|pakai)\s+(.+?)(?:\s*$|[,.])",
                        ]
                        for _bp in _bank_patterns:
                            _bm_cap = _re_bank.search(_bp, _caption_lower)
                            if _bm_cap:
                                _bank_hint = _bm_cap.group(1).strip()
                                break
                        if _bank_hint:
                            _ocr_data["bank_hint"] = _bank_hint
                            logger.info(
                                "[DocSimple] Bank hint from caption: %s", _bank_hint
                            )

                        # Inject user caption so matcher can use it for keyword-based account recommendation
                        if text:
                            _ocr_data["user_caption"] = text
                            _existing_raw = _ocr_data.get("raw_text") or ""
                            _ocr_data["raw_text"] = f"{_existing_raw} {text}".strip()
                        # ── Vision Gate: non-financial image escape hatch ──
                        _is_financial = _ocr_data.get("is_financial_document", True)
                        _ocr_confidence_val = float(_ocr_data.get("confidence", 0) or 0)
                        _ocr_doc_type_gate = (_ocr_data.get("doc_type") or "").lower()

                        _has_caption_hint = False
                        if text:
                            _caption_lower_gate = text.lower()
                            _financial_kws = (
                                "beban",
                                "biaya",
                                "pengeluaran",
                                "expense",
                                "servis",
                                "listrik",
                                "bayar",
                                "beli",
                                "catat",
                                "struk",
                                "kwitansi",
                                "nota",
                                "invoice",
                                "faktur",
                                "transfer",
                                "parkir",
                                "makan",
                                "bensin",
                                "sewa",
                            )
                            _has_caption_hint = any(
                                kw in _caption_lower_gate for kw in _financial_kws
                            )

                        if not _has_caption_hint and (
                            not _is_financial
                            or (
                                _ocr_doc_type_gate in ("unknown", "", "null")
                                and _ocr_confidence_val < 0.4
                            )
                        ):
                            _extracted_text = (
                                _ocr_data.get("extracted_text")
                                or _ocr_data.get("raw_text")
                                or _ocr_data.get("reference_note")
                                or ""
                            )
                            if _extracted_text:
                                text = (
                                    f"{text}\n\n[Konteks dari gambar: {_extracted_text}]"
                                    if text
                                    else f"[Konteks dari gambar: {_extracted_text}]"
                                )
                            logger.info(
                                "[VisionGate] Non-financial image detected "
                                "(is_financial=%s, doc_type=%s, confidence=%.2f, caption_hint=%s). "
                                "Falling through to chat pipeline.",
                                _is_financial,
                                _ocr_doc_type_gate,
                                _ocr_confidence_val,
                                _has_caption_hint,
                            )
                            raise _VisionGateSkip()

                        # -- Document Intake Pipeline (V2 default, V3 opt-in) --
                        import os as _v3_os

                        _intake_version = _v3_os.environ.get("DOC_INTAKE_VERSION", "v2")
                        if _intake_version == "v3":
                            from ..services.unified_agent.document_intake_v3.pipeline import (
                                DocumentIntakePipelineV3 as _IntakePipeline,
                            )
                        else:
                            from ..services.unified_agent.document_intake import (
                                DocumentIntakePipeline as _IntakePipeline,
                            )

                        _intake = _IntakePipeline(_ocr_pool, ctx["tenant_id"])
                        _t_match_start = _t_mod.perf_counter()
                        _intake_result = await _intake.process(
                            _ocr_data, caption=text or ""
                        )
                        _intake_elapsed = (
                            _t_mod.perf_counter() - _t_match_start
                        ) * 1000

                        logger.info(
                            "[DocIntakeV2] category=%s direction=%s(%s) match=%s bank=%s elapsed=%.0fms trail=%s",
                            _intake_result.doc_category,
                            _intake_result.direction,
                            _intake_result.direction_source,
                            _intake_result.best_match.label
                            if _intake_result.best_match
                            else "none",
                            "resolved"
                            if _intake_result.bank_id
                            else "needs_clarification",
                            _intake_elapsed,
                            " | ".join(_intake_result.log_trail),
                        )
                    except _VisionGateSkip:
                        raise  # Re-raise — must reach outer handler
                    except Exception as _match_err:
                        # V3 skip exceptions — only raised when flag=v3
                        if _intake_version == "v3":
                            from ..services.unified_agent.document_intake_v3.pipeline import (
                                _FallbackToGenericChatSkip as _V3Generic,
                                _FallbackToV2PreviewSkip as _V3V2Fallback,
                            )

                            if isinstance(_match_err, _V3Generic):
                                # V3 says "not a financial doc" — fall through to chat
                                logger.info(
                                    "[DocIntakeV3] generic-chat skip: %s",
                                    _match_err,
                                )
                                raise _VisionGateSkip() from _match_err
                            if isinstance(_match_err, _V3V2Fallback):
                                # V3 couldn't produce action — re-run via V2 safety net
                                logger.info(
                                    "[DocIntakeV3] fallback to V2: %s",
                                    _match_err,
                                )
                                from ..services.unified_agent.document_intake import (
                                    DocumentIntakePipeline as _V2Pipeline,
                                )

                                _intake = _V2Pipeline(_ocr_pool, ctx["tenant_id"])
                                _t_match_start = _t_mod.perf_counter()
                                _intake_result = await _intake.process(
                                    _ocr_data, caption=text or ""
                                )
                                _intake_elapsed = (
                                    _t_mod.perf_counter() - _t_match_start
                                ) * 1000
                                logger.info(
                                    "[DocIntakeV2-fallback] category=%s direction=%s(%s) match=%s bank=%s elapsed=%.0fms trail=%s",
                                    _intake_result.doc_category,
                                    _intake_result.direction,
                                    _intake_result.direction_source,
                                    _intake_result.best_match.label
                                    if _intake_result.best_match
                                    else "none",
                                    "resolved"
                                    if _intake_result.bank_id
                                    else "needs_clarification",
                                    _intake_elapsed,
                                    " | ".join(_intake_result.log_trail),
                                )
                                # Fall through to downstream V1-compat code below
                                _match_err = None  # clear — recovered
                                # continue with normal flow (no re-raise)
                                # Note: we can't `continue` a try-block; just pass
                                pass
                            else:
                                logger.warning(
                                    "[DocIntakeV3] Pipeline failed (non-blocking): %s",
                                    _match_err,
                                )
                        else:
                            logger.warning(
                                "[DocIntakeV2] Pipeline failed (non-blocking): %s",
                                _match_err,
                            )

                    # Build V1-compat variables for downstream code
                    _match_result = None  # V1 compat
                    _resolved_action = (
                        getattr(_intake_result, "resolved_action", None)
                        if _intake_result
                        else None
                    )

                    if _intake_result is not None:
                        # If direction clarification needed -> CLARIFICATION pills
                        if _intake_result.needs_direction_clarification:
                            _cl_options = [
                                {
                                    "label": "Pembayaran Masuk (dari pelanggan)",
                                    "value": "direction:in",
                                    "description": "",
                                },
                                {
                                    "label": "Pembayaran Keluar (ke vendor)",
                                    "value": "direction:out",
                                    "description": "",
                                },
                            ]
                            # Persist OCR data for direction re-trigger
                            try:
                                import json as _ctx_json

                                _doc_ctx_cl = {
                                    "pending_bank_selection": True,
                                    "resolved_action_key": "",
                                    "resolved_payload": {},
                                    "saved_ocr_data": {
                                        k: v
                                        for k, v in _ocr_data.items()
                                        if k != "image_bytes"
                                    },
                                }
                                _doc_json = _ctx_json.dumps(_doc_ctx_cl, default=str)
                                _sid_uuid = __import__("uuid").UUID(session_id)
                                _tid = ctx["tenant_id"]
                                async with _ocr_pool.acquire() as _rls_conn:
                                    await _rls_conn.execute(
                                        "SELECT set_config('app.tenant_id', $1, true)",
                                        _tid,
                                    )
                                    await _rls_conn.execute(
                                        "INSERT INTO chat_session_state (session_id, tenant_id, document_context) "
                                        "VALUES ($1::uuid, $2, $3::jsonb) "
                                        "ON CONFLICT (session_id) DO UPDATE SET document_context = EXCLUDED.document_context, updated_at = now()",
                                        _sid_uuid,
                                        _tid,
                                        _doc_json,
                                    )
                            except Exception as _store_err:
                                logger.error(
                                    "[DocIntakeV2] Failed to store direction state: %s",
                                    _store_err,
                                )

                            _doc_pipeline_result = ChatMessageResponse(
                                message_type="CLARIFICATION",
                                text="Ini pembayaran masuk (dari pelanggan) atau keluar (ke vendor)?",
                                data={
                                    "question": "Ini pembayaran masuk (dari pelanggan) atau keluar (ke vendor)?",
                                    "options": _cl_options,
                                    "allow_freetext": False,
                                },
                                session_id=session_id,
                            )

                        # If bank clarification needed
                        elif (
                            _intake_result.needs_bank_clarification
                            and _intake_result.bank_candidates
                        ):
                            _cl_options = [
                                {
                                    "label": c.get("label", c.get("display_name", "")),
                                    "value": c.get("id", c.get("value", "")),
                                    "description": "",
                                }
                                for c in _intake_result.bank_candidates
                            ]
                            _q = (
                                "Diterima di rekening mana?"
                                if _intake_result.direction == "in"
                                else "Dibayar dari rekening mana?"
                            )
                            # Persist state for bank re-trigger
                            try:
                                import json as _ctx_json

                                _doc_ctx_cl = {
                                    "pending_bank_selection": True,
                                    "resolved_action_key": _resolved_action.action_key
                                    if _resolved_action
                                    else "",
                                    "resolved_payload": (
                                        {
                                            **_resolved_action.payload,
                                            "_uploaded_document_id": _fm["document_id"],
                                        }
                                        if _resolved_action
                                        and _fm
                                        and _fm.get("document_id")
                                        else (
                                            _resolved_action.payload
                                            if _resolved_action
                                            else {}
                                        )
                                    ),
                                    "doc_type": _ocr_data.get("doc_type", "unknown"),
                                    "vendor_name": _ocr_data.get("vendor_name")
                                    or _ocr_data.get("customer_name")
                                    or "Unknown",
                                    "total_amount": float(
                                        _ocr_data.get("total_amount", 0)
                                    ),
                                    "match_label": _intake_result.best_match.label
                                    if _intake_result.best_match
                                    else None,
                                    "saved_ocr_data": {
                                        k: v
                                        for k, v in _ocr_data.items()
                                        if k != "image_bytes"
                                    },
                                }
                                _doc_json = _ctx_json.dumps(_doc_ctx_cl, default=str)
                                _sid_uuid = __import__("uuid").UUID(session_id)
                                _tid = ctx["tenant_id"]
                                async with _ocr_pool.acquire() as _rls_conn:
                                    await _rls_conn.execute(
                                        "SELECT set_config('app.tenant_id', $1, true)",
                                        _tid,
                                    )
                                    await _rls_conn.execute(
                                        "INSERT INTO chat_session_state (session_id, tenant_id, document_context) "
                                        "VALUES ($1::uuid, $2, $3::jsonb) "
                                        "ON CONFLICT (session_id) DO UPDATE SET document_context = EXCLUDED.document_context, updated_at = now()",
                                        _sid_uuid,
                                        _tid,
                                        _doc_json,
                                    )
                            except Exception as _store_err:
                                logger.error(
                                    "[DocIntakeV2] Failed to store bank state: %s",
                                    _store_err,
                                )

                            _counterparty_cl = (
                                _intake_result.best_match.counterparty
                                if _intake_result.best_match
                                else (
                                    _ocr_data.get("vendor_name")
                                    or _ocr_data.get("customer_name")
                                    or "Unknown"
                                )
                            )
                            _total_cl = float(_ocr_data.get("total_amount", 0))
                            _total_cl_fmt = f"Rp {_total_cl:,.0f}".replace(",", ".")
                            if (
                                _intake_result.direction == "in"
                                and _intake_result.best_match
                            ):
                                _cl_narration = (
                                    f"Pembayaran masuk dari **{_counterparty_cl}** "
                                    f"sebesar {_total_cl_fmt}. "
                                    f"Cocok dengan **{_intake_result.best_match.label}**."
                                )
                            elif (
                                _intake_result.direction == "out"
                                and _intake_result.best_match
                            ):
                                _cl_narration = (
                                    f"Pembayaran keluar ke **{_counterparty_cl}** "
                                    f"sebesar {_total_cl_fmt}. "
                                    f"Cocok dengan **{_intake_result.best_match.label}**."
                                )
                            else:
                                _dir_word_cl = (
                                    "ke"
                                    if _intake_result.direction == "out"
                                    else "dari"
                                )
                                _cl_narration = f"Dokumen {_dir_word_cl} **{_counterparty_cl}**. Total {_total_cl_fmt}."

                            _doc_pipeline_result = ChatMessageResponse(
                                message_type="CLARIFICATION",
                                text=_cl_narration,
                                data={
                                    "question": _q,
                                    "options": _cl_options,
                                    "allow_freetext": False,
                                },
                                session_id=session_id,
                            )

                        # If resolved with no clarification -> propose_direct
                        elif (
                            _resolved_action
                            and not _resolved_action.needs_clarification
                        ):
                            try:
                                _te = ToolExecutor(
                                    context=TenantContext(
                                        tenant_id=ctx["tenant_id"],
                                        user_id=ctx["user_id"],
                                        auth_token=ctx["auth_token"],
                                        tenant_name=ctx.get(
                                            "tenant_name", ctx["tenant_id"]
                                        ),
                                    ),
                                    session_id=session_id,
                                )
                                if _fm and _fm.get("document_id"):
                                    _resolved_action.payload[
                                        "_uploaded_document_id"
                                    ] = _fm["document_id"]
                                _propose_result = await _te._execute_propose_direct(
                                    {
                                        "action_key": _resolved_action.action_key,
                                        "payload": _resolved_action.payload,
                                    }
                                )
                                if _propose_result.get("success"):
                                    _doc_type = _ocr_data.get("doc_type", "unknown")
                                    _type_labels = {
                                        "purchase_invoice": "Faktur Pembelian",
                                        "sales_invoice": "Faktur Penjualan",
                                        "receipt": "Kwitansi",
                                        "bank_transfer": "Transfer Bank",
                                        "expense": "Biaya/Pengeluaran",
                                        "qris": "Pembayaran QRIS",
                                        "merchant_payment": "Pembayaran Merchant",
                                        "e_wallet": "Pembayaran E-Wallet",
                                    }
                                    _type_display = _type_labels.get(
                                        _doc_type, _doc_type
                                    )
                                    # Use match counterparty (customer/vendor) if available,
                                    # fallback to OCR-extracted name
                                    _counterparty = (
                                        _intake_result.best_match.counterparty
                                        if _intake_result.best_match
                                        else (
                                            _ocr_data.get("vendor_name")
                                            or _ocr_data.get("customer_name")
                                            or "Unknown"
                                        )
                                    )
                                    _total = float(_ocr_data.get("total_amount", 0))
                                    _total_fmt = f"Rp {_total:,.0f}".replace(",", ".")
                                    # Build narration based on direction + match
                                    if (
                                        _intake_result.direction == "in"
                                        and _intake_result.best_match
                                    ):
                                        _narration = (
                                            f"Pembayaran masuk dari **{_counterparty}** "
                                            f"sebesar {_total_fmt}. "
                                            f"Cocok dengan **{_intake_result.best_match.label}**."
                                        )
                                    elif (
                                        _intake_result.direction == "out"
                                        and _intake_result.best_match
                                    ):
                                        _narration = (
                                            f"Pembayaran keluar ke **{_counterparty}** "
                                            f"sebesar {_total_fmt}. "
                                            f"Cocok dengan **{_intake_result.best_match.label}**."
                                        )
                                    else:
                                        _dir_word = (
                                            "ke"
                                            if _intake_result.direction == "out"
                                            else "dari"
                                        )
                                        _narration = f"Saya baca {_type_display.lower()} {_dir_word} **{_counterparty}**. Total {_total_fmt}."
                                    # Append bank info if auto-resolved
                                    if _intake_result.bank_display_name:
                                        _narration += f" Rekening: **{_intake_result.bank_display_name}**."

                                    _doc_pipeline_result = ChatMessageResponse(
                                        message_type="DIRECT_ACTION_PREVIEW",
                                        text=_narration,
                                        data=_propose_result["data"],
                                        pending_action_id=_propose_result["data"].get(
                                            "pending_action_id"
                                        ),
                                        session_id=session_id,
                                    )

                                    # Persist document_context to Layer 2
                                    try:
                                        _sm_persist = SessionManager(
                                            db_pool=_ocr_pool,
                                            tenant_id=ctx["tenant_id"],
                                            user_id=ctx["user_id"],
                                        )
                                        _doc_ctx = {
                                            "document_id": str(_ocr_uuid.uuid4()),
                                            "doc_type": _doc_type,
                                            "vendor_name": _counterparty,
                                            "total_amount": _total,
                                            "resolved_action": _resolved_action.action_key,
                                            "pending_action_id": _propose_result[
                                                "data"
                                            ].get("pending_action_id"),
                                        }
                                        await _sm_persist.update_state(
                                            session_id, document_context=_doc_ctx
                                        )
                                    except Exception:
                                        pass
                            except Exception as _propose_err:
                                logger.warning(
                                    "[DocIntakeV2] Propose failed: %s", _propose_err
                                )
                                _resolved_action = None

                        # If resolved but needs clarification from resolver itself (bank options etc)
                        elif _resolved_action and _resolved_action.needs_clarification:
                            _cl_options = [
                                {
                                    "label": opt.label,
                                    "value": opt.value,
                                    "description": "",
                                }
                                for opt in _resolved_action.clarification_options
                            ]
                            try:
                                import json as _ctx_json

                                _doc_ctx_cl = {
                                    "pending_bank_selection": True,
                                    "resolved_action_key": _resolved_action.action_key,
                                    "resolved_payload": (
                                        {
                                            **_resolved_action.payload,
                                            "_uploaded_document_id": _fm["document_id"],
                                        }
                                        if _fm and _fm.get("document_id")
                                        else _resolved_action.payload
                                    ),
                                    "doc_type": _ocr_data.get("doc_type", "unknown"),
                                    "vendor_name": _ocr_data.get("vendor_name")
                                    or _ocr_data.get("customer_name")
                                    or "Unknown",
                                    "total_amount": float(
                                        _ocr_data.get("total_amount", 0)
                                    ),
                                    "match_label": _intake_result.best_match.label
                                    if _intake_result.best_match
                                    else None,
                                }
                                _doc_json = _ctx_json.dumps(_doc_ctx_cl, default=str)
                                _sid_uuid = __import__("uuid").UUID(session_id)
                                _tid = ctx["tenant_id"]
                                async with _ocr_pool.acquire() as _rls_conn:
                                    await _rls_conn.execute(
                                        "SELECT set_config('app.tenant_id', $1, true)",
                                        _tid,
                                    )
                                    await _rls_conn.execute(
                                        "INSERT INTO chat_session_state (session_id, tenant_id, document_context) "
                                        "VALUES ($1::uuid, $2, $3::jsonb) "
                                        "ON CONFLICT (session_id) DO UPDATE SET document_context = EXCLUDED.document_context, updated_at = now()",
                                        _sid_uuid,
                                        _tid,
                                        _doc_json,
                                    )
                            except Exception as _store_err:
                                logger.error(
                                    "[DocIntakeV2] Failed to store resolver clarification: %s",
                                    _store_err,
                                )

                            _doc_pipeline_result = ChatMessageResponse(
                                message_type="CLARIFICATION",
                                text=_resolved_action.clarification_question
                                or "Perlu informasi tambahan.",
                                data={
                                    "question": _resolved_action.clarification_question,
                                    "options": _cl_options,
                                    "allow_freetext": False,
                                },
                                session_id=session_id,
                            )

                    # ── Old inline preview path (fallback) ──
                    # Skip if resolver already produced a result
                    if _doc_pipeline_result:
                        pass  # resolver handled it, skip to return
                    else:
                        _pending_id = str(_ocr_uuid.uuid4())
                        _doc_type = _ocr_data.get("doc_type", "unknown")
                        _vendor = (
                            _ocr_data.get("vendor_name")
                            or _ocr_data.get("customer_name")
                            or "Unknown"
                        )
                        _total = float(_ocr_data.get("total_amount", 0))
                        _tax = float(_ocr_data.get("tax_amount", 0))
                        _items = _ocr_data.get("items", [])
                        _doc_number = _ocr_data.get("document_number", "-")
                        _doc_date = _ocr_data.get("document_date", "-")
                        _confidence = float(_ocr_data.get("confidence", 0))

                        _type_labels = {
                            "purchase_invoice": "Faktur Pembelian",
                            "sales_invoice": "Faktur Penjualan",
                            "receipt": "Kwitansi",
                            "bank_transfer": "Transfer Bank",
                            "expense": "Biaya/Pengeluaran",
                            "qris": "Pembayaran QRIS",
                            "merchant_payment": "Pembayaran Merchant",
                            "e_wallet": "Pembayaran E-Wallet",
                        }
                        _type_display = _type_labels.get(_doc_type, _doc_type)

                        # Build confirmation table
                        _table_lines = ["| Field | Value |", "|---|---|"]
                        _table_lines.append(f"| Tipe | {_type_display} |")
                        _table_lines.append(f"| Vendor | {_vendor} |")
                        if _doc_number != "-":
                            _table_lines.append(f"| No. Dokumen | {_doc_number} |")
                        if _doc_date != "-":
                            _table_lines.append(f"| Tanggal | {_doc_date} |")
                        if _items:
                            _table_lines.append(f"| Items | {len(_items)} item |")
                        _table_lines.append(
                            f"| Total | Rp {_total:,.0f} |".replace(",", ".")
                        )
                        if _tax > 0:
                            _table_lines.append(
                                f"| Pajak | Rp {_tax:,.0f} |".replace(",", ".")
                            )
                        _table_lines.append(f"| Confidence | {_confidence*100:.0f}% |")

                        _preview = {
                            "pending_action_id": _pending_id,
                            "action_type": "CONFIRM_DOCUMENT_DRAFT",
                            "action_key": "confirm_document_draft",
                            "display_name": f"Konfirmasi {_type_display}",
                            "confirmation_table": "\n".join(_table_lines),
                            "payload": {
                                "document_id": _fm.get(
                                    "file_hash", str(_ocr_uuid.uuid4())
                                ),
                                "doc_type": _doc_type,
                                "vendor_name": _counterparty,
                                "document_number": _doc_number,
                                "document_date": _doc_date,
                                "total_amount": _total,
                                "tax_amount": _tax,
                                "items": _items,
                            },
                            "loading_message": f"Memproses {_type_display.lower()} dari {_vendor}...",
                            "entity_type": "document",
                        }

                        # Build review_card for InlineReviewCard rendering
                        _rc_header = [
                            {"label": "Tipe", "value": _type_display},
                        ]
                        if _vendor != "Unknown":
                            _rc_header.append(
                                {"label": "Vendor/Pihak", "value": _vendor}
                            )
                        if _doc_number != "-":
                            _rc_header.append(
                                {"label": "No. Dokumen", "value": _doc_number}
                            )
                        if _doc_date != "-":
                            _rc_header.append({"label": "Tanggal", "value": _doc_date})
                        _rc_header.append(
                            {
                                "label": "Total",
                                "value": f"Rp {_total:,.0f}".replace(",", "."),
                                "field_type": "number",
                            }
                        )
                        if _tax > 0:
                            _rc_header.append(
                                {
                                    "label": "Pajak",
                                    "value": f"Rp {_tax:,.0f}".replace(",", "."),
                                }
                            )
                        # OCR confidence only shown if low
                        if _confidence < 0.8:
                            _rc_header.append(
                                {
                                    "label": "Confidence OCR",
                                    "value": f"{_confidence*100:.0f}%",
                                }
                            )

                        _rc_warnings = []

                        _rc = {
                            "render_target": "inline",
                            "title": f"Konfirmasi {_type_display}",
                            "subtitle": _vendor if _vendor != "Unknown" else None,
                            "header": _rc_header,
                            "warnings": _rc_warnings,
                            "version": 1,
                        }

                        # Add smart match info to review card
                        if _match_result and _match_result.best_match:
                            _bm = _match_result.best_match
                            _conf_pct = f"{_bm.confidence*100:.0f}%"
                            _match_label = (
                                "high"
                                if _bm.confidence >= 0.85
                                else "medium"
                                if _bm.confidence >= 0.60
                                else "low"
                            )
                            _dir_label = (
                                "Uang Masuk"
                                if _match_result.direction == "in"
                                else "Uang Keluar"
                            )
                            _rc["category_label"] = _dir_label
                            _rc_header.append(
                                {
                                    "label": "Match",
                                    "value": f"{_bm.label} ({_conf_pct} yakin)",
                                }
                            )
                            # Skip counterparty if same as vendor
                            if (
                                _bm.counterparty
                                and _bm.counterparty.lower() != _vendor.lower()
                            ):
                                _rc_header.append(
                                    {"label": "Counterparty", "value": _bm.counterparty}
                                )
                            _rc_header.append(
                                {
                                    "label": "Sisa Tagihan",
                                    "value": f"Rp {float(_bm.outstanding):,.0f}".replace(
                                        ",", "."
                                    ),
                                    "field_type": "number",
                                }
                            )
                            if _bm.due_date:
                                _rc_header.append(
                                    {"label": "Jatuh Tempo", "value": str(_bm.due_date)}
                                )
                            if _bm.reasons:
                                _rc_warnings.append(
                                    {"type": "info", "message": " · ".join(_bm.reasons)}
                                )
                        elif _match_result:
                            _dir_label = (
                                "Uang Masuk"
                                if _match_result.direction == "in"
                                else "Uang Keluar"
                            )
                            _rc["category_label"] = _dir_label
                            _rc_warnings.append(
                                {
                                    "type": "warning",
                                    "message": "Tidak ditemukan match di database. Transaksi ini mungkin belum tercatat.",
                                }
                            )

                        if _match_result and _match_result.account_recommendation:
                            _ar = _match_result.account_recommendation
                            _rc_header.append(
                                {
                                    "label": "Akun (CoA)",
                                    "value": f"{_ar.account_name} ({_ar.account_code})",
                                }
                            )

                        _preview["review_card"] = _rc

                        # Inject smart match info into preview
                        if _match_result:
                            _sm_info = {
                                "doc_category": _match_result.doc_category,
                                "direction": _match_result.direction,
                                "direction_label": "Uang Masuk"
                                if _match_result.direction == "inbound"
                                else "Uang Keluar",
                                "direction_confidence": _match_result.direction_confidence,
                                "confidence_level": _match_result.confidence_level,
                                "needs_user_input": _match_result.needs_user_input
                                or [],
                            }
                            if _match_result.best_match:
                                bm = _match_result.best_match
                                _sm_info["best_match"] = {
                                    "source_type": bm.source_type,
                                    "source_id": bm.source_id,
                                    "label": bm.label,
                                    "counterparty": bm.counterparty,
                                    "amount": bm.amount,
                                    "outstanding": bm.outstanding,
                                    "due_date": bm.due_date,
                                    "confidence": bm.confidence,
                                    "reasons": bm.reasons,
                                }
                            if _match_result.alternatives:
                                _sm_info["alternatives"] = [
                                    {
                                        "label": a.label,
                                        "counterparty": a.counterparty,
                                        "amount": a.amount,
                                        "outstanding": a.outstanding,
                                        "confidence": a.confidence,
                                    }
                                    for a in _match_result.alternatives
                                ]
                            if _match_result.account_recommendation:
                                ar = _match_result.account_recommendation
                                _sm_info["account_recommendation"] = {
                                    "account_id": ar.account_id,
                                    "account_name": ar.account_name,
                                    "account_code": ar.account_code,
                                    "confidence": ar.confidence,
                                }
                            _preview["payload"]["smart_match"] = _sm_info

                        # Store pending action
                        _expires = _ocr_dt.now(_ocr_tz.utc) + _ocr_td(seconds=300)
                        try:
                            await _ocr_pool.execute(
                                """INSERT INTO pending_actions (id, tenant_id, user_id, action_id, action_type, action_category, action_plan, status, expires_at)
                                   VALUES ($1, $2, $3, $4, $5, $6, $7, 'PENDING', $8)""",
                                _ocr_uuid.UUID(_pending_id),
                                ctx["tenant_id"],
                                ctx["user_id"],
                                "confirm_document_draft",
                                "CONFIRM_DOCUMENT_DRAFT",
                                "DOCUMENT",
                                _ocr_json.dumps(_preview["payload"], default=str),
                                _expires,
                            )
                        except Exception as _pa_err:
                            logger.warning(
                                f"[DocSimple] Failed to store pending: {_pa_err}"
                            )

                        # Persist to Layer 2 document_context
                        try:
                            _sm_persist = SessionManager(
                                db_pool=_ocr_pool,
                                tenant_id=ctx["tenant_id"],
                                user_id=ctx["user_id"],
                            )
                            _doc_ctx = {
                                "document_id": _preview["payload"]["document_id"],
                                "doc_type": _doc_type,
                                "confidence": _confidence,
                                "document_number": _doc_number,
                                "document_date": _doc_date,
                                "vendor_name": _counterparty,
                                "total_amount": _total,
                                "tax_amount": _tax,
                                "items": _items,
                                "pending_action_id": _pending_id,
                                "edits": {},
                            }
                            await _sm_persist.update_state(
                                session_id, document_context=_doc_ctx
                            )
                            logger.info(
                                "[DocSimple] Persisted document_context to Layer 2"
                            )
                        except (asyncpg.PostgresError, json.JSONDecodeError) as _l2_err:
                            logger.error(
                                "[DocSimple] Failed to persist Layer 2: session=%s",
                                session_id,
                                exc_info=True,
                            )

                        _dir_word = (
                            "ke"
                            if _match_result and _match_result.direction == "out"
                            else "dari"
                        )
                        _narration = f"Saya baca {_type_display.lower()} {_dir_word} **{_vendor}**. Total Rp {_total:,.0f}.".replace(
                            ",", "."
                        )
                        # Simplified narration — details are in the card
                        if _match_result and _match_result.best_match:
                            _bm = _match_result.best_match
                            _narration += f" Cocok dengan **{_bm.label}**."

                        # Append clarification question if resolver needs user input
                        if _resolved_action and _resolved_action.needs_clarification:
                            _narration += (
                                f"\n\n{_resolved_action.clarification_question}"
                            )

                        _doc_pipeline_result = ChatMessageResponse(
                            message_type="DIRECT_ACTION_PREVIEW",
                            text=_narration,
                            data=_preview,
                            pending_action_id=_pending_id,
                            session_id=session_id,
                        )

            except _VisionGateSkip:
                pass  # Non-financial image — fall through to normal LLM path
            except Exception as _doc_err:
                logger.error(f"[DocSimple] Failed: {_doc_err}", exc_info=True)
                # Fall through to normal LLM path

    if _doc_pipeline_result:
        try:
            _total_elapsed = (_t_mod.perf_counter() - _t_pipeline_start) * 1000
            logger.info(f"[DocSimple] TOTAL pipeline: {_total_elapsed:.0f}ms")
        except Exception:
            pass
        return _doc_pipeline_result

    # ── Cancel stale crud_form workflows when new file upload arrives ──
    # File uploads represent a fresh user action, not continuation of prior multi-turn
    if file_metas and session_id:
        try:
            from .unified_chat import logger as _uc_logger  # noqa: F401
        except ImportError:
            pass
        try:
            from ..services.unified_agent.workflow_engine import (
                WorkflowEngine as _WFE_cancel,
            )
            from ..services.unified_agent.db_utils import (
                get_session_db_pool as _wf_cancel_pool,
            )

            _wfc_db = await _wf_cancel_pool()
            _wfc = _WFE_cancel(
                _wfc_db,
                ctx["tenant_id"],
                ctx.get("user_id", ""),
                ctx.get("auth_token", ""),
            )
            _wfc_state = await _wfc.get_state(session_id, "crud_form")
            if _wfc_state and _wfc_state.status == "active":
                await _wfc.cancel(session_id, "crud_form")
                logger.info(
                    "[Upload] Cancelled stale crud_form workflow for fresh upload"
                )
        except Exception as _wfc_err:
            logger.warning("[Upload] Failed to cancel stale workflow: %s", _wfc_err)

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
        image_content=image_content,  # Vision blocks — ephemeral, not saved to history
    )

    # ── Logging ──
    msg_type = (
        agent_resp.get("message_type")
        if isinstance(agent_resp, dict)
        else agent_resp.message_type
    )
    iterations = (
        agent_resp.get("iterations", 0)
        if isinstance(agent_resp, dict)
        else agent_resp.iterations
    )
    tool_calls = (
        agent_resp.get("tool_calls_made", [])
        if isinstance(agent_resp, dict)
        else (agent_resp.tool_calls_made or [])
    )
    model_used = (
        agent_resp.get("model_used")
        if isinstance(agent_resp, dict)
        else agent_resp.model_used
    )
    latency = (
        agent_resp.get("total_latency_ms", 0)
        if isinstance(agent_resp, dict)
        else agent_resp.total_latency_ms
    )

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

    # -- Save attachment records to DB --
    # Use session_id if present, fall back to conversation_id (may also be a valid session UUID)
    _att_session_id = session_id or conversation_id
    if file_metas and _att_session_id:
        try:
            att_pool = await get_session_db_pool()
            saved_atts = await _save_chat_attachments(
                pool=att_pool,
                tenant_id=ctx["tenant_id"],
                session_id=_att_session_id,
                file_metas=file_metas,
            )
            if saved_atts:
                response.data = response.data or {}
                response.data["attachments"] = saved_atts
        except Exception as _att_err:
            logger.error("[Attachments] Post-save failed: %s", _att_err)

    return response


# =============================================================================
# GET /files/{storage_key} - Serve uploaded chat files with tenant isolation
# =============================================================================


@router.get("/files/{storage_key:path}")
async def get_chat_file(request: Request, storage_key: str):
    """Serve uploaded chat files with tenant isolation."""
    ctx = _get_user_context(request)
    tenant_id = ctx["tenant_id"]

    # Tenant isolation: storage_key must start with tenant_id/
    if not storage_key.startswith(f"{tenant_id}/"):
        raise HTTPException(status_code=403, detail="Access denied")

    file_path = os.path.join(UPLOAD_BASE_DIR, storage_key)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    # Determine content type from extension
    import mimetypes

    content_type, _ = mimetypes.guess_type(file_path)
    if not content_type:
        content_type = "application/octet-stream"

    return FileResponse(file_path, media_type=content_type)


async def _propose_document_draft(
    proposal: dict,
    pool,
    ctx: dict,
    session_id: str,
    conversation_id: str,
    file_metas: list,
) -> ChatMessageResponse:
    """
    Deterministically construct DIRECT_ACTION_PREVIEW for document draft confirmation.
    Bypasses ToolExecutor to avoid validation mismatch on confirm_document_draft.
    """
    import uuid as _uuid
    import json as _json_mod
    from datetime import datetime as _dt, timedelta as _td

    draft_plan = proposal.get("draft_plan", {})
    document_id = proposal.get("payload", {}).get("document_id") or proposal.get(
        "document_id", ""
    )
    narration = proposal.get("narration", "Dokumen siap di-review.")

    # -- Build confirmation table from draft_plan --
    doc_type = draft_plan.get("doc_type") or proposal.get("action_type", "unknown")
    counterparty = draft_plan.get("counterparty_name") or "Unknown"
    journal_draft = draft_plan.get("journal_draft") or {}
    total_debit = journal_draft.get("total_debit", "0")
    confidence = draft_plan.get("overall_confidence", 0)

    doc_type_labels = {
        "invoice_purchase": "Faktur Pembelian",
        "invoice_sales": "Faktur Penjualan",
        "receipt": "Kwitansi",
        "bank_transfer_out": "Transfer Keluar",
        "bank_transfer_in": "Transfer Masuk",
    }
    doc_type_display = doc_type_labels.get(doc_type, doc_type)

    lines = ["| Field | Value |", "|-------|-------|"]
    lines.append(f"| Tipe | {doc_type_display} |")
    lines.append(f"| Counterparty | {counterparty} |")

    line_items = (
        draft_plan.get("line_items") or draft_plan.get("inventory_movements") or []
    )
    if line_items:
        lines.append(f"| Items | {len(line_items)} item |")

    try:
        amount = int(float(total_debit))
        formatted = f"Rp {amount:,}".replace(",", ".")
    except (ValueError, TypeError):
        formatted = f"Rp {total_debit}"
    lines.append(f"| Total | {formatted} |")

    conf_pct = float(confidence) * 100 if confidence else 0
    lines.append(f"| Confidence | {conf_pct:.0f}% |")

    # Journal preview
    journal_lines = journal_draft.get("lines", [])
    if journal_lines:
        lines.append("")
        lines.append("**Dampak Jurnal:**")
        for jl in journal_lines:
            debit = float(jl.get("debit", 0) or 0)
            credit = float(jl.get("credit", 0) or 0)
            name = jl.get("account_name", "?")
            if debit > 0:
                lines.append(f"- Dr. {name}: Rp {int(debit):,}".replace(",", "."))
            if credit > 0:
                lines.append(f"- Cr. {name}: Rp {int(credit):,}".replace(",", "."))

    # -- Check dependencies (vendor/items existence) --
    from ..services.chat_document_bridge import check_draft_dependencies

    async with pool.acquire() as _dep_conn:
        await _dep_conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )
        deps = await check_draft_dependencies(_dep_conn, draft_plan, ctx["tenant_id"])
    if not deps["all_resolved"]:
        lines.append("")
        lines.append("**Akan dibuat otomatis saat confirm:**")
        if deps["vendor_missing"]:
            lines.append(f"- Vendor baru: {deps['vendor_name']}")
        for _missing_item in deps["missing_items"]:
            lines.append(f"- Item baru: {_missing_item}")

    confirmation_table = "\n".join(lines)

    # -- Insert pending_actions record --
    pending_id = str(_uuid.uuid4())
    expires_at = _dt.utcnow() + _td(seconds=300)

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )
        await conn.execute(
            """
            INSERT INTO pending_actions (
                id, tenant_id, user_id, conversation_id,
                action_id, action_type, action_category,
                action_plan, status, expires_at, is_direct
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            _uuid.UUID(pending_id),
            ctx["tenant_id"],
            ctx["user_id"],
            conversation_id or "",
            "confirm_document_draft",
            "confirm_document_draft",
            "DOCUMENT",
            _json_mod.dumps({"document_id": document_id, "draft_plan": draft_plan}),
            "PENDING",
            expires_at,
            True,
        )

    # -- Persist OCR data to Layer 2 (document_context) --
    _line_items_summary = []
    _inv_movements = draft_plan.get("inventory_movements") or []
    _line_items_raw = draft_plan.get("line_items") or _inv_movements
    for _li in _line_items_raw:
        if not isinstance(_li, dict):
            continue
        _line_items_summary.append(
            {
                "description": _li.get("description") or _li.get("product_name") or "",
                "qty": _li.get("quantity") or _li.get("qty"),
                "unit_price": _li.get("unit_price") or _li.get("unit_cost"),
                "total": _li.get("total_price")
                or _li.get("total")
                or _li.get("amount"),
            }
        )

    # Extract totals from journal_draft if not at top level
    _jd = draft_plan.get("journal_draft") or {}
    _jd_lines = _jd.get("lines") or []
    _total_debit = sum(
        float(ln.get("debit") or 0) for ln in _jd_lines if isinstance(ln, dict)
    )
    _total_credit = sum(
        float(ln.get("credit") or 0) for ln in _jd_lines if isinstance(ln, dict)
    )

    # Extract vendor/counterparty from multiple possible locations
    _matched = draft_plan.get("matched_to") or {}
    _vendor = (
        draft_plan.get("counterparty_name")
        or draft_plan.get("counterparty")
        or (_matched.get("vendor_name") if isinstance(_matched, dict) else None)
    )

    _doc_context = {
        "document_id": document_id,
        "doc_type": draft_plan.get("action_type")
        or draft_plan.get("transaction_type")
        or "unknown",
        "confidence": float(
            draft_plan.get("overall_confidence") or draft_plan.get("confidence") or 0
        ),
        "document_number": draft_plan.get("document_number"),
        "document_date": draft_plan.get("document_date"),
        "vendor_name": _vendor,
        "total_amount": float(
            draft_plan.get("total_debit")
            or draft_plan.get("total_amount")
            or _total_debit
            or 0
        ),
        "tax_amount": float(draft_plan.get("tax_amount") or 0),
        "items": _line_items_summary,
        "pending_action_id": pending_id,
        "edits": {},
    }

    try:
        from ..services.unified_agent.session_manager import SessionManager as _DocSM

        _doc_sm = _DocSM(
            db_pool=pool, tenant_id=ctx["tenant_id"], user_id=ctx["user_id"]
        )
        await _doc_sm.update_state(session_id, document_context=_doc_context)
        logger.info(
            f"[DocDraft] Persisted document_context to Layer 2 for session {session_id}"
        )
    except Exception as _e:
        logger.warning(
            f"[DocDraft] Failed to persist document_context to Layer 2: {_e}"
        )

    # -- Build preview data matching orchestrator format --
    preview_data = {
        "pending_action_id": pending_id,
        "action_key": "confirm_document_draft",
        "display_name": "Konfirmasi Draft Dokumen",
        "payload": {"document_id": document_id},
        "expires_at": expires_at.isoformat() + "Z",
        "risk_level": "medium",
        "confirmation_table": confirmation_table,
        "loading_message": "Memproses dokumen...",
        "success_message": "Dokumen berhasil diterbitkan.",
        "uploaded_files": [
            {
                "filename": fm["filename"],
                "size": fm["size"],
                "extension": fm["extension"],
                "file_hash": fm["file_hash"],
            }
            for fm in file_metas
        ],
    }

    content_text = narration
    if confirmation_table:
        content_text = f"{narration}\n\n{confirmation_table}"

    return ChatMessageResponse(
        message_type="DIRECT_ACTION_PREVIEW",
        text=content_text,
        session_id=session_id,
        pending_action_id=pending_id,
        data=preview_data,
    )


async def _process_document_background(pool, doc_id: str, tenant_id: str):
    """Fire-and-forget background processing for batch document uploads."""
    try:
        from ..services.chat_document_bridge import process_document_sync

        result = await process_document_sync(pool, doc_id, tenant_id)
        logger.info(
            f"[DocPipeline] Background processing complete: {doc_id} -> {result.get('status')}"
        )
    except Exception as e:
        logger.error(
            f"[DocPipeline] Background processing failed: {doc_id} -> {e}",
            exc_info=True,
        )


async def _confirm_direct_action(
    pending_action_id: str,
    tenant_id: str,
    user_id: str,
    pool,
    http_request,
    payload_overrides: dict = None,
    session_id: str = None,
) -> ChatMessageResponse:
    """Execute a direct action by calling the REST endpoint."""
    import httpx
    from ..services.unified_agent.direct_action_registry import get_direct_action

    # Fetch pending action
    row = await pool.fetchrow(
        """SELECT action_id, action_plan, status, expires_at
           FROM pending_actions WHERE id = $1 AND tenant_id = $2""",
        uuid_mod.UUID(pending_action_id),
        tenant_id,
    )

    if not row:
        return ChatMessageResponse(
            message_type="ACTION_RESULT",
            text="Action tidak ditemukan.",
            data={"success": False},
        )

    if row["status"] != "PENDING":
        return ChatMessageResponse(
            message_type="ACTION_RESULT",
            text=f"Action sudah {row['status'].lower()}.",
            data={"success": False},
        )

    from datetime import datetime, timezone

    if row["expires_at"] and row["expires_at"].replace(
        tzinfo=timezone.utc
    ) < datetime.now(timezone.utc):
        await pool.execute(
            "UPDATE pending_actions SET status = 'EXPIRED' WHERE id = $1",
            uuid_mod.UUID(pending_action_id),
        )
        # Phase D: Update chat message metadata status to EXPIRED
        try:
            await pool.execute(
                """
                UPDATE chat_messages
                SET metadata = jsonb_set(
                    COALESCE(metadata, '{}'::jsonb),
                    '{status}', '"EXPIRED"'
                )
                WHERE metadata->>'pending_action_id' = $1
                  AND tenant_id = $2
            """,
                str(pending_action_id),
                tenant_id,
            )
        except Exception as _md_err:
            logger.warning(
                "[Phase-D] Failed to update message metadata on expire: %s", _md_err
            )
        return ChatMessageResponse(
            message_type="ACTION_RESULT",
            text="Action sudah kedaluwarsa. Silakan buat ulang.",
            data={"success": False},
        )

    action_key = row["action_id"]
    action_plan_raw = row["action_plan"]
    payload = (
        json.loads(action_plan_raw)
        if isinstance(action_plan_raw, str)
        else action_plan_raw
    )
    if payload_overrides:
        payload.update(payload_overrides)
    config = get_direct_action(action_key)

    if not config:
        return ChatMessageResponse(
            message_type="ACTION_RESULT",
            text=f"Konfigurasi untuk '{action_key}' tidak ditemukan.",
            data={"success": False},
        )

    # Mark as executing
    await pool.execute(
        "UPDATE pending_actions SET status = 'EXECUTING', confirmed_at = NOW() WHERE id = $1",
        uuid_mod.UUID(pending_action_id),
    )

    # Forward tenant JWT for auth
    auth_header = http_request.headers.get("authorization", "")
    base_url = "http://localhost:8000"  # internal API

    try:
        # --- Bug Fix 1: Resolve path parameters (e.g. {id}) from payload ---
        endpoint = config.rest_endpoint
        if "{" in endpoint:
            try:
                endpoint = endpoint.format(**payload)
            except KeyError:
                if "{id}" in endpoint:
                    entity_id = (
                        payload.get("id")
                        or payload.get("account_id")
                        or payload.get(f"{config.entity_type}_id", "")
                    )
                    endpoint = endpoint.replace("{id}", str(entity_id))

        # Strip display_only fields (context for user, not needed by REST endpoint)
        display_only_names = {f.name for f in config.fields if f.display_only}
        clean_payload = {
            k: v for k, v in payload.items() if k not in display_only_names
        }

        # Resolve default CoA accounts based on DirectActionConfig.default_accounts_policy
        # (parity with form UI — chat-created entities get same account linkage)
        if config.default_accounts_policy:
            try:
                from ..services.default_accounts import (
                    resolve_default_accounts as _resolve_defaults,
                )

                _acct_conn = await pool.acquire()
                try:
                    await _resolve_defaults(
                        _acct_conn,
                        tenant_id,
                        clean_payload,
                        config.default_accounts_policy,
                    )
                finally:
                    await pool.release(_acct_conn)
            except Exception as _acct_err:
                logger.warning(
                    "[default_accounts] Resolve failed (non-blocking): %s", _acct_err
                )

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
                "reference_number": payload.get(
                    "invoice_numbers", payload.get("reference_number", "")
                ),
                "notes": "Pembayaran dari rekonsiliasi bank",
            }
            if payload.get("session_id"):
                clean_payload["session_id"] = payload["session_id"]
            if payload.get("statement_line_id"):
                clean_payload["statement_line_id"] = payload["statement_line_id"]

        elif action_key == "create_vendor_credit":
            # VC schema requires vendor_name (stripped by display_only), reason enum, items[].
            _reason_raw = str(payload.get("reason") or "").lower()
            if any(k in _reason_raw for k in ("cacat", "rusak", "damaged")):
                _reason_enum = "damaged"
            elif any(k in _reason_raw for k in ("retur", "kembalikan", "return")):
                _reason_enum = "return"
            elif any(k in _reason_raw for k in ("diskon", "discount", "potongan")):
                _reason_enum = "discount"
            elif any(
                k in _reason_raw for k in ("harga salah", "kesalahan harga", "pricing")
            ):
                _reason_enum = "pricing_error"
            else:
                _reason_enum = "other"
            _amt = int(float(payload.get("amount") or payload.get("total_amount") or 0))
            _items = payload.get("items") or []
            if not _items and _amt > 0:
                _items = [
                    {
                        "description": (payload.get("reason") or "Nota debit").strip()
                        or "Nota debit",
                        "quantity": 1,
                        "unit_price": _amt,
                    }
                ]
            clean_payload = {
                "vendor_id": payload.get("vendor_id"),
                "vendor_name": payload.get("vendor_name") or payload.get("name") or "",
                "vendor_credit_date": payload.get("vendor_credit_date"),
                "reason": _reason_enum,
                "reason_detail": payload.get("reason")
                if payload.get("reason")
                else None,
                "items": _items,
            }
            if payload.get("notes"):
                clean_payload["notes"] = payload["notes"]
            if payload.get("original_bill_id"):
                clean_payload["original_bill_id"] = payload["original_bill_id"]
            if payload.get("ref_no"):
                clean_payload["ref_no"] = payload["ref_no"]
            clean_payload = {k: v for k, v in clean_payload.items() if v is not None}

        elif action_key == "create_credit_note":
            # CN schema requires customer_name (stripped by display_only), reason enum, items[].
            _reason_raw = str(payload.get("reason") or "").lower()
            if any(k in _reason_raw for k in ("cacat", "rusak", "damaged")):
                _reason_enum = "damaged"
            elif any(k in _reason_raw for k in ("retur", "kembalikan", "return")):
                _reason_enum = "return"
            elif any(k in _reason_raw for k in ("diskon", "discount", "potongan")):
                _reason_enum = "discount"
            elif any(
                k in _reason_raw for k in ("harga salah", "kesalahan harga", "pricing")
            ):
                _reason_enum = "pricing_error"
            else:
                _reason_enum = "other"
            _amt = int(float(payload.get("amount") or payload.get("total_amount") or 0))
            _items = payload.get("items") or []
            if not _items and _amt > 0:
                _items = [
                    {
                        "description": (payload.get("reason") or "Nota kredit").strip()
                        or "Nota kredit",
                        "quantity": 1,
                        "unit_price": _amt,
                    }
                ]
            clean_payload = {
                "customer_id": payload.get("customer_id"),
                "customer_name": payload.get("customer_name")
                or payload.get("name")
                or "",
                "credit_note_date": payload.get("credit_note_date"),
                "reason": _reason_enum,
                "reason_detail": payload.get("reason")
                if payload.get("reason")
                else None,
                "items": _items,
            }
            if payload.get("notes"):
                clean_payload["notes"] = payload["notes"]
            if payload.get("original_invoice_id"):
                clean_payload["original_invoice_id"] = payload["original_invoice_id"]
            if payload.get("ref_no"):
                clean_payload["ref_no"] = payload["ref_no"]
            clean_payload = {k: v for k, v in clean_payload.items() if v is not None}

        elif action_key == "categorize_statement":
            clean_payload = {
                "statement_line_id": payload["statement_line_id"],
                "account_id": payload["account_id"],
                "description": payload.get("description", ""),
                "contact_id": payload.get("contact_id"),
            }

        # For DELETE/path-param requests, strip ID fields from body to avoid endpoint rejections
        request_body = clean_payload
        # Phase A: extract uploaded_document_id (audit trail) before stripping
        _uploaded_document_id = (
            clean_payload.pop("_uploaded_document_id", None)
            if isinstance(clean_payload, dict)
            else None
        )

        if config.rest_method.upper() == "DELETE":
            id_keys = {"id", "account_id", f"{config.entity_type}_id"}
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

            # Phase A.3: Link uploaded document to created entity (audit trail)
            if _uploaded_document_id and entity_id:
                _entity_type_map = {
                    "create_bill_payment": "payment",
                    "create_receive_payment": "payment",
                    "create_expense": "expense",
                    "create_bill": "bill",
                    "create_sales_invoice": "sales_invoice",
                }
                _link_entity_type = _entity_type_map.get(action_key)
                if _link_entity_type:
                    try:
                        async with pool.acquire() as _link_conn:
                            async with _link_conn.transaction():
                                await _link_conn.execute(
                                    f"SET LOCAL app.tenant_id = '{tenant_id}'"
                                )
                                await _link_conn.execute(
                                    """INSERT INTO document_attachments
                                       (tenant_id, document_id, entity_type, entity_id, attachment_type, attached_by)
                                       VALUES ($1, $2, $3, $4, 'receipt', $5)
                                       ON CONFLICT (document_id, entity_type, entity_id) DO NOTHING""",
                                    tenant_id,
                                    uuid_mod.UUID(_uploaded_document_id),
                                    _link_entity_type,
                                    uuid_mod.UUID(entity_id),
                                    uuid_mod.UUID(user_id) if user_id else None,
                                )
                                logger.info(
                                    f"[DocLink] Attached document {_uploaded_document_id[:8]} to {_link_entity_type}/{entity_id[:8]}"
                                )
                    except Exception as _link_err:
                        logger.warning(
                            f"[DocLink] Failed to attach document: {_link_err}"
                        )

            await pool.execute(
                """UPDATE pending_actions
                   SET status = 'COMPLETED', executed_at = NOW(),
                       result = $1
                   WHERE id = $2""",
                json.dumps(result_data),
                uuid_mod.UUID(pending_action_id),
            )

            # Phase D: Update chat message metadata status to COMPLETED
            try:
                await pool.execute(
                    """
                    UPDATE chat_messages
                    SET metadata = jsonb_set(
                        jsonb_set(
                            COALESCE(metadata, '{}'::jsonb),
                            '{status}', '"COMPLETED"'
                        ),
                        '{completed_at}', to_jsonb(NOW()::text)
                    )
                    WHERE metadata->>'pending_action_id' = $1
                      AND tenant_id = $2
                """,
                    str(pending_action_id),
                    tenant_id,
                )
            except Exception as _md_err:
                logger.warning(
                    "[Phase-D] Failed to update message metadata on confirm: %s",
                    _md_err,
                )

            success_msg = config.get_success_message(payload)

            # Clear document_context from Layer 2 (only for document actions)
            if action_key == "confirm_document_draft" and session_id:
                try:
                    from ..services.unified_agent.session_manager import (
                        SessionManager as _ClearSM,
                    )

                    _clear_sm = _ClearSM(
                        db_pool=pool, tenant_id=tenant_id, user_id=user_id
                    )
                    await _clear_sm.update_state(session_id, document_context=None)
                except Exception as _e:
                    logger.warning(
                        f"[DocConfirm] Failed to clear document_context: {_e}"
                    )

            # Recon actions: signal frontend to auto-continue workflow
            recon_actions = {
                "confirm_single_match",
                "categorize_statement",
                "create_bill_payment",
                "create_receive_payment",
            }
            is_recon = action_key in recon_actions

            # Task G: Short acknowledgment for recon actions
            if is_recon:
                success_msg = "Dicatat."

            # Post-execute: mark statement line as matched for recon payment actions
            if action_key in ("create_bill_payment", "create_receive_payment"):
                _sl_id = payload.get("statement_line_id")
                _sess_id = payload.get("session_id")
                if _sl_id and _sess_id:
                    try:
                        # Mark statement line as matched
                        await pool.execute(
                            """UPDATE bank_statement_lines_v2
                               SET match_status = 'matched', updated_at = NOW()
                               WHERE id = $1 AND tenant_id = $2""",
                            uuid_mod.UUID(_sl_id),
                            tenant_id,
                        )
                        # Update session matched/unmatched counts
                        await pool.execute(
                            """UPDATE reconciliation_sessions
                               SET matched_count = (
                                       SELECT COUNT(*) FROM bank_statement_lines_v2
                                       WHERE session_id = $1 AND match_status = 'matched' AND tenant_id = $2
                                   ),
                                   unmatched_count = (
                                       SELECT COUNT(*) FROM bank_statement_lines_v2
                                       WHERE session_id = $1 AND match_status <> 'matched' AND tenant_id = $2
                                   ),
                                   updated_at = NOW()
                               WHERE id = $1 AND tenant_id = $2""",
                            uuid_mod.UUID(_sess_id),
                            tenant_id,
                        )
                        logger.info(
                            f"[DirectAction] Marked statement line {_sl_id} as matched (session {_sess_id})"
                        )
                    except Exception as _mark_err:
                        logger.warning(
                            f"[DirectAction] Failed to mark statement line matched: {_mark_err}"
                        )

            # ── Tutorial auto-advance on data-changed ──
            try:
                _ta_active = await get_active_tutorial(pool, user_id)
                if _ta_active and _ta_active.status == "active":
                    _ta_step = _get_tutorial_step(
                        _ta_active.tutorial_key, _ta_active.current_step
                    )
                    if (
                        _ta_step
                        and _ta_step.completion_trigger
                        == f"data_changed:{config.entity_type}"
                    ):
                        _ta_next = await _advance_tutorial_step(
                            pool, user_id, tenant_id, _ta_active.tutorial_key
                        )
                        logger.info(
                            f"[Tutorial] Auto-advanced {_ta_active.tutorial_key} -> step {_ta_next}"
                        )
            except Exception as _ta_err:
                logger.warning(f"[Tutorial] Auto-advance failed: {_ta_err}")

            # Response composer: inject CTA buttons
            from ..services.unified_agent.response_composer import (
                compose_confirm_response,
            )

            _composed = compose_confirm_response(
                action_key=action_key,
                success_message=success_msg,
                payload=payload,
                action_result=result_data,
            )

            # Bucket A1.5: fire after_confirm hook for direct actions so
            # action_patterns / L2 state get updated on this path too. The
            # non-direct path (ActionExecutor) already fires this hook below.
            # Single source of truth: only here (not at caller).
            if session_id:
                try:
                    sm_hook = SessionManager(
                        db_pool=pool, tenant_id=tenant_id, user_id=user_id
                    )
                    # Use action_key directly (lowercase intent) so downstream
                    # record_pattern PATTERN_INTENTS check matches (e.g.
                    # "create_sales_invoice"). state.last_action_type may be
                    # uppercase legacy form.
                    _action_type = action_key
                    # Pass the ORIGINAL payload under "payload" so
                    # after_confirm_pattern -> record_pattern sees
                    # customer_id/vendor_id/items for structure_key.
                    _hook_result = {
                        "success": True,
                        "action_type": action_key.upper(),
                        "entity_id": str(entity_id),
                        "entity_type": config.entity_type,
                        "result_data": result_data,
                        "payload": payload,
                    }
                    await StateUpdateHooks.after_confirm(
                        sm_hook, session_id, _action_type, _hook_result
                    )
                    logger.info(
                        "[HOOK] after_confirm (direct): session=%s action=%s",
                        session_id[:8],
                        _action_type,
                    )
                except (
                    KeyError,
                    TypeError,
                    ValueError,
                    asyncpg.PostgresError,
                    json.JSONDecodeError,
                ) as _hook_err:
                    logger.error(
                        "[HOOK] after_confirm (direct) failed: session=%s err=%s",
                        session_id[:8] if session_id else "-",
                        _hook_err,
                    )

            return ChatMessageResponse(
                message_type="ACTION_RESULT",
                text=_composed["text"],
                data={
                    "success": True,
                    "action_type": action_key.upper(),
                    "entity_id": str(entity_id),
                    "entity_type": config.entity_type,
                    "next_actions": _composed.get("next_actions"),
                },
                workflow_continuation=True if is_recon else None,
            )
        else:
            error_detail = response.text
            try:
                error_json = response.json()
                error_detail = error_json.get(
                    "detail", error_json.get("message", response.text)
                )
            except Exception:
                pass

            await pool.execute(
                """UPDATE pending_actions
                   SET status = 'FAILED', executed_at = NOW(),
                       error_message = $1
                   WHERE id = $2""",
                str(error_detail),
                uuid_mod.UUID(pending_action_id),
            )

            return ChatMessageResponse(
                message_type="ACTION_RESULT",
                text=f"Gagal: {error_detail}",
                data={"success": False},
            )

    except Exception as e:
        await pool.execute(
            """UPDATE pending_actions
               SET status = 'FAILED', executed_at = NOW(),
                   error_message = $1
               WHERE id = $2""",
            str(e),
            uuid_mod.UUID(pending_action_id),
        )
        return ChatMessageResponse(
            message_type="ACTION_RESULT",
            text=f"Error: {str(e)}",
            data={"success": False},
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
                uuid_mod.UUID(body.pending_action_id),
                ctx["tenant_id"],
            )
        except Exception as da_err:
            logger.warning(
                f"[Confirm] Direct action check failed (non-fatal): {da_err}"
            )
            is_direct = False

        if is_direct:
            # Direct action — execute via REST
            da_pool2 = await get_session_db_pool()
            return await _confirm_direct_action(
                body.pending_action_id,
                ctx["tenant_id"],
                ctx["user_id"],
                da_pool2,
                request,
                payload_overrides=body.payload_overrides,
                session_id=body.session_id,
            )

        # FSM: AWAITING_CONFIRMATION -> EXECUTING
        try:
            if body.session_id:
                db_pool = await get_session_db_pool()
                sm_fsm = SessionManager(
                    db_pool=db_pool, tenant_id=ctx["tenant_id"], user_id=ctx["user_id"]
                )
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
                    sm = SessionManager(
                        db_pool=db_pool,
                        tenant_id=ctx["tenant_id"],
                        user_id=ctx["user_id"],
                    )
                    # Get action_type from current session state (set during propose)
                    state = await sm.get_state(body.session_id)
                    action_type = state.last_action_type or "UNKNOWN"
                    await StateUpdateHooks.after_confirm(
                        sm, body.session_id, action_type, result
                    )
                    # Clear pending payload + FSM → IDLE on successful confirm
                    await sm.update_state(
                        body.session_id,
                        pending_payload={},
                        pending_intent="",
                        editing_mode=False,
                    )
                    await sm.transition_fsm(body.session_id, FSMState.IDLE.value)
                    logger.info(
                        f"[Confirm] Layer 2 updated: session={body.session_id[:8]} "
                        f"action={action_type} status=confirmed fsm=IDLE"
                    )

                    # Cancel crud_form workflow if active
                    try:
                        from ..services.unified_agent.workflow_engine import (
                            WorkflowEngine,
                        )

                        _wf_db_confirm = await get_session_db_pool()
                        _wf_eng_confirm = WorkflowEngine(
                            _wf_db_confirm,
                            ctx["tenant_id"],
                            ctx["user_id"],
                            "",
                        )
                        _wf_confirm = await _wf_eng_confirm.get_state(
                            body.session_id, "crud_form"
                        )
                        if _wf_confirm and _wf_confirm.status == "active":
                            await _wf_eng_confirm.cancel(body.session_id, "crud_form")
                            logger.info(
                                "[Confirm] Cancelled crud_form workflow after confirm success"
                            )
                    except Exception as _wf_err:
                        logger.warning(
                            "[Confirm] crud_form cancel failed (non-fatal): %s", _wf_err
                        )

            except (
                KeyError,
                TypeError,
                ValueError,
                asyncpg.PostgresError,
                json.JSONDecodeError,
            ):
                logger.error(
                    "[Confirm] Layer 2 hook failed: session=%s action=%s",
                    body.session_id,
                    locals().get("action_type", "UNKNOWN"),
                    exc_info=True,
                )

            # Fallback: ensure FSM is IDLE even if hooks failed
            if body.session_id:
                try:
                    _fb_pool = await get_session_db_pool()
                    _fb_sm = SessionManager(
                        db_pool=_fb_pool,
                        tenant_id=ctx["tenant_id"],
                        user_id=ctx["user_id"],
                    )
                    _fb_state = await _fb_sm.get_state(body.session_id)
                    if _fb_state.fsm_state != "IDLE":
                        await _fb_sm.transition_fsm(
                            body.session_id, FSMState.IDLE.value
                        )
                        logger.warning(
                            "[Confirm] Fallback FSM→IDLE (was %s)", _fb_state.fsm_state
                        )
                except Exception:
                    pass

            text = (
                "Dokumen berhasil disimpan sebagai draft."
                if body.doc_status == "DRAFT"
                else "Transaksi berhasil diterbitkan."
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
                    sm_fail = SessionManager(
                        db_pool=db_pool_fail,
                        tenant_id=ctx["tenant_id"],
                        user_id=ctx["user_id"],
                    )
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
        _result = await executor.cancel_action(
            pending_action_id=body.pending_action_id,
            tenant_id=ctx["tenant_id"],
            user_id=ctx["user_id"],
        )

        # Phase D: Update chat message metadata status to CANCELLED
        try:
            _cancel_pool = await get_session_db_pool()
            await _cancel_pool.execute(
                """
                UPDATE chat_messages
                SET metadata = jsonb_set(
                    COALESCE(metadata, '{}'::jsonb),
                    '{status}', '"CANCELLED"'
                )
                WHERE metadata->>'pending_action_id' = $1
                  AND tenant_id = $2
            """,
                str(body.pending_action_id),
                ctx["tenant_id"],
            )
        except Exception as _md_err:
            logger.warning(
                "[Phase-D] Failed to update message metadata on cancel: %s", _md_err
            )

        # FSM: AWAITING_CONFIRMATION -> IDLE
        try:
            db_pool_fsm = await get_session_db_pool()
            sm_fsm = SessionManager(
                db_pool=db_pool_fsm,
                tenant_id=ctx["tenant_id"],
                user_id=ctx["user_id"],
            )
            if body.session_id:
                await sm_fsm.transition_fsm(body.session_id, FSMState.IDLE.value)
            else:
                # Fallback: clear ALL AWAITING_CONFIRMATION for this user
                await db_pool_fsm.execute(
                    """UPDATE chat_session_state
                       SET fsm_state = 'IDLE'
                       WHERE tenant_id = $1
                         AND fsm_state = 'AWAITING_CONFIRMATION'""",
                    ctx["tenant_id"],
                )
                logger.warning(
                    "[Cancel] No session_id — cleared all AWAITING_CONFIRMATION for user %s",
                    ctx["user_id"],
                )
        except Exception as fsm_err:
            logger.warning(f"[Cancel] FSM transition failed (non-fatal): {fsm_err}")

        # === Update Layer 2 session state via hooks ===
        try:
            if body.session_id:
                db_pool = await get_session_db_pool()
                sm = SessionManager(
                    db_pool=db_pool, tenant_id=ctx["tenant_id"], user_id=ctx["user_id"]
                )
                # Get action_type from current session state (set during propose)
                state = await sm.get_state(body.session_id)
                action_type = state.last_action_type or "UNKNOWN"
                await StateUpdateHooks.after_reject(sm, body.session_id, action_type)
                logger.info(
                    f"[Cancel] Layer 2 updated: session={body.session_id[:8]} "
                    f"action={action_type} status=rejected"
                )
        except (
            KeyError,
            TypeError,
            ValueError,
            asyncpg.PostgresError,
            json.JSONDecodeError,
        ):
            logger.error(
                "[Cancel] Layer 2 hook failed: session=%s action=%s",
                body.session_id,
                locals().get("action_type", "UNKNOWN"),
                exc_info=True,
            )

        # === Edit mode: preserve pending_payload and set editing_mode ===
        if body.is_edit and body.session_id:
            try:
                db_pool_edit = await get_session_db_pool()
                sm_edit = SessionManager(
                    db_pool=db_pool_edit,
                    tenant_id=ctx["tenant_id"],
                    user_id=ctx["user_id"],
                )
                # Extract payload from pending_actions and save to session state
                _pa_row = await db_pool_edit.fetchrow(
                    "SELECT action_type, action_plan FROM pending_actions WHERE id = $1::uuid AND tenant_id = $2",
                    body.pending_action_id,
                    ctx["tenant_id"],
                )
                _edit_payload = {}
                _edit_intent = ""
                if _pa_row:
                    _edit_intent = (_pa_row["action_type"] or "").lower()
                    _raw_payload = _pa_row["action_plan"]
                    if isinstance(_raw_payload, str):
                        import json as _ej

                        _edit_payload = _ej.loads(_raw_payload)
                    elif isinstance(_raw_payload, dict):
                        _edit_payload = _raw_payload
                    else:
                        _edit_payload = {}
                    logger.info(
                        "[Cancel-Edit] Extracted payload: intent=%s keys=%s",
                        _edit_intent,
                        list(_edit_payload.keys())[:8],
                    )

                await sm_edit.update_state(
                    body.session_id,
                    editing_mode=True,
                    pending_payload=_edit_payload,
                    pending_intent=_edit_intent,
                )
                logger.info(
                    "[Cancel-Edit] editing_mode=True + payload saved for session=%s",
                    body.session_id[:8],
                )
            except Exception as edit_err:
                logger.warning("[Cancel-Edit] Failed to set editing_mode: %s", edit_err)

            # Also rewind crud_form workflow to COLLECTING (keep payload)
            try:
                from ..services.unified_agent.workflow_engine import WorkflowEngine

                _pool_edit = await get_session_db_pool()
                _eng_edit = WorkflowEngine(
                    db_pool=_pool_edit,
                    tenant_id=ctx["tenant_id"],
                    user_id=ctx["user_id"],
                    auth_token=ctx["auth_token"],
                )
                _wf_edit = await _eng_edit.get_state(body.session_id, "crud_form")
                if _wf_edit and _wf_edit.status == "active":
                    _wf_edit.current_state = "COLLECTING"
                    await _eng_edit._save(_wf_edit)
                    logger.warning(
                        "[Cancel-Edit] Rewound crud_form to COLLECTING for session=%s",
                        body.session_id[:8],
                    )
            except Exception as _wf_edit_err:
                logger.warning(
                    "[Cancel-Edit] Workflow rewind failed (non-fatal): %s", _wf_edit_err
                )
        elif not body.is_edit and body.session_id:
            # Full cancel — also cancel any active crud_form workflow
            try:
                from ..services.unified_agent.workflow_engine import WorkflowEngine

                _pool_cancel = await get_session_db_pool()
                _eng_cancel = WorkflowEngine(
                    db_pool=_pool_cancel,
                    tenant_id=ctx["tenant_id"],
                    user_id=ctx["user_id"],
                    auth_token=ctx["auth_token"],
                )
                _cancelled = await _eng_cancel.cancel(body.session_id, "crud_form")
                if _cancelled:
                    logger.warning(
                        "[Cancel] Cancelled crud_form workflow for session=%s",
                        body.session_id[:8],
                    )
            except Exception as _wf_cancel_err:
                logger.warning(
                    "[Cancel] Workflow cancel failed (non-fatal): %s", _wf_cancel_err
                )

        # Build contextual cancel message with workflow info
        cancel_text = "Ok, dilewati."
        try:
            if body.session_id:
                from ..services.unified_agent.workflow_engine import WorkflowEngine

                _pool = await get_session_db_pool()
                _eng = WorkflowEngine(
                    db_pool=_pool,
                    tenant_id=ctx["tenant_id"],
                    user_id=ctx["user_id"],
                    auth_token=ctx["auth_token"],
                )
                _wf = await _eng.get_state(body.session_id, "bank_reconciliation")
                if _wf and _wf.status == "active" and _wf.current_state == "REVIEWING":
                    _summary = _wf.data.get("summary", {})
                    _unmatched = _summary.get("unmatched_count", 0)
                    _reviewed = _wf.data.get("reviewed_count", 0)
                    _remaining = max(
                        0, _unmatched - _reviewed - 1
                    )  # -1 for the one just cancelled
                    if _remaining > 0:
                        cancel_text = f"Ok, dilewati. Masih ada {_remaining} transaksi lagi yang perlu review."
                    else:
                        cancel_text = 'Ok, dilewati. Itu item terakhir — ketik "lanjut" untuk selesaikan rekonsiliasi.'
        except Exception:
            pass  # Fall back to simple message

        return ChatMessageResponse(
            message_type="TEXT",
            text=cancel_text,
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
            messages.append(
                {
                    "id": msg.message_id,
                    "role": "user",
                    "content": msg.message,
                    "timestamp": msg.timestamp,
                }
            )
            if msg.response:
                messages.append(
                    {
                        "id": f"{msg.message_id}_resp",
                        "role": "assistant",
                        "content": msg.response,
                        "timestamp": msg.timestamp,
                    }
                )

        return {"messages": messages, "count": len(messages)}

    except Exception as e:
        logger.warning(f"[History] Fetch failed: {e}")
        return {"messages": [], "error": str(e)}


@router.post("/feedback")
async def record_feedback(request: Request):
    """Record user thumbs up/down on bot response."""
    ctx = _get_user_context(request)
    body = await request.json()
    session_id = body.get("session_id")
    feedback = body.get("feedback", 0)

    if not session_id or feedback not in (1, -1):
        return {"success": False, "error": "session_id and feedback (+1/-1) required"}

    try:
        from ..services.unified_agent.telemetry import IntentTelemetry

        pool = await get_session_db_pool()
        telemetry = IntentTelemetry(pool, ctx["tenant_id"])
        await telemetry.record_feedback(session_id, feedback)
        return {"success": True}
    except Exception as e:
        logger.warning("[FEEDBACK] Record failed: %s", e)
        return {"success": False, "error": str(e)[:100]}


# =============================================================================
# POST /action/edit — Apply NL edit patch to pending action (Bucket 3 Step 2)
# =============================================================================


@router.post("/action/edit", response_model=ChatMessageResponse)
async def edit_action(request: Request, body: EditActionRequest):
    """Apply a natural-language edit to a pending action's payload.

    Flow:
      1. Auth + tenant context (mirror confirm_action).
      2. Load pending row from Postgres via action_service.get_pending_action_pg.
      3. Delegate to action_service.edit_pending_action (regex/LLM patch + revalidate).
      4. On success: insert NEW chat_messages bot row with edit's preview_snapshot,
         mark prior bot row metadata.superseded_by = new_message_id (Phase D).
      5. Return DIRECT_ACTION_PREVIEW with same pending_action_id.

    On failure: return TEXT with error message (no clarification flow in Step 2).
    """
    ctx = _get_user_context(request)
    tenant_id = ctx["tenant_id"]
    _ = ctx.get("user_id")  # reserved for future audit logging

    try:
        # 1. Load pending envelope from Redis
        _as_pool = await get_session_db_pool()
        action_service = ActionService(_as_pool)
        pending = await action_service.get_pending_action_pg(
            tenant_id, body.pending_action_id
        )
        if not pending:
            return ChatMessageResponse(
                message_type="TEXT",
                text="Aksi sudah expired. Silakan ulangi.",
                session_id=body.session_id,
                pending_action_id=body.pending_action_id,
                trace_id=str(uuid_mod.uuid4()),
            )

        action_key = (
            pending.get("action_type")
            or pending.get("action_key")
            or pending.get("action_id")
        )
        if not action_key:
            return ChatMessageResponse(
                message_type="TEXT",
                text="Tidak dapat menentukan jenis aksi. Silakan ulangi.",
                session_id=body.session_id,
                pending_action_id=body.pending_action_id,
                trace_id=str(uuid_mod.uuid4()),
            )

        # 2. Apply edit
        result = await action_service.edit_pending_action(
            tenant_id=tenant_id,
            pending_id=body.pending_action_id,
            text=body.text,
            action_key=action_key,
        )

        if not result.get("success"):
            logger.info(
                "[BucketA1.8] edit failed: pending=%s err=%s msg=%s",
                body.pending_action_id[:8],
                result.get("error"),
                result.get("message"),
            )
            return ChatMessageResponse(
                message_type="TEXT",
                text=result.get("message") or "Edit gagal. Silakan coba lagi.",
                session_id=body.session_id,
                pending_action_id=body.pending_action_id,
                trace_id=str(uuid_mod.uuid4()),
            )

        new_payload = result.get("payload") or result.get("preview") or {}
        new_version = result.get("version")

        # 3. Phase D: write NEW chat_messages bot row + supersede prior preview row
        new_message_id = str(uuid_mod.uuid4())
        try:
            if body.session_id:
                _md_pool = await get_session_db_pool()
                # Mark prior preview row(s) as superseded by this new message
                await _md_pool.execute(
                    """
                    UPDATE chat_messages
                       SET metadata = jsonb_set(
                           COALESCE(metadata, '{}'::jsonb),
                           '{superseded_by}',
                           to_jsonb($3::text)
                       )
                     WHERE tenant_id = $1
                       AND metadata->>'pending_action_id' = $2
                       AND (metadata->>'superseded_by') IS NULL
                       AND COALESCE(metadata->>'action_status', '') <> 'COMPLETED'
                    """,
                    tenant_id,
                    body.pending_action_id,
                    new_message_id,
                )

                # Insert new bot row carrying the edit preview snapshot
                _new_metadata = {
                    "pending_action_id": body.pending_action_id,
                    "action_status": "PENDING_EDITED",
                    "preview_snapshot": new_payload,
                    "version": new_version,
                    "message_type": "DIRECT_ACTION_PREVIEW",
                }
                await _md_pool.execute(
                    """
                    INSERT INTO chat_messages (
                        id, session_id, tenant_id, role, content,
                        message_type, metadata
                    ) VALUES (
                        $1::uuid, $2::uuid, $3, $4, $5, $6, $7::jsonb
                    )
                    """,
                    new_message_id,
                    body.session_id,
                    tenant_id,
                    "assistant",
                    "[edit applied]",
                    "DIRECT_ACTION_PREVIEW",
                    json.dumps(_new_metadata, default=str),
                )
        except Exception as _md_err:
            logger.warning(
                "[BucketA1.8] Phase-D metadata persistence failed (non-fatal): %s",
                _md_err,
            )

        logger.info(
            "[BucketA1.8] edit applied: tenant=%s pending=%s version=%s",
            tenant_id,
            body.pending_action_id[:8],
            new_version,
        )

        return ChatMessageResponse(
            message_id=new_message_id,
            message_type="DIRECT_ACTION_PREVIEW",
            text="Edit diterapkan. Silakan tinjau perubahan dan konfirmasi.",
            data=new_payload,
            session_id=body.session_id,
            pending_action_id=body.pending_action_id,
            trace_id=str(uuid_mod.uuid4()),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "[BucketA1.8] edit_action failed for %s", body.pending_action_id
        )
        raise HTTPException(status_code=500, detail=f"Edit failed: {str(e)}")
