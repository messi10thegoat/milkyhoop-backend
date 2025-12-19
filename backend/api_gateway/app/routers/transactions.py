"""
Transaction Router - Form-based Transaction Recording
Converts structured form input to orchestrator query
"""
import grpc
import json
import time
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, Literal, List
import logging
import uuid

from backend.api_gateway.libs.milkyhoop_protos import (
    tenant_orchestrator_pb2,
    tenant_orchestrator_pb2_grpc,
)
from backend.api_gateway.libs.milkyhoop_protos import (
    conversation_service_pb2,
    conversation_service_pb2_grpc,
)
from backend.api_gateway.app.utils.conversational_parser import (
    parse_conversational_input,
    validate_parsed_input,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================
# CHAT HISTORY PERSISTENCE HELPER
# ============================================
async def save_to_chat_history(
    user_id: str,
    tenant_id: str,
    message: str,
    response: str,
    intent: str = "transaction",
    metadata: dict = None,
) -> bool:
    """
    Save message to chat_messages table via conversation_service.
    Non-blocking: failures are logged but don't break the transaction.
    """
    try:
        channel = grpc.aio.insecure_channel("conversation_service:5002")
        stub = conversation_service_pb2_grpc.ConversationServiceStub(channel)

        request = conversation_service_pb2.SaveMessageRequest(
            user_id=user_id,
            tenant_id=tenant_id,
            message=message,
            response=response,
            intent=intent,
            metadata_json=json.dumps(metadata or {}),
        )

        save_response = await stub.SaveMessage(request)
        await channel.close()

        if save_response.status == "success":
            logger.info(f"[ChatHistory] Message saved: {save_response.message_id}")
            return True
        else:
            logger.warning(
                f"[ChatHistory] Save returned non-success: {save_response.status}"
            )
            return False

    except Exception as e:
        logger.warning(f"[ChatHistory] Save failed (non-blocking): {e}")
        return False


class PurchaseTransactionRequest(BaseModel):
    """Purchase transaction from frontend form"""

    product_name: str
    quantity: int
    unit: str  # pcs, kg, karton, etc.
    price_per_unit: int  # in rupiah (integer)
    # Accept both lowercase (backend) and uppercase (Kulakan frontend) payment methods
    payment_method: Literal[
        "tunai", "transfer", "kredit", "CASH", "TRANSFER", "TEMPO"
    ] = "tunai"
    vendor_name: Optional[str] = None
    notes: Optional[str] = None
    # Discount & PPN fields (V005)
    discount_type: Optional[Literal["amount", "percentage"]] = None
    discount_value: Optional[float] = 0
    include_vat: bool = False
    # Additional metadata
    units_per_pack: Optional[int] = None
    purchase_type: Optional[str] = None
    purchase_date: Optional[str] = None
    due_date: Optional[str] = None
    # HPP & Margin fields (V006)
    hpp_per_unit: Optional[float] = None  # HPP per satuan kecil
    harga_jual: Optional[float] = None  # Selling price per unit
    margin: Optional[float] = None  # Profit margin
    margin_percent: Optional[float] = None  # Margin percentage
    retail_unit: Optional[
        str
    ] = None  # Retail unit for HPP display (e.g., "pcs" instead of "dus")
    # Kulakan frontend additional fields (aliases and extras)
    transaction_type: Optional[str] = None  # 'pembelian'
    product_barcode: Optional[str] = None  # barcode from scanner
    supplier_name: Optional[str] = None  # alias for vendor_name
    catatan: Optional[str] = None  # alias for notes (Indonesian)
    content_unit: Optional[
        str
    ] = None  # unit for contents (e.g., 'pcs' in 'karton isi 24 pcs')
    total_amount: Optional[int] = None  # total price (quantity * price_per_unit)
    harga_pokok: Optional[float] = None  # alias for hpp_per_unit
    has_discount: Optional[bool] = False  # whether discount is applied
    has_ppn: Optional[bool] = False  # alias for include_vat
    is_wholesale: Optional[bool] = False  # wholesale mode
    is_tempo: Optional[bool] = False  # credit payment
    is_new_product: Optional[bool] = False  # new product flag
    category: Optional[str] = None  # product category


class TransactionResponse(BaseModel):
    status: str
    message: str
    transaction_id: Optional[str] = None


def format_rupiah(amount: int) -> str:
    """Format integer to rupiah string (e.g., 50000 → '50rb' or '50ribu')"""
    if amount >= 1_000_000:
        return f"{amount // 1_000_000}jt"
    elif amount >= 1_000:
        return f"{amount // 1_000}rb"
    else:
        return str(amount)


@router.post("/purchase", response_model=TransactionResponse)
async def create_purchase_transaction(
    request: Request, body: PurchaseTransactionRequest
):
    """
    Create purchase transaction from form

    Converts structured form data to natural language message,
    then routes to tenant_orchestrator for processing.

    Example:
        Input: {product_name: "Sepatu", quantity: 10, unit: "pcs", price_per_unit: 50000}
        Message: "Beli 10 pcs Sepatu harga 50rb per pcs bayar tunai"
    """
    t_request_start = time.perf_counter()

    try:
        # ===== 1. GET USER FROM AUTH MIDDLEWARE =====
        # AuthMiddleware already validates token and sets request.state.user
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        user = request.state.user
        tenant_id = user.get("tenant_id")
        user_id = user.get("user_id")

        if not tenant_id or not user_id:
            raise HTTPException(
                status_code=401,
                detail="Invalid user context: missing tenant_id or user_id",
            )

        logger.info(f"Purchase request from user {user_id} in tenant {tenant_id}")

        # ===== 2. NORMALIZE FIELD ALIASES =====
        # Handle both frontend (Kulakan) and backend naming conventions
        payment_method_map = {
            "CASH": "tunai",
            "TRANSFER": "transfer",
            "TEMPO": "kredit",
            "tunai": "tunai",
            "transfer": "transfer",
            "kredit": "kredit",
        }
        normalized_payment = payment_method_map.get(body.payment_method, "tunai")
        vendor = body.vendor_name or body.supplier_name or ""
        notes_text = body.notes or body.catatan or ""
        use_vat = body.include_vat or body.has_ppn or False
        hpp = body.hpp_per_unit or body.harga_pokok

        # ===== 3. BUILD NATURAL LANGUAGE MESSAGE =====
        # Convert form to conversational message that orchestrator understands
        # Add [FORM] flag so orchestrator skips clarification flow

        price_str = format_rupiah(body.price_per_unit)

        # Build base message with [FORM] flag
        message_parts = [
            f"[FORM] Beli {body.quantity} {body.unit} {body.product_name}",
            f"harga {price_str} per {body.unit}",
        ]

        # Add payment method
        if normalized_payment == "tunai":
            message_parts.append("bayar tunai")
        elif normalized_payment == "transfer":
            message_parts.append("bayar transfer")
        elif normalized_payment == "kredit":
            message_parts.append("bayar kredit")

        # Add vendor if specified
        if vendor:
            message_parts.append(f"dari {vendor}")

        # Add notes if specified
        if notes_text:
            message_parts.append(f"catatan: {notes_text}")

        message = " ".join(message_parts)

        logger.info(f"Generated message: {message}")

        # ===== 3. CALL TENANT ORCHESTRATOR =====
        session_id = f"form_{uuid.uuid4().hex[:12]}"

        # Connect to tenant_orchestrator gRPC
        channel = grpc.aio.insecure_channel("tenant_orchestrator:5017")
        stub = tenant_orchestrator_pb2_grpc.TenantOrchestratorStub(channel)

        # NEW: Pass structured form data as JSON in conversation_context
        # This allows orchestrator to extract data directly without LLM parsing
        # Convert frontend discount_type "amount" to backend "nominal"
        backend_discount_type = None
        if body.discount_type == "amount":
            backend_discount_type = "nominal"
        elif body.discount_type == "percentage":
            backend_discount_type = "percentage"

        form_data_json = json.dumps(
            {
                "form_data": {
                    "product_name": body.product_name,
                    "product_barcode": body.product_barcode or "",
                    "quantity": body.quantity,
                    "unit": body.unit,
                    "price_per_unit": body.price_per_unit,
                    "total": body.total_amount or (body.quantity * body.price_per_unit),
                    "payment_method": normalized_payment,  # Use normalized value
                    "vendor_name": vendor,  # Use normalized value (vendor_name or supplier_name)
                    "notes": notes_text,  # Use normalized value (notes or catatan)
                    "transaction_type": body.transaction_type or "pembelian",
                    # Discount & PPN fields (V005)
                    "discount_type": backend_discount_type,
                    "discount_value": body.discount_value or 0,
                    "include_vat": use_vat,  # Use normalized value (include_vat or has_ppn)
                    "has_discount": body.has_discount or False,
                    # Additional metadata
                    "units_per_pack": body.units_per_pack,
                    "content_unit": body.content_unit,  # Kulakan: unit for contents
                    "purchase_type": body.purchase_type,
                    "purchase_date": body.purchase_date,
                    "due_date": body.due_date,
                    "is_tempo": body.is_tempo or False,
                    "is_wholesale": body.is_wholesale or False,
                    "is_new_product": body.is_new_product or False,
                    "category": body.category,
                    # HPP & Margin fields (V006)
                    "hpp_per_unit": hpp,  # Use normalized value (hpp_per_unit or harga_pokok)
                    "harga_jual": body.harga_jual,
                    "margin": body.margin,
                    "margin_percent": body.margin_percent,
                    "retail_unit": body.retail_unit,
                }
            }
        )

        logger.info(f"Form data JSON: {form_data_json}")

        # Build gRPC request
        grpc_request = tenant_orchestrator_pb2.ProcessTenantQueryRequest(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            message=message,
            conversation_context=form_data_json,
        )

        # Call tenant_orchestrator
        t_grpc = time.perf_counter()
        grpc_response = await stub.ProcessTenantQuery(grpc_request)
        grpc_duration_ms = (time.perf_counter() - t_grpc) * 1000
        logger.info(f"[PERF] gRPC_TenantOrchestrator: {grpc_duration_ms:.0f}ms")

        # Close channel
        await channel.close()

        logger.info(
            f"Orchestrator response: status={grpc_response.status}, response={grpc_response.milky_response[:100]}"
        )

        # ===== 4. PARSE RESPONSE =====
        milky_response = grpc_response.milky_response or ""

        if grpc_response.status == "success":
            # Extract transaction_id from multiple possible locations
            transaction_id = None

            # 1. Try entities_json first
            try:
                entities = (
                    json.loads(grpc_response.entities_json)
                    if grpc_response.entities_json
                    else {}
                )
                transaction_id = entities.get("transaksi_id") or entities.get(
                    "transaction_id"
                )
            except:
                pass

            # 2. Try parsing from milky_response (fallback)
            if not transaction_id and milky_response:
                import re

                match = re.search(r"ID:\s*([a-zA-Z0-9_]+)", milky_response)
                if match:
                    transaction_id = match.group(1)

            logger.info(f"Extracted transaction_id: {transaction_id}")

            # ===== 5. SAVE TO CHAT HISTORY FOR PERSISTENCE =====
            # This ensures receipts persist after page refresh
            await save_to_chat_history(
                user_id=user_id,
                tenant_id=tenant_id,
                message=message,
                response=milky_response or "Transaksi berhasil dicatat",
                intent="pembelian",
                metadata={
                    "transaction_id": transaction_id,
                    "form_type": "pembelian",
                    "product": body.product_name,
                    "quantity": body.quantity,
                    "unit": body.unit,
                    "total": body.total_amount or (body.quantity * body.price_per_unit),
                },
            )

            total_ms = (time.perf_counter() - t_request_start) * 1000
            logger.info(f"[PERF] API_GATEWAY_TOTAL: {total_ms:.0f}ms")

            return TransactionResponse(
                status="success",
                message=milky_response or "Transaksi berhasil dicatat",
                transaction_id=transaction_id,
            )
        else:
            total_ms = (time.perf_counter() - t_request_start) * 1000
            logger.info(f"[PERF] API_GATEWAY_TOTAL: {total_ms:.0f}ms (error)")

            return TransactionResponse(
                status="error", message=milky_response or "Gagal mencatat transaksi"
            )

    except HTTPException:
        raise
    except grpc.RpcError as e:
        logger.error(f"gRPC error: {e.code()} - {e.details()}")
        raise HTTPException(status_code=500, detail=f"Service error: {e.details()}")
    except Exception as e:
        logger.error(f"Error in create_purchase_transaction: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


class GuidedInputRequest(BaseModel):
    """Guided conversational input"""

    input_text: str


class ParsedTransaction(BaseModel):
    """Parsed transaction data"""

    keyword: Optional[str] = None
    product_name: Optional[str] = None
    quantity: Optional[int] = None
    unit: Optional[str] = None
    price_per_unit: Optional[int] = None
    isi_per_unit: Optional[int] = None
    unit_kecil: Optional[str] = None
    discount_type: Optional[str] = None
    discount_value: Optional[float] = None
    include_vat: bool = False
    payment_method: Optional[str] = None
    vendor_name: Optional[str] = None
    transaction_type: str = "retail"
    total: int = 0
    hpp_per_piece: Optional[float] = None


class ParseGuidedResponse(BaseModel):
    """Response from parse-guided endpoint"""

    status: str
    parsed: ParsedTransaction
    validation: dict


@router.post("/parse-guided", response_model=ParseGuidedResponse)
async def parse_guided_input(request: Request, body: GuidedInputRequest):
    """
    Parse guided conversational input using REGEX (NO LLM).
    Returns structured transaction data for preview/confirmation.

    Example input:
    "Kulakan Indomie Goreng 5 karton harga 110rb per karton isi 40 per pcs dari Indogrosir bayar tunai"
    """
    try:
        # Get user from auth middleware
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        # Parse the input
        parsed = parse_conversational_input(body.input_text)

        # Validate required fields
        validation = validate_parsed_input(parsed)

        logger.info(
            f"Parsed guided input: tenant={tenant_id}, product={parsed.get('product_name')}, valid={validation['is_valid']}"
        )

        return ParseGuidedResponse(
            status="success" if validation["is_valid"] else "incomplete",
            parsed=ParsedTransaction(**parsed),
            validation=validation,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error parsing guided input: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Parse error: {str(e)}")


class SalesItemRequest(BaseModel):
    """Single item in sales cart"""

    productId: str
    name: Optional[str] = None  # Product name for display
    barcode: Optional[str] = None
    qty: int
    price: int  # harga_jual per unit


class SalesTransactionRequest(BaseModel):
    """Sales transaction from POS frontend"""

    items: List[SalesItemRequest]
    totalAmount: int
    paymentAmount: int
    kembalian: int
    paymentMethod: Literal["CASH", "TRANSFER", "QRIS"] = "CASH"
    proofImage: Optional[str] = None  # Base64 image for TRANSFER/QRIS
    discount: Optional[int] = 0  # Discount percentage
    hutang: Optional[int] = 0  # Amount owed (for hutang payment)


class SalesTransactionResponse(BaseModel):
    """Response for sales transaction"""

    status: str
    message: str
    transaction_id: Optional[str] = None
    receipt_html: Optional[str] = None


@router.post("/sales", response_model=SalesTransactionResponse)
async def create_sales_transaction(request: Request, body: SalesTransactionRequest):
    """
    Create sales transaction from POS form.

    Routes to tenant_orchestrator for proper processing (like Kulakan pattern).
    Flow: API Gateway → Orchestrator → DB (sync) → Outbox (async)
    """
    try:
        # ===== 1. GET USER FROM AUTH MIDDLEWARE =====
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        user = request.state.user
        tenant_id = user.get("tenant_id")
        user_id = user.get("user_id")

        if not tenant_id or not user_id:
            raise HTTPException(
                status_code=401,
                detail="Invalid user context: missing tenant_id or user_id",
            )

        logger.info(
            f"POS Sales request from user {user_id} in tenant {tenant_id}: {len(body.items)} items, total={body.totalAmount}"
        )

        # ===== 2. BUILD NATURAL LANGUAGE MESSAGE =====
        # [FORM] flag tells orchestrator to skip clarification flow
        items_text = ", ".join(
            [
                f"{item.qty} {item.name or 'produk'} @{format_rupiah(item.price)}"
                for item in body.items
            ]
        )

        # Map payment method
        payment_map = {"CASH": "tunai", "TRANSFER": "transfer", "QRIS": "qris"}
        payment_text = payment_map.get(body.paymentMethod, "tunai")

        message = f"[FORM] Jual {items_text} bayar {payment_text}"

        logger.info(f"Generated POS message: {message}")

        # ===== 3. BUILD FORM DATA JSON =====
        # Same pattern as /purchase endpoint - pass structured data to orchestrator
        form_data_json = json.dumps(
            {
                "form_data": {
                    "transaction_type": "penjualan",
                    "items": [
                        {
                            "nama_produk": item.name or f"Produk #{item.productId[:8]}",
                            "product_id": item.productId,
                            "barcode": item.barcode,
                            "jumlah": item.qty,
                            "satuan": "pcs",
                            "harga_satuan": item.price,
                            "subtotal": item.qty * item.price,
                        }
                        for item in body.items
                    ],
                    "total": body.totalAmount,
                    "payment_method": payment_text,
                    "payment_amount": body.paymentAmount,
                    "change": body.kembalian,
                    "discount": body.discount or 0,
                    "hutang": body.hutang or 0,
                    # Optional: proof image for non-cash
                    "proof_image": body.proofImage if body.proofImage else None,
                }
            }
        )

        logger.info(f"Form data JSON: {form_data_json[:200]}...")

        # ===== 4. CALL TENANT ORCHESTRATOR =====
        session_id = f"pos_{uuid.uuid4().hex[:12]}"

        # Connect to tenant_orchestrator gRPC
        channel = grpc.aio.insecure_channel("tenant_orchestrator:5017")
        stub = tenant_orchestrator_pb2_grpc.TenantOrchestratorStub(channel)

        # Build gRPC request
        grpc_request = tenant_orchestrator_pb2.ProcessTenantQueryRequest(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            message=message,
            conversation_context=form_data_json,
        )

        # Call tenant_orchestrator
        grpc_response = await stub.ProcessTenantQuery(grpc_request)

        # Close channel
        await channel.close()

        logger.info(
            f"Orchestrator response: status={grpc_response.status}, response={grpc_response.milky_response[:100] if grpc_response.milky_response else 'empty'}"
        )

        # ===== 5. PARSE RESPONSE =====
        milky_response = grpc_response.milky_response or ""

        if grpc_response.status == "success":
            # Extract transaction_id from multiple possible locations
            transaction_id = None
            receipt_html = None

            # 1. Try entities_json first
            try:
                entities = (
                    json.loads(grpc_response.entities_json)
                    if grpc_response.entities_json
                    else {}
                )
                transaction_id = entities.get("transaksi_id") or entities.get(
                    "transaction_id"
                )
                receipt_html = entities.get("receipt_html")
            except:
                pass

            # 2. Try parsing from milky_response (fallback)
            if not transaction_id and milky_response:
                import re

                match = re.search(r"ID:\s*([a-zA-Z0-9_]+)", milky_response)
                if match:
                    transaction_id = match.group(1)

            logger.info(f"Extracted transaction_id: {transaction_id}")

            # 3. Generate receipt if orchestrator didn't provide one
            if not receipt_html:
                receipt_items = [
                    {
                        "name": item.name or f"Produk #{item.productId[:8]}",
                        "qty": item.qty,
                        "price": item.price,
                        "subtotal": item.qty * item.price,
                    }
                    for item in body.items
                ]
                receipt_html = generate_sales_receipt(
                    transaction_id=transaction_id
                    or f"POS_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    items=receipt_items,
                    total=body.totalAmount,
                    payment=body.paymentAmount,
                    change=body.kembalian,
                    payment_method=body.paymentMethod,
                )

            # ===== 6. SAVE TO CHAT HISTORY FOR PERSISTENCE =====
            # This ensures receipts persist after page refresh
            await save_to_chat_history(
                user_id=user_id,
                tenant_id=tenant_id,
                message=message,
                response=receipt_html
                or milky_response
                or "Transaksi penjualan berhasil",
                intent="penjualan",
                metadata={
                    "transaction_id": transaction_id,
                    "form_type": "penjualan",
                    "items_count": len(body.items),
                    "total": body.totalAmount,
                    "payment_method": body.paymentMethod,
                },
            )

            return SalesTransactionResponse(
                status="success",
                message=milky_response or "Transaksi penjualan berhasil",
                transaction_id=transaction_id,
                receipt_html=receipt_html,
            )
        else:
            return SalesTransactionResponse(
                status="error",
                message=milky_response or "Gagal mencatat transaksi penjualan",
            )

    except HTTPException:
        raise
    except grpc.RpcError as e:
        logger.error(f"gRPC error in sales: {e.code()} - {e.details()}")
        raise HTTPException(status_code=500, detail=f"Service error: {e.details()}")
    except Exception as e:
        logger.error(f"Error in create_sales_transaction: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


def generate_sales_receipt(
    transaction_id: str,
    items: list,
    total: int,
    payment: int,
    change: int,
    payment_method: str,
) -> str:
    """Generate HTML receipt for sales transaction"""
    now = datetime.now()

    items_html = ""
    for item in items:
        items_html += f"""
        <tr>
            <td style="padding: 4px 0; border-bottom: 1px dashed #ddd;">{item['name']}</td>
            <td style="padding: 4px 0; text-align: center; border-bottom: 1px dashed #ddd;">{item['qty']}</td>
            <td style="padding: 4px 0; text-align: right; border-bottom: 1px dashed #ddd;">Rp{item['price']:,}</td>
            <td style="padding: 4px 0; text-align: right; border-bottom: 1px dashed #ddd;">Rp{item['subtotal']:,}</td>
        </tr>
        """

    return f"""
    <div style="font-family: 'Courier New', monospace; max-width: 300px; margin: 0 auto; padding: 16px; background: #fff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
        <div style="text-align: center; margin-bottom: 12px;">
            <div style="font-size: 18px; font-weight: bold;">🧾 STRUK PENJUALAN</div>
            <div style="font-size: 11px; color: #666;">{now.strftime('%d/%m/%Y %H:%M')}</div>
            <div style="font-size: 10px; color: #999;">ID: {transaction_id}</div>
        </div>

        <table style="width: 100%; border-collapse: collapse; font-size: 12px;">
            <thead>
                <tr style="border-bottom: 2px solid #333;">
                    <th style="text-align: left; padding: 4px 0;">Item</th>
                    <th style="text-align: center; padding: 4px 0;">Qty</th>
                    <th style="text-align: right; padding: 4px 0;">Harga</th>
                    <th style="text-align: right; padding: 4px 0;">Total</th>
                </tr>
            </thead>
            <tbody>
                {items_html}
            </tbody>
        </table>

        <div style="margin-top: 12px; padding-top: 8px; border-top: 2px solid #333;">
            <div style="display: flex; justify-content: space-between; font-size: 14px; font-weight: bold;">
                <span>TOTAL</span>
                <span>Rp{total:,}</span>
            </div>
            <div style="display: flex; justify-content: space-between; font-size: 12px; color: #666; margin-top: 4px;">
                <span>Bayar ({payment_method})</span>
                <span>Rp{payment:,}</span>
            </div>
            <div style="display: flex; justify-content: space-between; font-size: 14px; font-weight: bold; color: #22c55e; margin-top: 4px;">
                <span>Kembalian</span>
                <span>Rp{change:,}</span>
            </div>
        </div>

        <div style="text-align: center; margin-top: 16px; font-size: 11px; color: #666;">
            ✅ Terima kasih!
        </div>
    </div>
    """


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "transactions_router"}
