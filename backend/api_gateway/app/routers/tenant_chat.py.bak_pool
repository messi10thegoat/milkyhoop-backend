"""
Tenant Chat Router - Business Query Mode
Handles: Financial reports, analytics, customer data queries
+ Sales Intent Detection for Hybrid Conversational POS
"""

import grpc
import logging
import re
import time
from typing import Dict, Optional, Any
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.api_gateway.libs.milkyhoop_protos import (
    tenant_orchestrator_pb2,
    tenant_orchestrator_pb2_grpc,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# =====================================================
# SALES INTENT PARSER - HYBRID CONVERSATIONAL POS
# =====================================================


def parse_sales_intent(message: str) -> Optional[Dict[str, Any]]:
    """
    Parse sales intent from natural language.

    Examples:
    - "Jual Aqua 2 botol cash" → opens POS with Aqua x2, tunai
    - "Bu Siti beli beras 25kg, bon" → opens POS with beras x25, hutang, customer Bu Siti
    - "Transaksi Indomie 5 bungkus" → opens POS with Indomie x5

    Returns:
        dict with intent data if sales detected, None otherwise
    """
    message_lower = message.lower().strip()

    # Sales trigger keywords
    SALES_TRIGGERS = [
        r"^jual\s+",  # "jual ..."
        r"^transaksi\s+",  # "transaksi ..."
        r"^penjualan\s+",  # "penjualan ..."
        r"^catat\s+penjualan",  # "catat penjualan ..."
        r"\bbeli\s+",  # "... beli ..." (customer buying)
        r"^kasir\s+",  # "kasir ..."
    ]

    # Check if message matches sales pattern
    is_sales = False
    for pattern in SALES_TRIGGERS:
        if re.search(pattern, message_lower):
            is_sales = True
            break

    if not is_sales:
        return None

    # Words to skip when extracting products
    skip_words = {
        "jual",
        "beli",
        "transaksi",
        "kasir",
        "penjualan",
        "catat",
        "cash",
        "tunai",
        "qris",
        "bon",
        "hutang",
        "transfer",
        "bu",
        "pak",
        "ibu",
        "bapak",
        "mbak",
        "mas",
        "dengan",
        "untuk",
    }

    # Clean message - remove trigger words and customer name prefix
    cleaned_message = message_lower
    # Remove sales triggers
    for trigger in ["jual ", "transaksi ", "penjualan ", "catat penjualan ", "kasir "]:
        if cleaned_message.startswith(trigger):
            cleaned_message = cleaned_message[len(trigger) :]
            break

    # Remove customer prefix like "bu siti beli"
    customer_beli_match = re.match(
        r"^(bu|pak|ibu|bapak|mbak|mas)\s+\w+\s+beli\s+", cleaned_message
    )
    if customer_beli_match:
        cleaned_message = cleaned_message[customer_beli_match.end() :]

    # Extract items from cleaned message
    items = []
    seen_products = set()  # Avoid duplicates

    # Unit pattern for reuse
    UNITS = r"(pcs|botol|bungkus|kg|gram|g|dus|box|karton|lusin|liter|l|pack|sachet|biji|buah|unit)"

    def add_item(product_name, quantity, unit):
        """Helper to add item if valid"""
        # Clean product name - remove skip words and extra spaces
        product_words = product_name.split()
        product_words = [w for w in product_words if w.lower() not in skip_words]
        product_name = " ".join(product_words).strip()

        # Skip if empty or already seen
        if not product_name or product_name.lower() in seen_products:
            return False

        seen_products.add(product_name.lower())
        items.append(
            {
                "productQuery": product_name.title(),  # Capitalize
                "qty": int(quantity) if quantity == int(quantity) else quantity,
                "unit": unit,
            }
        )
        return True

    # ========== PARSING APPROACH: Check for separators first ==========
    has_separators = bool(re.search(r"[,\n]|\s+dan\s+|\s+and\s+", cleaned_message))

    if has_separators:
        # Split by comma, newline, or "dan"/"and"
        segments = re.split(r"[,\n]|\s+dan\s+|\s+and\s+", cleaned_message)

        for segment in segments:
            segment = segment.strip()
            if not segment:
                continue

            # Remove payment keywords from segment for item parsing
            segment_clean = segment
            for pay_word in [
                "cash",
                "tunai",
                "qris",
                "bon",
                "hutang",
                "transfer",
                "tf",
                "kontan",
            ]:
                segment_clean = re.sub(
                    r"\b" + pay_word + r"\b", "", segment_clean, flags=re.IGNORECASE
                )
            segment_clean = segment_clean.strip()

            if not segment_clean:
                continue

            # Pattern A: product_name quantity unit (e.g., "esse 5", "aqua 2 botol")
            pattern_a = (
                r"^([A-Za-z][A-Za-z\s]*?)\s+(\d+(?:[.,]\d+)?)\s*" + UNITS + r"?$"
            )
            match_a = re.match(pattern_a, segment_clean, re.IGNORECASE)

            if match_a:
                product_name = match_a.group(1).strip()
                quantity = float(match_a.group(2).replace(",", "."))
                unit = match_a.group(3) if match_a.group(3) else "pcs"
                if add_item(product_name, quantity, unit):
                    continue

            # Pattern B: quantity unit product_name (e.g., "5 esse", "2 botol aqua")
            # IMPORTANT: Product name should NOT contain digits (stop at first digit)
            pattern_b = r"^(\d+(?:[.,]\d+)?)\s*" + UNITS + r"?\s+([A-Za-z][A-Za-z\s]*)$"
            match_b = re.match(pattern_b, segment_clean, re.IGNORECASE)

            if match_b:
                quantity = float(match_b.group(1).replace(",", "."))
                unit = match_b.group(2) if match_b.group(2) else "pcs"
                product_name = match_b.group(3).strip()
                if add_item(product_name, quantity, unit):
                    continue

            # Pattern C: Just product name (e.g., "esse") - default qty=1
            pattern_c = r"^([A-Za-z][A-Za-z\s]*)$"
            match_c = re.match(pattern_c, segment_clean, re.IGNORECASE)

            if match_c:
                product_name = match_c.group(1).strip()
                add_item(product_name, 1, "pcs")

    # ========== SPACE-SEPARATED: Parse "5 esse 6 kongbap" or "esse 5 kongbap 6" ==========
    if not items:
        # Try to parse "5 esse 6 kongbap" style (alternating qty-product)
        # This handles cases without explicit separators
        tokens = cleaned_message.split()
        i = 0
        while i < len(tokens):
            token = tokens[i]

            # Skip payment/skip words
            if token.lower() in skip_words or token.lower() in [
                "cash",
                "tunai",
                "qris",
                "bon",
                "hutang",
                "transfer",
                "tf",
            ]:
                i += 1
                continue

            # Check if current token is a number
            qty_match = re.match(r"^(\d+(?:[.,]\d+)?)$", token)
            if qty_match and i + 1 < len(tokens):
                # qty-first: "5 esse"
                quantity = float(qty_match.group(1).replace(",", "."))
                # Check next token for unit or product
                next_token = tokens[i + 1]
                unit_match = re.match(UNITS, next_token, re.IGNORECASE)
                if unit_match and i + 2 < len(tokens):
                    # "5 botol esse"
                    unit = unit_match.group(1)
                    product_name = tokens[i + 2]
                    add_item(product_name, quantity, unit)
                    i += 3
                else:
                    # "5 esse"
                    product_name = next_token
                    # Check if product_name is actually a unit
                    if not re.match(UNITS, product_name, re.IGNORECASE):
                        add_item(product_name, quantity, "pcs")
                    i += 2
            elif re.match(r"^[A-Za-z]", token):
                # product-first: "esse 5"
                product_name = token
                if i + 1 < len(tokens):
                    next_token = tokens[i + 1]
                    qty_match = re.match(r"^(\d+(?:[.,]\d+)?)$", next_token)
                    if qty_match:
                        quantity = float(qty_match.group(1).replace(",", "."))
                        # Check for unit after quantity
                        if i + 2 < len(tokens):
                            unit_match = re.match(UNITS, tokens[i + 2], re.IGNORECASE)
                            if unit_match:
                                add_item(product_name, quantity, unit_match.group(1))
                                i += 3
                                continue
                        add_item(product_name, quantity, "pcs")
                        i += 2
                        continue
                # No quantity found - default to 1
                add_item(product_name, 1, "pcs")
                i += 1
            else:
                i += 1

    # Pattern 3: Product name only WITHOUT quantity (e.g., "jual esse", "kasir aqua")
    # Default quantity to 1 pcs
    if not items:
        # Remove payment method keywords from cleaned message
        product_only = cleaned_message
        for payment_word in [
            "cash",
            "tunai",
            "qris",
            "bon",
            "hutang",
            "transfer",
            "tf",
        ]:
            product_only = re.sub(r"\b" + payment_word + r"\b", "", product_only)
        product_only = product_only.strip()

        # Extract product name (alphabetic characters, may include spaces)
        product_match = re.match(r"^([A-Za-z][A-Za-z0-9\s]{0,30})", product_only)
        if product_match:
            product_name = product_match.group(1).strip()
            # Clean and add with default qty=1
            if product_name and product_name.lower() not in skip_words:
                add_item(product_name, 1, "pcs")

    # Extract payment method
    payment_method = None
    if re.search(r"\b(cash|tunai|kontan)\b", message_lower):
        payment_method = "tunai"
    elif re.search(r"\b(qris|qr|scan)\b", message_lower):
        payment_method = "qris"
    elif re.search(r"\b(bon|hutang|kredit|piutang|nanti)\b", message_lower):
        payment_method = "hutang"
    elif re.search(r"\b(transfer|tf|bank)\b", message_lower):
        payment_method = "transfer"

    # Extract customer name (Bu/Pak/Ibu/Bapak + name)
    customer_name = None
    customer_match = re.search(
        r"\b(bu|pak|ibu|bapak|mbak|mas)\s+([A-Za-z]+)", message_lower
    )
    if customer_match:
        title = customer_match.group(1).title()
        name = customer_match.group(2).title()
        customer_name = f"{title} {name}"

    # Calculate confidence
    confidence = 0.0
    if items:
        confidence += 0.5  # Has items
    if payment_method:
        confidence += 0.25  # Has payment method
    if customer_name:
        confidence += 0.15  # Has customer
    if is_sales:
        confidence += 0.1  # Has sales keyword

    # Only return if we have at least items
    if not items:
        return None

    return {
        "intent": "sales_pos",
        "items": items,
        "payment_method": payment_method,
        "customer_name": customer_name,
        "confidence": min(confidence, 1.0),
        "raw_message": message,
    }


class TenantChatRequest(BaseModel):
    message: str
    session_id: str = ""
    conversation_context: str = ""


class TenantChatResponse(BaseModel):
    status: str
    milky_response: str
    intent: str = ""
    trace_id: str = ""


@router.get("/{tenant_id}/info")
async def get_tenant_info(tenant_id: str):
    """
    Get Tenant Public Info

    Purpose: Fetch tenant display_name, menu_items for dynamic UI
    Authentication: NOT required (public endpoint)
    """
    import asyncpg

    try:
        # Connect to local PostgreSQL
        conn = await asyncpg.connect(
            host="postgres",  # Docker service name
            port=5432,
            user="postgres",
            password="Proyek771977",
            database="milkydb",
        )

        # Query tenant data
        row = await conn.fetchrow(
            'SELECT id, alias, display_name, menu_items, status FROM "Tenant" WHERE id = $1',
            tenant_id,
        )

        await conn.close()

        if not row:
            raise HTTPException(
                status_code=404, detail=f"Tenant '{tenant_id}' not found"
            )

        return {
            "status": "success",
            "data": {
                "tenant_id": row["id"],
                "alias": row["alias"],
                "display_name": row["display_name"],
                "menu_items": row["menu_items"],
                "status": row["status"],
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch tenant info: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch tenant info")


@router.post("/{tenant_id}/chat")
async def tenant_chat(
    tenant_id: str, request_body: TenantChatRequest, request: Request
):
    """
    Tenant Mode Chat Endpoint

    Purpose: Business queries (financial, analytics, customer data)
    + Sales Intent Detection for Hybrid Conversational POS
    Authentication: JWT required
    """
    start_time = time.time()

    try:
        # Get user_id from JWT context (set by AuthMiddleware)
        user_id = request.state.user.get("user_id", "")

        print("[HYBRID-POS] ===== REQUEST START =====", flush=True)
        print(f"[HYBRID-POS] tenant={tenant_id} | user={user_id}", flush=True)
        print(f"[HYBRID-POS] message='{request_body.message}'", flush=True)
        print(f"[HYBRID-POS] session_id='{request_body.session_id}'", flush=True)

        # ========== PHASE 0: SALES INTENT DETECTION ==========
        # Check for sales intent FIRST - shortcut to POS
        sales_intent = parse_sales_intent(request_body.message)
        print(f"[HYBRID-POS] sales_intent result: {sales_intent}", flush=True)

        if sales_intent and sales_intent.get("confidence", 0) >= 0.5:
            # SALES INTENT DETECTED - Return action payload for frontend
            print("[HYBRID-POS] ✅ SALES INTENT DETECTED!", flush=True)
            print(
                f"[HYBRID-POS] items={sales_intent.get('items', [])} | payment={sales_intent.get('payment_method')}",
                flush=True,
            )

            # Build friendly response message
            items_text = ", ".join(
                [
                    f"{item['productQuery']} x{item['qty']}"
                    for item in sales_intent.get("items", [])
                ]
            )
            payment_text = sales_intent.get("payment_method", "")
            customer_text = sales_intent.get("customer_name", "")

            response_parts = ["Siap! Membuka POS"]
            if items_text:
                response_parts.append(f"dengan {items_text}")
            if payment_text:
                response_parts.append(f"({payment_text})")
            if customer_text:
                response_parts.append(f"untuk {customer_text}")

            milky_response = " ".join(response_parts) + "..."

            # Calculate processing time
            processing_time = round((time.time() - start_time) * 1000, 2)

            # Return with action payload for frontend
            response_dict = {
                "status": "success",
                "milky_response": milky_response,
                "intent": "sales_pos",
                "trace_id": "",
                "action": {
                    "type": "open_pos",
                    "payload": {
                        "items": sales_intent.get("items", []),
                        "paymentMethod": sales_intent.get("payment_method"),
                        "customerName": sales_intent.get("customer_name"),
                        "navigateTo": "pos",  # Always go to POS for user validation
                    },
                },
                "confidence_metadata": {
                    "confidence_score": sales_intent.get("confidence", 0),
                    "route_taken": "sales_intent_shortcut",
                    "processing_time_ms": processing_time,
                },
            }
            print("[HYBRID-POS] ✅ RETURNING with action.type=open_pos", flush=True)
            print(
                f"[HYBRID-POS] response_dict keys: {list(response_dict.keys())}",
                flush=True,
            )
            print(f"[HYBRID-POS] action: {response_dict['action']}", flush=True)
            print("[HYBRID-POS] ===== REQUEST END (SALES PATH) =====", flush=True)
            return response_dict

        # ========== CONTINUE WITH NORMAL CHAT FLOW ==========
        print("[HYBRID-POS] ❌ No sales intent - going to gRPC path", flush=True)
        # Connect to tenant_orchestrator gRPC
        channel = grpc.aio.insecure_channel("tenant_orchestrator:5017")
        stub = tenant_orchestrator_pb2_grpc.TenantOrchestratorStub(channel)

        # Build gRPC request
        grpc_request = tenant_orchestrator_pb2.ProcessTenantQueryRequest(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=request_body.session_id,
            message=request_body.message,
            conversation_context=request_body.conversation_context,
        )

        # Call tenant_orchestrator
        grpc_response = await stub.ProcessTenantQuery(grpc_request)

        # Close channel
        await channel.close()

        # Return response
        print("[HYBRID-POS] ===== REQUEST END (gRPC PATH) =====", flush=True)
        return TenantChatResponse(
            status=grpc_response.status,
            milky_response=grpc_response.milky_response,
            intent=grpc_response.intent,
            trace_id=grpc_response.trace_id,
        )

    except grpc.RpcError as e:
        logger.error(f"gRPC error: {e.code()} - {e.details()}")
        raise HTTPException(status_code=500, detail=f"Service error: {e.details()}")

    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
