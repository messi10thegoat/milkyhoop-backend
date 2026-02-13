"""
Router: Agentic Chat Action Mode (v3 - Full Pipeline)

Complete microservice pipeline:
  1. ActionPlanner (gRPC)   → Intent classification, text parsing, response generation
  2. ActionValidator (gRPC) → 6-layer validation, dry-run journal preview
  3. ActionExecutor (gRPC)  → Two-phase commit, saga execution

Endpoints:
  POST /api/action-chat/message           - Send text message
  POST /api/action-chat/message/structured - Send structured data → validate → preview
  POST /api/action-chat/confirm           - Confirm pending action → execute
  POST /api/action-chat/cancel            - Cancel pending action
  GET  /api/action-chat/status/{id}       - Poll action status

IRON LAW 0: LLM → ActionPlanner, Validation → ActionValidator, Execution → ActionExecutor
IRON LAW 10: LLM NEVER writes data. All writes through Executor → Kernel.
"""
import logging
import uuid as uuid_mod
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from ..schemas.action_chat import (
    ChatMessageRequest,
    ConfirmActionRequest,
    CancelActionRequest,
    ChatMessageResponse,
    ActionStatusResponse,
    MessageType,
)
from ..services.action_planner_client import get_action_planner_client
from ..services.action_validator_client import get_action_validator_client
from ..services.action_executor_client import get_action_executor_client

logger = logging.getLogger(__name__)
router = APIRouter()


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": tenant_id, "user_id": user_id or ""}


def make_response(
    message_type: MessageType,
    text: str = None,
    data: dict = None,
    pending_action_id: str = None,
) -> ChatMessageResponse:
    return ChatMessageResponse(
        message_id=str(uuid_mod.uuid4()),
        message_type=message_type,
        text=text,
        data=data,
        trace_id=str(uuid_mod.uuid4()),
        pending_action_id=pending_action_id,
    )


# =============================================================================
# POST /message - Main text entry point
# =============================================================================
@router.post("/message", response_model=ChatMessageResponse)
async def send_message(request: Request, body: ChatMessageRequest):
    """
    Process a chat message through the full pipeline.
    Uses ActionPlanner for NLU, ActionValidator for validation,
    ActionExecutor for preparation.
    """
    ctx = get_user_context(request)
    planner = get_action_planner_client()

    text = (body.text or "").strip()
    if not text:
        greeting = await planner.generate_response(
            "User baru buka chat, belum ketik apa-apa. Sapa singkat.",
            context="User baru memulai percakapan."
        )
        return make_response(
            MessageType.TEXT,
            text=greeting or "Halo! Ada yang bisa saya bantu soal keuangan bisnis kamu?"
        )

    # Step 1: Classify intent via ActionPlanner gRPC
    classify_result = await planner.classify_intent(text, ctx["tenant_id"], ctx["user_id"])
    intent_type = classify_result.get("intent", "UNCLEAR").upper()
    action_type = classify_result.get("action_type", "")
    confidence = classify_result.get("confidence", 0.0)

    logger.info(f"Intent: {intent_type}, action: {action_type}, confidence: {confidence}, text: {text[:50]}")

    # --- CONFIRM (text-based, without pending action context) ---
    if intent_type == "CONFIRM":
        response = await planner.generate_response(
            text,
            context="User mengkonfirmasi tapi tidak ada pending action. Jelaskan bahwa konfirmasi dilakukan lewat tombol Lanjutkan di preview card."
        )
        return make_response(MessageType.TEXT, text=response or "Belum ada aksi yang perlu dikonfirmasi.")

    # --- CANCEL (text-based) ---
    if intent_type == "CANCEL":
        response = await planner.generate_response(
            text,
            context="User membatalkan tapi tidak ada pending action. Acknowledge dan tanya mau ngapain."
        )
        return make_response(MessageType.TEXT, text=response or "Oke, tidak ada yang dibatalkan. Ada yang lain?")

    # --- READ ---
    if intent_type == "READ":
        return await _handle_read_intent(text, ctx, planner)

    # --- ACTION ---
    if intent_type == "ACTION":
        return await _handle_action_intent(text, action_type, ctx, planner)

    # --- UNCLEAR ---
    return await _handle_unclear_intent(text, ctx, planner)


