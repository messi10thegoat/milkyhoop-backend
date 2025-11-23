"""
Clarification Handler for Tenant Orchestrator
Handles incomplete transaction data with clarification questions

Author: MilkyHoop Team
Version: 1.0.0
"""

import logging
import json
import re
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


def detect_missing_fields(entities: Dict[str, Any], jenis_transaksi: str) -> List[str]:
    """
    Detect missing required fields for transaction
    
    Returns list of missing field names
    """
    missing = []
    
    # Always check total_nominal (required for all transactions)
    if not entities.get("total_nominal") or entities.get("total_nominal") == 0:
        # Don't add to missing if we have items (will calculate from items)
        if not entities.get("items") or len(entities.get("items", [])) == 0:
            missing.append("total_nominal")
    
    if jenis_transaksi == "beban":
        # For beban, check kategori_beban
        if not entities.get("kategori_beban"):
            missing.append("kategori_beban")
        
        # For gaji, check detail karyawan and periode
        if "gaji" in entities.get("keterangan", "").lower() or entities.get("kategori_beban") == "beban_gaji":
            if not entities.get("detail_karyawan"):
                missing.append("detail_karyawan")
            if not entities.get("periode_gaji"):
                missing.append("periode_gaji")
    
    elif jenis_transaksi == "pembelian":
        # For pembelian, check items
        items = entities.get("items", [])
        if not items or len(items) == 0:
            missing.append("items")
        else:
            # Check each item
            for i, item in enumerate(items):
                if not item.get("nama_produk"):
                    missing.append(f"items[{i}].nama_produk")
                if not item.get("jumlah") or item.get("jumlah", 0) == 0:
                    missing.append(f"items[{i}].jumlah")
                if not item.get("satuan"):
                    missing.append(f"items[{i}].satuan")
    
    elif jenis_transaksi == "penjualan":
        # For penjualan, check items
        items = entities.get("items", [])
        if not items or len(items) == 0:
            missing.append("items")
        else:
            for i, item in enumerate(items):
                if not item.get("nama_produk"):
                    missing.append(f"items[{i}].nama_produk")
                if not item.get("jumlah") or item.get("jumlah", 0) == 0:
                    missing.append(f"items[{i}].jumlah")
    
    return missing


async def get_product_list_from_inventory(
    tenant_id: str,
    search_term: str,
    client_manager,
    trace_id: str,
    limit: int = 10
) -> List[Dict[str, str]]:
    """
    Get list of products from inventory that match search term
    
    Returns list of {produk_id, satuan} dicts
    """
    try:
        # Import Prisma client
        from app.prisma_client import prisma
        
        # Connect if not connected
        if not prisma.is_connected():
            await prisma.connect()
        
        # Query Persediaan table - get unique produk_id by tenant_id
        # Filter by search_term (fuzzy match on produk_id)
        search_lower = search_term.lower()
        search_tokens = search_lower.split()
        
        # Get all products for tenant
        all_products = await prisma.persediaan.find_many(
            where={
                "tenantId": tenant_id
            },
            distinct=["produkId"],  # Get unique produk_id
            take=limit * 3  # Get more to filter
        )
        
        # Filter products that match search term
        matched_products = []
        for prod in all_products:
            produk_id_lower = prod.produkId.lower()
            # Match if any token appears in produk_id
            if any(token in produk_id_lower for token in search_tokens) or search_lower in produk_id_lower:
                matched_products.append({
                    "produk_id": prod.produkId,
                    "satuan": prod.satuan or "pcs"
                })
                if len(matched_products) >= limit:
                    break
        
        logger.info(f"[{trace_id}] Found {len(matched_products)} products matching '{search_term}'")
        return matched_products
        
    except Exception as e:
        logger.error(f"[{trace_id}] Failed to get product list: {e}", exc_info=True)
        return []


