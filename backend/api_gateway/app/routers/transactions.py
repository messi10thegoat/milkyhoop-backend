"""
Transaction Router - Form-based Transaction Recording
Converts structured form input to orchestrator query
"""
import grpc
import json
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, Literal
import logging
import uuid

from backend.api_gateway.libs.milkyhoop_protos import tenant_orchestrator_pb2, tenant_orchestrator_pb2_grpc
from backend.api_gateway.app.utils.conversational_parser import parse_conversational_input, validate_parsed_input

logger = logging.getLogger(__name__)
router = APIRouter()


class PurchaseTransactionRequest(BaseModel):
    """Purchase transaction from frontend form"""
    product_name: str
    quantity: int
    unit: str  # pcs, kg, karton, etc.
    price_per_unit: int  # in rupiah (integer)
    payment_method: Literal["tunai", "transfer", "kredit"] = "tunai"
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
    hpp_per_unit: Optional[float] = None      # HPP per satuan kecil
    harga_jual: Optional[float] = None        # Selling price per unit
    margin: Optional[float] = None            # Profit margin
    margin_percent: Optional[float] = None    # Margin percentage
    retail_unit: Optional[str] = None         # Retail unit for HPP display (e.g., "pcs" instead of "dus")


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
    request: Request,
    body: PurchaseTransactionRequest
):
    """
    Create purchase transaction from form

    Converts structured form data to natural language message,
    then routes to tenant_orchestrator for processing.

    Example:
        Input: {product_name: "Sepatu", quantity: 10, unit: "pcs", price_per_unit: 50000}
        Message: "Beli 10 pcs Sepatu harga 50rb per pcs bayar tunai"
    """
    try:
        # ===== 1. GET USER FROM AUTH MIDDLEWARE =====
        # AuthMiddleware already validates token and sets request.state.user
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        user = request.state.user
        tenant_id = user.get("tenant_id")
        user_id = user.get("user_id")

        if not tenant_id or not user_id:
            raise HTTPException(status_code=401, detail="Invalid user context: missing tenant_id or user_id")

        logger.info(f"Purchase request from user {user_id} in tenant {tenant_id}")

        # ===== 2. BUILD NATURAL LANGUAGE MESSAGE =====
        # Convert form to conversational message that orchestrator understands
        # Add [FORM] flag so orchestrator skips clarification flow

        price_str = format_rupiah(body.price_per_unit)

        # Build base message with [FORM] flag
        message_parts = [
            f"[FORM] Beli {body.quantity} {body.unit} {body.product_name}",
            f"harga {price_str} per {body.unit}"
        ]

        # Add payment method
        if body.payment_method == "tunai":
            message_parts.append("bayar tunai")
        elif body.payment_method == "transfer":
            message_parts.append("bayar transfer")
        elif body.payment_method == "kredit":
            message_parts.append("bayar kredit")

        # Add vendor if specified
        if body.vendor_name:
            message_parts.append(f"dari {body.vendor_name}")

        # Add notes if specified
        if body.notes:
            message_parts.append(f"catatan: {body.notes}")

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

        form_data_json = json.dumps({
            "form_data": {
                "product_name": body.product_name,
                "quantity": body.quantity,
                "unit": body.unit,
                "price_per_unit": body.price_per_unit,
                "total": body.quantity * body.price_per_unit,
                "payment_method": body.payment_method,
                "vendor_name": body.vendor_name or "",
                "notes": body.notes or "",
                "transaction_type": "pembelian",
                # Discount & PPN fields (V005)
                "discount_type": backend_discount_type,
                "discount_value": body.discount_value or 0,
                "include_vat": body.include_vat,
                # Additional metadata
                "units_per_pack": body.units_per_pack,
                "purchase_type": body.purchase_type,
                "purchase_date": body.purchase_date,
                "due_date": body.due_date,
                # HPP & Margin fields (V006)
                "hpp_per_unit": body.hpp_per_unit,
                "harga_jual": body.harga_jual,
                "margin": body.margin,
                "margin_percent": body.margin_percent,
                "retail_unit": body.retail_unit
            }
        })

        logger.info(f"Form data JSON: {form_data_json}")

        # Build gRPC request
        grpc_request = tenant_orchestrator_pb2.ProcessTenantQueryRequest(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            message=message,
            conversation_context=form_data_json
        )

        # Call tenant_orchestrator
        grpc_response = await stub.ProcessTenantQuery(grpc_request)

        # Close channel
        await channel.close()

        logger.info(f"Orchestrator response: status={grpc_response.status}, response={grpc_response.milky_response[:100]}")

        # ===== 4. PARSE RESPONSE =====
        milky_response = grpc_response.milky_response or ""

        if grpc_response.status == "success":
            # Extract transaction_id from multiple possible locations
            transaction_id = None

            # 1. Try entities_json first
            try:
                entities = json.loads(grpc_response.entities_json) if grpc_response.entities_json else {}
                transaction_id = entities.get("transaksi_id") or entities.get("transaction_id")
            except:
                pass

            # 2. Try parsing from milky_response (fallback)
            if not transaction_id and milky_response:
                import re
                match = re.search(r'ID:\s*([a-zA-Z0-9_]+)', milky_response)
                if match:
                    transaction_id = match.group(1)

            logger.info(f"Extracted transaction_id: {transaction_id}")

            return TransactionResponse(
                status="success",
                message=milky_response or "Transaksi berhasil dicatat",
                transaction_id=transaction_id
            )
        else:
            return TransactionResponse(
                status="error",
                message=milky_response or "Gagal mencatat transaksi"
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
async def parse_guided_input(
    request: Request,
    body: GuidedInputRequest
):
    """
    Parse guided conversational input using REGEX (NO LLM).
    Returns structured transaction data for preview/confirmation.

    Example input:
    "Kulakan Indomie Goreng 5 karton harga 110rb per karton isi 40 per pcs dari Indogrosir bayar tunai"
    """
    try:
        # Get user from auth middleware
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        # Parse the input
        parsed = parse_conversational_input(body.input_text)

        # Validate required fields
        validation = validate_parsed_input(parsed)

        logger.info(f"Parsed guided input: tenant={tenant_id}, product={parsed.get('product_name')}, valid={validation['is_valid']}")

        return ParseGuidedResponse(
            status="success" if validation["is_valid"] else "incomplete",
            parsed=ParsedTransaction(**parsed),
            validation=validation
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error parsing guided input: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Parse error: {str(e)}")


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "transactions_router"}