# =============================================================================
# Handlers for each intent type
# =============================================================================
async def _handle_read_intent(text: str, ctx: dict, planner) -> ChatMessageResponse:
    """Handle READ intent - master data queries and report queries."""
    text_lower = text.lower()
    is_master_data = any(kw in text_lower for kw in [
        "master data", "vendor", "supplier", "produk", "barang", "item",
        "sudah ada", "terdaftar", "ada gak", "ada nggak", "ada tidak",
        "cek data", "cek stok", "stok",
    ])

    if is_master_data:
        entities = await planner.extract_entities(text)
        ctx_parts = []
        for vname in entities.get("vendors", []):
            ctx_parts.append(f"Vendor '{vname}' — dicari di master data.")
        for pname in entities.get("products", []):
            ctx_parts.append(f"Produk '{pname}' — dicari di master data.")

        if not ctx_parts:
            ctx_parts.append("Tidak bisa mendeteksi nama vendor/produk spesifik.")

        response = await planner.generate_response(
            text,
            context=f"User bertanya soal master data. Hasil: {' | '.join(ctx_parts)}. Jawab natural."
        )
        return make_response(MessageType.TEXT, text=response or "Saya sudah cek master data.")

    response = await planner.generate_response(
        text,
        context="User bertanya soal data/laporan. Fitur laporan via chat belum tersedia, bisa diakses lewat menu Laporan."
    )
    return make_response(MessageType.TEXT, text=response or "Fitur laporan via chat sedang dikembangkan.")


async def _handle_action_intent(text: str, action_type: str, ctx: dict, planner) -> ChatMessageResponse:
    """Handle ACTION intent - parse, validate (optional), prepare preview."""
    # --- Master Data: CREATE_VENDOR, CREATE_CUSTOMER, CREATE_PRODUCT ---
    MASTER_DATA_ACTIONS = {"CREATE_VENDOR", "CREATE_CUSTOMER", "CREATE_PRODUCT"}

    if action_type in MASTER_DATA_ACTIONS:
        return await _handle_master_data_action(text, action_type, ctx, planner)

    # --- Document: CREATE_PURCHASE_INVOICE ---
    if action_type == "CREATE_PURCHASE_INVOICE":
        # Parse text via ActionPlanner
        parsed = await planner.parse_document_text(text, "CREATE_PURCHASE_INVOICE")
        if not parsed:
            parsed = {"vendor_name": None, "items": []}

        has_items = parsed.get("items") and len(parsed["items"]) > 0
        has_vendor = parsed.get("vendor_name") is not None

        if has_items and has_vendor:
            # We have enough data — run validation + prepare preview via executor
            return await _validate_and_prepare(
                action_type="CREATE_PURCHASE_INVOICE",
                category="DOCUMENT",
                payload=parsed,
                ctx=ctx,
                planner=planner,
                original_text=text,
            )
        else:
            # Need more info — ask user
            ctx_parts = []
            if has_vendor:
                ctx_parts.append(f"Vendor: {parsed['vendor_name']}")
            if has_items:
                ctx_parts.append(f"Items: {len(parsed['items'])} item")
            if not has_vendor:
                ctx_parts.append("Vendor belum disebutkan")
            if not has_items:
                ctx_parts.append("Item belum disebutkan")

            response = await planner.generate_response(
                text,
                context=f"User mau buat faktur pembelian. Data yang ada: {', '.join(ctx_parts)}. Tanya natural yang masih kurang."
            )
            return make_response(MessageType.TEXT, text=response or "Oke, mau catat faktur pembelian. Dari vendor mana dan item apa?")

    else:
        response = await planner.generate_response(
            text,
            context=f"User mau aksi '{action_type}' yang belum tersedia. Acknowledge, jelaskan segera hadir."
        )
        return make_response(MessageType.TEXT, text=response or f"Fitur {action_type} segera hadir.")