def generate_clarification_question(
    missing_fields: List[str],
    entities: Dict[str, Any],
    jenis_transaksi: str,
    product_list: List[Dict[str, str]] = None
) -> str:
    """
    Generate clarification question based on missing fields
    """
    question_parts = []
    
    # Handle beban - gaji
    if entities.get("kategori_beban") == "beban_gaji" or "gaji" in entities.get("keterangan", "").lower():
        if "detail_karyawan" in missing_fields and "periode_gaji" in missing_fields:
            return "Mohon maaf, untuk pencatatan yang rapi, bisa tolong sebutkan:\n\n1. Gaji untuk siapa saja? (nama karyawan atau jumlah karyawan)\n2. Untuk periode bulan apa? (misalnya: November, Desember, atau bulan ini)"
        elif "detail_karyawan" in missing_fields:
            return "Mohon maaf, untuk pencatatan yang rapi, bisa tolong sebutkan gaji untuk siapa saja? (nama karyawan atau jumlah karyawan)"
        elif "periode_gaji" in missing_fields:
            return "Mohon maaf, untuk pencatatan yang rapi, bisa tolong sebutkan untuk periode bulan apa? (misalnya: November, Desember, atau bulan ini)"
    
    # Handle pembelian - produk tidak jelas
    if "items" in missing_fields or any("items" in f for f in missing_fields):
        search_term = entities.get("keterangan", "") or entities.get("nama_pihak", "") or ""
        
        # Check if it's about kain, bahan, or generic produk
        if any(k in search_term.lower() for k in ["kain", "bahan", "material", "produk"]):
            product_type = "kain" if "kain" in search_term.lower() else "produk"
            question = f"Mohon maaf, untuk pencatatan yang rapi, bisa tolong sebutkan {product_type} jenis apa yang dibeli"
            
            if product_list and len(product_list) > 0:
                question += f"?\n\nDi data inventory tersedia jenis {product_type} sebagai berikut:\n\n"
                for i, prod in enumerate(product_list[:10], 1):  # Max 10 items
                    satuan = prod.get('satuan', 'pcs')
                    question += f"{i}. {prod.get('produk_id', 'N/A')} (satuan: {satuan})\n"
                question += "\n"
            else:
                question += "?\n\n"
            
            # Check if quantity missing
            if any("jumlah" in f for f in missing_fields):
                if "kain" in search_term.lower():
                    question += "Bisa tolong sebutkan juga berapa meter atau berapa kilo yang dibeli? Ini penting untuk catatan inventory yang akurat."
                else:
                    question += "Bisa tolong sebutkan juga berapa jumlah yang dibeli? Ini penting untuk catatan inventory yang akurat."
            
            return question
    
    # Generic missing items
    if "items" in missing_fields:
        return "Mohon maaf, untuk pencatatan yang rapi, bisa tolong sebutkan produk apa yang dibeli dan berapa jumlahnya?"
    
    # Generic missing kategori_beban
    if "kategori_beban" in missing_fields:
        return "Mohon maaf, untuk pencatatan yang rapi, bisa tolong sebutkan kategori beban ini? (contoh: gaji, operasional, pajak, listrik, dll)"
    
    # Missing total_nominal
    if "total_nominal" in missing_fields:
        return "Mohon maaf, untuk pencatatan yang rapi, bisa tolong sebutkan nominal atau jumlah uangnya berapa?"
    
    # Default - user-friendly messages (no technical terms)
    if missing_fields:
        # Map technical field names to user-friendly terms
        friendly_names = {
            "total_nominal": "nominal atau jumlah uang",
            "detail_karyawan": "detail karyawan",
            "periode_gaji": "periode gaji",
            "items": "produk yang dibeli",
            "kategori_beban": "kategori beban"
        }
        
        friendly_missing = [friendly_names.get(f, f) for f in missing_fields]
        
        if len(friendly_missing) == 1:
            return f"Mohon maaf, untuk pencatatan yang rapi, bisa tolong sebutkan {friendly_missing[0]}?"
        else:
            return f"Mohon maaf, untuk pencatatan yang rapi, ada beberapa informasi yang kurang lengkap. Bisa tolong sebutkan:\n\n" + "\n".join([f"{i+1}. {fm}" for i, fm in enumerate(friendly_missing)]) + "\n\nTerima kasih!"
    
    return "Mohon maaf, ada informasi yang kurang lengkap. Bisa tolong lengkapi? Terima kasih!"


class ClarificationHandler:
    """Handler for transaction clarification in tenant mode"""
    
    @staticmethod
    async def handle_clarification(
        request,
        ctx_response,
        intent_response,
        trace_id: str,
        service_calls: list,
        progress: int,
        client_manager
    ) -> str:
        """
        Handle incomplete transaction data - returns clarification question
        """
        logger.info(f"[{trace_id}] Handling clarification for incomplete data")
        
        # Parse entities from intent_response
        try:
            entities = json.loads(intent_response.entities_json)
        except:
            entities = {}
        
        jenis_transaksi = entities.get("jenis_transaksi", "")
        
        # Detect missing fields
        missing_fields = detect_missing_fields(entities, jenis_transaksi)
        
        if not missing_fields:
            # No missing fields, proceed with transaction
            logger.info(f"[{trace_id}] No missing fields, proceeding with transaction")
            return None  # Signal to proceed
        
        logger.info(f"[{trace_id}] Missing fields detected: {missing_fields}")
        
        # Get product list if needed (for pembelian with unclear product)
        product_list = []
        if "items" in missing_fields or any("items" in f for f in missing_fields):
            search_term = entities.get("keterangan", "") or entities.get("nama_pihak", "")
            if search_term:
                product_list = await get_product_list_from_inventory(
                    request.tenant_id,
                    search_term,
                    client_manager,
                    trace_id
                )
        
        # Generate clarification question
        question = generate_clarification_question(
            missing_fields,
            entities,
            jenis_transaksi,
            product_list
        )
        
        # Store partial data in conversation context for later completion
        # This will be saved in grpc_server.py metadata
        logger.info(f"[{trace_id}] 💬 Clarification question generated")
        
        # Return question with signal that partial data should be stored
        # The question will be returned, and grpc_server.py will store partial data in metadata
        return question