async def _validate_and_prepare(
    action_type: str,
    category: str,
    payload: dict,
    ctx: dict,
    planner,
    original_text: str,
) -> ChatMessageResponse:
    """
    Full pipeline: validate via ActionValidator → prepare via ActionExecutor.
    Returns ACTION_PREVIEW or VALIDATION_ERROR.
    """
    validator = get_action_validator_client()
    executor = get_action_executor_client()

    # Step 1: Validate via ActionValidator (6-layer pipeline)
    validation = await validator.validate_action(
        tenant_id=ctx["tenant_id"],
        user_id=ctx["user_id"],
        action_id=action_type,
        action_type=action_type,
        category=category,
        draft_payload=payload,
    )

    if not validation["valid"]:
        blocking_errors = [e for e in validation["errors"] if e.get("blocking")]
        if blocking_errors:
            return make_response(
                MessageType.VALIDATION_ERROR,
                text="Validasi gagal. Periksa data berikut:",
                data={
                    "errors": blocking_errors,
                    "suggestions": ["Perbaiki data dan coba lagi."],
                },
            )

    # Step 2: Prepare via ActionExecutor (creates pending action)
    assumptions = []
    if validation.get("dry_run") and validation["dry_run"].get("balanced"):
        assumptions.append("Jurnal balanced (debit = kredit)")
    if validation.get("confirmation_message"):
        assumptions.append(validation["confirmation_message"])

    prepare_result = await executor.prepare_action(
        tenant_id=ctx["tenant_id"],
        user_id=ctx["user_id"],
        action_type=action_type,
        category=category,
        draft_payload=payload,
        assumptions=assumptions,
    )

    if not prepare_result["success"]:
        return make_response(
            MessageType.VALIDATION_ERROR,
            text=prepare_result.get("error_message", "Gagal menyiapkan aksi."),
            data={
                "errors": [{"layer": "EXECUTOR", "code": "PREPARE_FAILED", "message": prepare_result.get("error_message", ""), "blocking": True}],
                "suggestions": ["Coba lagi."],
            },
        )

    # Step 3: Build ACTION_PREVIEW response
    preview = prepare_result.get("preview", {})
    dry_run = validation.get("dry_run", {})
    journal_preview = dry_run.get("journal_entries", []) if dry_run else []
    if not journal_preview and preview:
        journal_preview = preview.get("journal_entries", [])

    # Calculate totals from payload
    items_preview = []
    grand_total = 0
    for item in payload.get("items", []):
        qty = float(item.get("qty", 0))
        price = float(item.get("price", 0))
        subtotal = qty * price
        grand_total += subtotal
        items_preview.append({
            "name": item.get("name", item.get("product_name", "")),
            "qty": qty,
            "unit": item.get("unit", "pcs"),
            "price": price,
            "discount_percent": item.get("discount_percent", 0),
            "subtotal": subtotal,
        })

    vendor_name = payload.get("vendor_name", "vendor")

    # Warnings from validation
    warnings = [e["message"] for e in validation.get("errors", []) if not e.get("blocking")]

    return make_response(
        MessageType.ACTION_PREVIEW,
        text=f"Faktur pembelian Rp{grand_total:,.0f} dari {vendor_name} siap dibuat.",
        data={
            "pending_action_id": prepare_result["pending_action_id"],
            "action_type": action_type,
            "expires_at": prepare_result.get("expires_at", ""),
            "confirmation_token": prepare_result.get("confirmation_token", ""),
            "summary": {
                "title": "Faktur Pembelian",
                "vendor": vendor_name,
                "invoice_number": payload.get("invoice_number"),
                "date": payload.get("bill_date"),
                "due_date": payload.get("due_date"),
            },
            "items": items_preview,
            "calculation": {
                "subtotal": grand_total,
                "discount": 0,
                "dpp": grand_total,
                "tax": 0,
                "grand_total": grand_total,
            },
            "journal_preview": journal_preview,
            "assumptions": assumptions,
            "warnings": warnings,
            "side_effects": [],
        },
        pending_action_id=prepare_result["pending_action_id"],
    )



# =============================================================================
# Master Data: parse text, validate, auto-execute (no confirmation needed)
# =============================================================================

# Display names for master data action types
_MASTER_DATA_DISPLAY = {
    "CREATE_VENDOR": "Vendor",
    "CREATE_CUSTOMER": "Customer",
    "CREATE_PRODUCT": "Produk",
}

# Generic/stop words that should NOT be accepted as entity names.
# If the parsed name matches one of these, we ask for clarification.
GENERIC_NAME_WORDS = {
    "baru", "baru.", "item", "produk", "vendor", "pelanggan",
    "customer", "supplier", "barang", "jasa", "pemasok",
    "data", "master", "buat", "tambah", "daftar", "input",
    "catat", "baru!", "baru,", "baru?",
}


def _is_generic_name(name: str) -> bool:
    """Check if the extracted name is too generic to be a real entity name."""
    if not name:
        return True
    cleaned = name.strip().lower().rstrip(".,!?")
    if cleaned in GENERIC_NAME_WORDS:
        return True
    if len(cleaned) < 2:
        return True
    # Multi-word but all words are generic (e.g. "produk baru", "vendor baru")
    words = cleaned.split()
    if all(w in GENERIC_NAME_WORDS for w in words):
        return True
    return False



def _parse_master_data_text(text: str, action_type: str) -> dict:
    """
    Extract master data fields from user text using simple heuristics.
    The planner LLM already classified the intent; we just need to extract the name.
    
    Examples:
        "tambah vendor PT ABC" → {"name": "PT ABC"}
        "daftarkan customer Toko Jaya" → {"name": "Toko Jaya"}
        "tambah produk Kemeja harga beli 110000 harga jual 155000" → {"name": "Kemeja", ...}
    """
    import re as _re
    text_stripped = text.strip()

    if action_type == "CREATE_VENDOR":
        # Remove keyword prefixes to extract the vendor name
        patterns = [
            r"(?:tambah(?:kan)?|daftar(?:kan)?|buat(?:kan)?|input|catat)\s+(?:vendor|supplier)\s+(.+)",
            r"(?:vendor|supplier)\s+baru\s+(.+)",
        ]
        for pat in patterns:
            m = _re.search(pat, text_stripped, _re.IGNORECASE)
            if m:
                return {"vendor_name": m.group(1).strip()}
        # Fallback: use the whole text after removing common verbs
        name = _re.sub(r"^(tambah(?:kan)?|daftar(?:kan)?|buat(?:kan)?|input|catat)\s+", "", text_stripped, flags=_re.IGNORECASE)
        name = _re.sub(r"^(vendor|supplier)\s+", "", name, flags=_re.IGNORECASE)
        return {"vendor_name": name.strip() or text_stripped}

    elif action_type == "CREATE_CUSTOMER":
        patterns = [
            r"(?:tambah(?:kan)?|daftar(?:kan)?|buat(?:kan)?|input|catat)\s+(?:customer|pelanggan)\s+(.+)",
            r"(?:customer|pelanggan)\s+baru\s+(.+)",
        ]
        for pat in patterns:
            m = _re.search(pat, text_stripped, _re.IGNORECASE)
            if m:
                return {"customer_name": m.group(1).strip()}
        name = _re.sub(r"^(tambah(?:kan)?|daftar(?:kan)?|buat(?:kan)?|input|catat)\s+", "", text_stripped, flags=_re.IGNORECASE)
        name = _re.sub(r"^(customer|pelanggan)\s+", "", name, flags=_re.IGNORECASE)
        return {"customer_name": name.strip() or text_stripped}

    elif action_type == "CREATE_PRODUCT":
        # Try to extract product name, buy_price, sell_price
        payload = {}
        # Extract prices
        buy_match = _re.search(r"(?:harga\s+beli|buy\s*price|purchase\s*price|beli)\s*[:\s]*(?:rp\.?\s*)?([\d.,]+)", text_stripped, _re.IGNORECASE)
        sell_match = _re.search(r"(?:harga\s+jual|sell\s*price|sales\s*price|jual)\s*[:\s]*(?:rp\.?\s*)?([\d.,]+)", text_stripped, _re.IGNORECASE)
        if buy_match:
            payload["buy_price"] = float(buy_match.group(1).replace(".", "").replace(",", ""))
        if sell_match:
            payload["sell_price"] = float(sell_match.group(1).replace(".", "").replace(",", ""))

        # Extract unit
        unit_match = _re.search(r"(?:satuan|unit)\s*[:\s]*([\w]+)", text_stripped, _re.IGNORECASE)
        if unit_match:
            payload["unit"] = unit_match.group(1)

        # Extract product name - remove all matched parts and keyword prefixes
        name_text = text_stripped
        # Remove price mentions
        name_text = _re.sub(r"(?:harga\s+beli|buy\s*price|purchase\s*price|beli)\s*[:\s]*(?:rp\.?\s*)?[\d.,]+", "", name_text, flags=_re.IGNORECASE)
        name_text = _re.sub(r"(?:harga\s+jual|sell\s*price|sales\s*price|jual)\s*[:\s]*(?:rp\.?\s*)?[\d.,]+", "", name_text, flags=_re.IGNORECASE)
        name_text = _re.sub(r"(?:satuan|unit)\s*[:\s]*[\w]+", "", name_text, flags=_re.IGNORECASE)
        # Remove keyword prefixes
        name_text = _re.sub(r"^(tambah(?:kan)?|daftar(?:kan)?|buat(?:kan)?|input|catat)\s+", "", name_text.strip(), flags=_re.IGNORECASE)
        name_text = _re.sub(r"^(produk|barang|item)\s+", "", name_text.strip(), flags=_re.IGNORECASE)
        name_text = name_text.strip().strip(",").strip()

        if name_text:
            payload["product_name"] = name_text
        else:
            payload["product_name"] = text_stripped

        return payload

    return {"name": text_stripped}


async def _handle_master_data_action(
    text: str, action_type: str, ctx: dict, planner
) -> ChatMessageResponse:
    """
    Handle master data creation (vendor, customer, product).
    
    Flow: parse text → validate → prepare → auto-execute (no confirmation).
    Master data is LOW risk, so we skip the confirmation step.
    """
    executor = get_action_executor_client()
    display_name = _MASTER_DATA_DISPLAY.get(action_type, action_type)

    # Step 1: Parse the text into a payload
    payload = _parse_master_data_text(text, action_type)
    logger.info(f"Master data parsed: action={action_type}, payload={payload}")

    # Extract the name for display purposes
    entity_name = (
        payload.get("vendor_name")
        or payload.get("customer_name")
        or payload.get("product_name")
        or payload.get("name")
        or ""
    )

    # Guard: if the extracted name is generic/empty, ask for clarification
    if _is_generic_name(entity_name):
        type_label = {
            "CREATE_VENDOR": "vendor",
            "CREATE_CUSTOMER": "pelanggan",
            "CREATE_PRODUCT": "produk",
        }.get(action_type, "data")
        return make_response(
            MessageType.CLARIFICATION,
            text=(
                f"Saya perlu tahu nama {type_label} yang ingin Anda daftarkan. "
                f"Silakan berikan detail lengkap, contoh:\n"
                f"\u2022 Nama {type_label}\n"
                f"\u2022 Alamat/kontak (opsional)"
            ),
            data={
                "question": f"Siapa nama {type_label} yang ingin didaftarkan?",
                "options": [],
            },
        )

    # Step 2: Validate via ActionValidator (lightweight for master data)
    validator = get_action_validator_client()
    validation = await validator.validate_action(
        tenant_id=ctx["tenant_id"],
        user_id=ctx["user_id"],
        action_id=action_type,
        action_type=action_type,
        category="MASTER_DATA",
        draft_payload=payload,
    )

    if not validation["valid"]:
        blocking_errors = [e for e in validation["errors"] if e.get("blocking")]
        if blocking_errors:
            return make_response(
                MessageType.VALIDATION_ERROR,
                text=f"Gagal membuat {display_name}. Periksa data berikut:",
                data={
                    "errors": blocking_errors,
                    "suggestions": ["Perbaiki data dan coba lagi."],
                },
            )

    # Step 3: Prepare action (creates pending_action for audit trail)
    prepare_result = await executor.prepare_action(
        tenant_id=ctx["tenant_id"],
        user_id=ctx["user_id"],
        action_type=action_type,
        category="MASTER_DATA",
        draft_payload=payload,
        assumptions=[f"{display_name} baru: {entity_name}"],
    )

    if not prepare_result["success"]:
        return make_response(
            MessageType.VALIDATION_ERROR,
            text=prepare_result.get("error_message", f"Gagal menyiapkan {display_name}."),
            data={
                "errors": [{"layer": "EXECUTOR", "code": "PREPARE_FAILED", "message": prepare_result.get("error_message", ""), "blocking": True}],
                "suggestions": ["Coba lagi."],
            },
        )

    pending_action_id = prepare_result["pending_action_id"]
    confirmation_token = prepare_result.get("confirmation_token", "")

    # Step 4: AUTO-EXECUTE (master data is low risk, no user confirmation needed)
    execute_result = await executor.execute_action(
        tenant_id=ctx["tenant_id"],
        user_id=ctx["user_id"],
        pending_action_id=pending_action_id,
        confirmation_token=confirmation_token,
    )

    if execute_result.get("success"):
        action_result = execute_result.get("result", {})
        entity_id = action_result.get("entity_id", "") if action_result else ""
        entity_type = action_result.get("entity_type", display_name.lower()) if action_result else display_name.lower()

        entities_created = []
        if action_result:
            entities_created.append({
                "type": entity_type,
                "id": entity_id,
                "label": entity_name,
            })

        return make_response(
            MessageType.ACTION_RESULT,
            text=f"{display_name} \"{entity_name}\" berhasil dibuat.",
            data={
                "success": True,
                "action_type": action_type,
                "entities_created": entities_created,
                "impact": {},
            },
        )
    else:
        error_msg = execute_result.get("error_message", f"Gagal membuat {display_name}.")
        return make_response(
            MessageType.VALIDATION_ERROR,
            text=error_msg,
            data={
                "errors": [{"layer": "EXECUTION", "code": execute_result.get("error_code", "UNKNOWN"), "message": error_msg, "blocking": True}],
                "suggestions": ["Coba lagi."],
            },
        )


async def _handle_unclear_intent(text: str, ctx: dict, planner) -> ChatMessageResponse:
    """Handle UNCLEAR intent - try entity extraction, then conversational response."""
    text_lower = text.lower()
    has_entity = any(kw in text_lower for kw in [
        "vendor", "supplier", "produk", "barang", "item", "stok",
        "sudah ada", "terdaftar", "ada gak", "ada nggak",
    ])

    if has_entity:
        entities = await planner.extract_entities(text)
        has_results = entities.get("vendors") or entities.get("products")
        if has_results:
            ctx_parts = []
            for v in entities.get("vendors", []):
                ctx_parts.append(f"Vendor '{v}' dicari.")
            for p in entities.get("products", []):
                ctx_parts.append(f"Produk '{p}' dicari.")
            response = await planner.generate_response(
                text,
                context=f"Hasil cek: {' | '.join(ctx_parts)}. Jawab natural."
            )
            return make_response(MessageType.TEXT, text=response or "Ini yang saya temukan.")

    response = await planner.generate_response(text)
    return make_response(
        MessageType.TEXT,
        text=response or "Maaf, saya kurang paham. Bisa ceritakan lebih detail?"
    )



async def _handle_master_data_structured(
    action_type: str, raw_data: dict, ctx: dict
) -> ChatMessageResponse:
    """
    Handle structured master data creation (from frontend form/OCR).

    Unlike _handle_master_data_action (which parses free text), this receives
    structured data directly. Flow: validate → prepare → auto-execute.
    Master data is LOW risk, so we skip the confirmation step.
    """
    executor = get_action_executor_client()
    validator = get_action_validator_client()
    display_name = _MASTER_DATA_DISPLAY.get(action_type, action_type)

    # raw_data is already structured (e.g. {"vendor_name": "PT ABC"})
    payload = raw_data

    # Extract the name for display purposes
    entity_name = (
        payload.get("vendor_name")
        or payload.get("customer_name")
        or payload.get("product_name")
        or payload.get("name")
        or ""
    )

    # Guard: if the extracted name is generic/empty, ask for clarification
    if _is_generic_name(entity_name):
        type_label = {
            "CREATE_VENDOR": "vendor",
            "CREATE_CUSTOMER": "pelanggan",
            "CREATE_PRODUCT": "produk",
        }.get(action_type, "data")
        return make_response(
            MessageType.CLARIFICATION,
            text=(
                f"Saya perlu tahu nama {type_label} yang ingin Anda daftarkan. "
                f"Silakan berikan detail lengkap, contoh:\n"
                f"\u2022 Nama {type_label}\n"
                f"\u2022 Alamat/kontak (opsional)"
            ),
            data={
                "question": f"Siapa nama {type_label} yang ingin didaftarkan?",
                "options": [],
            },
        )

    logger.info(f"Master data structured: action={action_type}, payload={payload}")

    # Step 1: Validate via ActionValidator
    validation = await validator.validate_action(
        tenant_id=ctx["tenant_id"],
        user_id=ctx["user_id"],
        action_id=action_type,
        action_type=action_type,
        category="MASTER_DATA",
        draft_payload=payload,
    )

    if not validation["valid"]:
        blocking_errors = [e for e in validation["errors"] if e.get("blocking")]
        if blocking_errors:
            return make_response(
                MessageType.VALIDATION_ERROR,
                text=f"Gagal membuat {display_name}. Periksa data berikut:",
                data={
                    "errors": blocking_errors,
                    "suggestions": ["Perbaiki data dan coba lagi."],
                },
            )

    # Step 2: Prepare action (creates pending_action for audit trail)
    prepare_result = await executor.prepare_action(
        tenant_id=ctx["tenant_id"],
        user_id=ctx["user_id"],
        action_type=action_type,
        category="MASTER_DATA",
        draft_payload=payload,
        assumptions=[f"{display_name} baru: {entity_name}"],
    )

    if not prepare_result["success"]:
        return make_response(
            MessageType.VALIDATION_ERROR,
            text=prepare_result.get("error_message", f"Gagal menyiapkan {display_name}."),
            data={
                "errors": [{"layer": "EXECUTOR", "code": "PREPARE_FAILED", "message": prepare_result.get("error_message", ""), "blocking": True}],
                "suggestions": ["Coba lagi."],
            },
        )

    pending_action_id = prepare_result["pending_action_id"]
    confirmation_token = prepare_result.get("confirmation_token", "")

    # Step 3: AUTO-EXECUTE (master data is low risk, no user confirmation needed)
    execute_result = await executor.execute_action(
        tenant_id=ctx["tenant_id"],
        user_id=ctx["user_id"],
        pending_action_id=pending_action_id,
        confirmation_token=confirmation_token,
    )

    if execute_result.get("success"):
        action_result = execute_result.get("result", {})
        entity_id = action_result.get("entity_id", "") if action_result else ""
        entity_type = action_result.get("entity_type", display_name.lower()) if action_result else display_name.lower()

        entities_created = []
        if action_result:
            entities_created.append({
                "type": entity_type,
                "id": entity_id,
                "label": entity_name,
            })

        return make_response(
            MessageType.ACTION_RESULT,
            text=f"{display_name} \"{entity_name}\" berhasil dibuat.",
            data={
                "success": True,
                "action_type": action_type,
                "entities_created": entities_created,
                "impact": {},
            },
        )
    else:
        error_msg = execute_result.get("error_message", f"Gagal membuat {display_name}.")
        return make_response(
            MessageType.VALIDATION_ERROR,
            text=error_msg,
            data={
                "errors": [{"layer": "EXECUTION", "code": execute_result.get("error_code", "UNKNOWN"), "message": error_msg, "blocking": True}],
                "suggestions": ["Coba lagi."],
            },
        )


# =============================================================================
# POST /message/structured - Create action from structured data
# =============================================================================
@router.post("/message/structured", response_model=ChatMessageResponse)
async def send_structured_message(request: Request, body: dict):
    """
    Process a structured action request (e.g., from OCR/LLM output).
    Full pipeline: validate → prepare → preview.
    """
    ctx = get_user_context(request)
    planner = get_action_planner_client()

    action_type = body.get("action_type", "")
    raw_data = body.get("data", {})

    MASTER_DATA_ACTIONS = {"CREATE_VENDOR", "CREATE_CUSTOMER", "CREATE_PRODUCT"}

    if action_type in MASTER_DATA_ACTIONS:
        # Master data: validate → prepare → auto-execute (no confirmation needed)
        return await _handle_master_data_structured(action_type, raw_data, ctx)
    elif action_type == "CREATE_PURCHASE_INVOICE":
        # Document: validate → prepare → preview (needs user confirmation)
        return await _validate_and_prepare(
            action_type=action_type,
            category="DOCUMENT",
            payload=raw_data,
            ctx=ctx,
            planner=planner,
            original_text="",
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported action_type: {action_type}")


# =============================================================================
# POST /confirm - Execute pending action
# =============================================================================
@router.post("/confirm", response_model=ChatMessageResponse)
async def confirm_action(request: Request, body: ConfirmActionRequest):
    """Confirm and execute a pending action via ActionExecutor."""
    ctx = get_user_context(request)
    executor = get_action_executor_client()

    result = await executor.execute_action(
        tenant_id=ctx["tenant_id"],
        user_id=ctx["user_id"],
        pending_action_id=body.pending_action_id,
    )

    if result.get("success"):
        action_result = result.get("result", {})
        entity_label = ""
        if action_result:
            entity_label = f"{action_result.get('entity_type', '')} {action_result.get('entity_number', '')}"

        return make_response(
            MessageType.ACTION_RESULT,
            text=f"Berhasil! {entity_label or 'Aksi'} telah dibuat.",
            data={
                "success": True,
                "action_type": action_result.get("entity_type", "") if action_result else "",
                "entities_created": [action_result] if action_result else [],
                "impact": action_result.get("impact", {}) if action_result else {},
            },
        )
    else:
        error_code = result.get("error_code", "UNKNOWN")
        error_msg = result.get("error_message", "Terjadi kesalahan.")

        if error_code == "EXPIRED":
            return make_response(
                MessageType.VALIDATION_ERROR,
                text=error_msg,
                data={
                    "errors": [{"layer": "POLICY", "code": "EXPIRED", "message": error_msg}],
                    "suggestions": ["Kirim ulang data."],
                },
            )

        return make_response(
            MessageType.VALIDATION_ERROR,
            text=error_msg,
            data={
                "errors": [{"layer": "EXECUTION", "code": error_code, "message": error_msg}],
                "suggestions": ["Coba lagi."],
            },
        )


# =============================================================================
# POST /cancel - Cancel pending action
# =============================================================================
@router.post("/cancel", response_model=ChatMessageResponse)
async def cancel_action(request: Request, body: CancelActionRequest):
    """Cancel a pending action via ActionExecutor."""
    ctx = get_user_context(request)
    executor = get_action_executor_client()

    try:
        result = await executor.cancel_action(
            tenant_id=ctx["tenant_id"],
            user_id=ctx["user_id"],
            pending_action_id=body.pending_action_id,
        )
    except Exception as e:
        return make_response(MessageType.TEXT, text="Aksi tidak ditemukan.")

    if result.get("success"):
        return make_response(MessageType.TEXT, text="Aksi dibatalkan.")
    else:
        return make_response(MessageType.TEXT, text=result.get("message", "Aksi tidak ditemukan atau sudah diproses."))


# =============================================================================
# GET /status/{pending_action_id}
# =============================================================================
@router.get("/status/{pending_action_id}", response_model=ActionStatusResponse)
async def get_action_status(request: Request, pending_action_id: str):
    """Poll the status of a pending action via ActionExecutor."""
    ctx = get_user_context(request)
    executor = get_action_executor_client()

    result = await executor.get_action_status(
        tenant_id=ctx["tenant_id"],
        action_id=pending_action_id,
    )

    status = result.get("status", "UNKNOWN").lower()

    return ActionStatusResponse(
        pending_action_id=pending_action_id,
        status=status,
        message=result.get("error_message"),
        data={"action_id": result.get("action_id")} if result.get("action_id") else None,
    )
