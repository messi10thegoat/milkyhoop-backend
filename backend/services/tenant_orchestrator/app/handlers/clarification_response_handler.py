"""
Clarification Response Handler for Tenant Orchestrator
Handles user response to clarification questions and merges with partial data

Author: MilkyHoop Team
Version: 1.0.0
"""

import logging
import json
import re
from typing import Dict, Any, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)

# In-memory cache for partial transaction data (fallback when message history fails)
# Key: f"{tenant_id}:{user_id}:{session_id}", Value: partial_transaction_data dict
_partial_data_cache: Dict[str, Dict[str, Any]] = {}


def _get_cache_key(tenant_id: str, user_id: str, session_id: str) -> str:
    """Generate cache key for partial data"""
    return f"{tenant_id}:{user_id}:{session_id}"


def store_partial_data_in_cache(
    tenant_id: str,
    user_id: str,
    session_id: str,
    partial_data: Dict[str, Any]
):
    """Store partial transaction data in in-memory cache"""
    cache_key = _get_cache_key(tenant_id, user_id, session_id)
    _partial_data_cache[cache_key] = partial_data
    logger.info(f"💾 Stored partial data in cache: {cache_key}")


def get_partial_data_from_cache(
    tenant_id: str,
    user_id: str,
    session_id: str
) -> Optional[Dict[str, Any]]:
    """Get partial transaction data from in-memory cache"""
    cache_key = _get_cache_key(tenant_id, user_id, session_id)
    partial_data = _partial_data_cache.get(cache_key)
    if partial_data:
        logger.info(f"✅ Found partial data in cache: {cache_key}")
    return partial_data


async def get_partial_transaction_data(
    request,
    client_manager,
    trace_id: str
) -> Optional[Dict[str, Any]]:
    """
    Get partial transaction data from last message metadata
    
    Returns:
        {
            "partial_entities": {...},
            "missing_fields": [...],
            "jenis_transaksi": "..."
        } or None
    """
    try:
        import conversation_service_pb2
        
        # Get last message from chat history
        history_request = conversation_service_pb2.GetChatHistoryRequest(
            user_id=request.user_id if hasattr(request, 'user_id') else request.tenant_id,
            tenant_id=request.tenant_id,
            limit=1,
            offset=0
        )
        
        history_response = await client_manager.stubs['conversation'].GetChatHistory(
            history_request
        )
        
        logger.info(f"[{trace_id}] GetChatHistory returned {len(history_response.messages) if history_response.messages else 0} messages")
        
        if not history_response.messages or len(history_response.messages) == 0:
            logger.info(f"[{trace_id}] No messages in history, trying cache fallback")
            # Fallback to in-memory cache
            user_id = request.user_id if hasattr(request, 'user_id') else request.tenant_id
            session_id = getattr(request, 'session_id', f"{request.tenant_id}_session")
            cached_data = get_partial_data_from_cache(request.tenant_id, user_id, session_id)
            if cached_data:
                return cached_data
            logger.info(f"[{trace_id}] No partial data in cache either")
            return None
        
        last_message = history_response.messages[0]
        logger.info(f"[{trace_id}] Last message: intent={last_message.intent}, response={last_message.response[:100] if last_message.response else 'None'}")
        
        # Parse metadata first
        if not last_message.metadata_json:
            logger.info(f"[{trace_id}] No metadata_json in last message, trying fallback")
        else:
            try:
                metadata = json.loads(last_message.metadata_json)
                logger.info(f"[{trace_id}] Metadata keys: {list(metadata.keys())}")
                
                # Check if there's partial transaction data in metadata
                if metadata.get("partial_transaction_data"):
                    partial_data = metadata.get("partial_transaction_data")
                    logger.info(f"[{trace_id}] ✅ Found partial transaction data in metadata")
                    return partial_data
            except Exception as e:
                logger.warning(f"[{trace_id}] Failed to parse metadata: {e}")
        
        # Fallback: Check if last message was a clarification question
        # (response contains clarification keywords)
        logger.info(f"[{trace_id}] Trying fallback: checking if last message was clarification question")
        if last_message.response and any(k in last_message.response.lower() for k in ["maaf bisa dibantu", "bisa tolong sebutkan"]):
            logger.info(f"[{trace_id}] ✅ Last message is clarification question, reconstructing partial data")
            # This is a clarification question, try to reconstruct partial data
            if last_message.intent == "transaction_record":
                try:
                    # Try to get entities from entities_json or metadata
                    last_entities = {}
                    if hasattr(last_message, 'entities_json') and last_message.entities_json:
                        last_entities = json.loads(last_message.entities_json)
                    elif metadata.get("partial_transaction_data"):
                        # Use partial entities from metadata
                        partial_data = metadata.get("partial_transaction_data")
                        last_entities = partial_data.get("partial_entities", {})
                    
                    if last_entities:
                        from app.handlers.clarification_handler import detect_missing_fields
                        missing = detect_missing_fields(last_entities, last_entities.get("jenis_transaksi", ""))
                        if missing:
                            logger.info(f"[{trace_id}] ✅ Reconstructed partial data from last message")
                            return {
                                "partial_entities": last_entities,
                                "missing_fields": missing,
                                "jenis_transaksi": last_entities.get("jenis_transaksi", "")
                            }
                except Exception as e:
                    logger.warning(f"[{trace_id}] Failed to reconstruct partial data: {e}")
        
        return None
        
    except Exception as e:
        logger.error(f"[{trace_id}] Error getting partial data: {e}")
        return None


def parse_clarification_response(
    message: str,
    missing_fields: list,
    jenis_transaksi: str
) -> Dict[str, Any]:
    """
    Parse user response to extract missing fields
    
    Returns:
        {
            "detail_karyawan": "...",
            "periode_gaji": "...",
            "items": [...],
            ...
        }
    """
    message_lower = message.lower()
    extracted = {}
    
    # Parse detail_karyawan (for gaji)
    if "detail_karyawan" in missing_fields:
        # Patterns: 
        # - "Bayar gaji untuk Anna, bulan November" → "Anna"
        # - "5 karyawan" → "5 karyawan"
        # - "karyawan A, B, C" → "A, B, C"
        # - "semua karyawan" → "semua karyawan"
        
        # Pattern 1: "untuk [Name]" or "gaji untuk [Name]"
        # Try multiple patterns to catch variations
        # Support both uppercase and lowercase names (case-insensitive matching)
        untuk_patterns = [
            r'gaji\s+untuk\s+([A-Za-z]+)',  # "gaji untuk Anna" or "gaji untuk anna"
            r'untuk\s+([A-Za-z]+)',  # "untuk Anna" or "untuk anna"
            r'(?:gaji|untuk)\s+([A-Za-z]+)(?:\s*,|\s+bulan)',  # "untuk Anna, bulan" or "untuk Anna bulan"
            r'untuk\s+([A-Za-z]+)\s*,',  # "untuk Anna," (with comma)
        ]
        name_found = False
        for pattern in untuk_patterns:
            untuk_match = re.search(pattern, message, re.IGNORECASE)
            if untuk_match:
                name = untuk_match.group(1)
                # Capitalize first letter for consistency
                name = name.capitalize()
                extracted["detail_karyawan"] = name
                name_found = True
                logger.info(f"[parse_clarification] Extracted detail_karyawan: {name} using pattern: {pattern}")
                break
        
        # Pattern 2: Number of employees (only if name not found)
        if not name_found:
            if re.search(r'(\d+)\s*(?:orang|karyawan|pegawai|staff)', message_lower):
                count_match = re.search(r'(\d+)\s*(?:orang|karyawan|pegawai|staff)', message_lower)
                if count_match:
                    extracted["detail_karyawan"] = f"{count_match.group(1)} karyawan"
                    name_found = True
            # Pattern 3: "semua karyawan"
            elif "semua" in message_lower or "all" in message_lower:
                extracted["detail_karyawan"] = "semua karyawan"
                name_found = True
            # Pattern 4: "karyawan: [names]" or "karyawan [names]"
            elif re.search(r'(?:karyawan|pegawai|staff)\s*:?\s*(.+?)(?:,|dan|untuk|bulan)', message_lower):
                karyawan_match = re.search(r'(?:karyawan|pegawai|staff)\s*:?\s*(.+?)(?:,|dan|untuk|bulan)', message_lower)
                if karyawan_match:
                    extracted["detail_karyawan"] = karyawan_match.group(1).strip()
                    name_found = True
            # Pattern 5: Just number at start: "5, bulan November"
            elif re.search(r'^(\d+)\s*(?:,|dan)', message_lower):
                num_match = re.search(r'^(\d+)', message_lower)
                if num_match:
                    extracted["detail_karyawan"] = f"{num_match.group(1)} karyawan"
                    name_found = True
    
    # Parse periode_gaji (for gaji)
    if "periode_gaji" in missing_fields:
        # Patterns: "bulan November", "November 2024", "bulan ini", "november"
        bulan_patterns = [
            r'bulan\s+(november|desember|januari|februari|maret|april|mei|juni|juli|agustus|september|oktober)',
            r'(november|desember|januari|februari|maret|april|mei|juni|juli|agustus|september|oktober)',
            r'bulan\s+ini',
            r'bulan\s+lalu'
        ]
        for pattern in bulan_patterns:
            match = re.search(pattern, message_lower)
            if match:
                bulan = match.group(1) if match.lastindex else "bulan ini"
                extracted["periode_gaji"] = bulan
                break
    
    # Parse items (for pembelian/penjualan)
    if "items" in missing_fields or any("items" in f for f in missing_fields):
        # Patterns: "kain cotton 100 meter", "10 pcs kemeja", "5 kilo kain"
        items = []
        
        # Try to extract product name and quantity
        # Pattern: "quantity unit product" or "product quantity unit"
        item_patterns = [
            r'(\d+)\s*(pcs|meter|kilo|kg|liter|lt|buah|unit)\s+(.+)',
            r'(.+?)\s+(\d+)\s*(pcs|meter|kilo|kg|liter|lt|buah|unit)',
            r'(\d+)\s+(.+)',  # Generic: "10 kain"
        ]
        
        for pattern in item_patterns:
            match = re.search(pattern, message_lower)
            if match:
                if len(match.groups()) == 3:
                    if match.group(2) in ["pcs", "meter", "kilo", "kg", "liter", "lt", "buah", "unit"]:
                        # Format: "10 meter kain"
                        jumlah = int(match.group(1))
                        satuan = match.group(2)
                        nama_produk = match.group(3).strip()
                    else:
                        # Format: "kain 10 meter"
                        nama_produk = match.group(1).strip()
                        jumlah = int(match.group(2))
                        satuan = match.group(3)
                elif len(match.groups()) == 2:
                    # Generic format
                    jumlah = int(match.group(1))
                    nama_produk = match.group(2).strip()
                    satuan = "pcs"  # Default
                
                items.append({
                    "nama_produk": nama_produk,
                    "jumlah": jumlah,
                    "satuan": satuan
                })
                break
        
        if items:
            extracted["items"] = items
    
    logger.info(f"[parse_clarification] Extracted: {extracted}")
    return extracted


def merge_partial_with_response(
    partial_entities: Dict[str, Any],
    response_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Merge partial transaction entities with user response data
    
    Returns:
        Complete merged entities ready for transaction creation
    """
    merged = partial_entities.copy()
    
    # Merge response data
    for key, value in response_data.items():
        if key == "items" and merged.get("items"):
            # Merge items list
            merged["items"].extend(value)
        else:
            merged[key] = value
    
    return merged


class ClarificationResponseHandler:
    """Handler for processing clarification responses"""
    
    @staticmethod
    async def handle_clarification_response(
        request,
        ctx_response,
        intent_response,
        trace_id: str,
        service_calls: list,
        progress: int,
        client_manager
    ) -> Optional[str]:
        """
        Handle user response to clarification question
        
        Returns:
            None if not a clarification response (proceed normally)
            String response if clarification processed (should complete transaction)
        """
        logger.info(f"[{trace_id}] Checking if message is clarification response")
        
        # Step 1: Get partial data from last message
        partial_data = await get_partial_transaction_data(
            request, client_manager, trace_id
        )
        
        if not partial_data:
            # Not a clarification response, proceed normally
            return None
        
        logger.info(f"[{trace_id}] ✅ Detected clarification response, partial data found")
        
        # Step 2: Parse user response
        missing_fields = partial_data.get("missing_fields", [])
        jenis_transaksi = partial_data.get("jenis_transaksi", "")
        partial_entities = partial_data.get("partial_entities", {})
        
        response_data = parse_clarification_response(
            request.message,
            missing_fields,
            jenis_transaksi
        )
        
        # Step 3: Merge partial with response
        merged_entities = merge_partial_with_response(
            partial_entities,
            response_data
        )
        
        # Step 4: Check if all required fields are now complete
        from app.handlers.clarification_handler import detect_missing_fields
        remaining_missing = detect_missing_fields(merged_entities, jenis_transaksi)
        
        if remaining_missing:
            # Still missing fields, ask again
            logger.info(f"[{trace_id}] ⚠️ Still missing fields: {remaining_missing}")
            from app.handlers.clarification_handler import ClarificationHandler, generate_clarification_question
            question = generate_clarification_question(
                remaining_missing,
                merged_entities,
                jenis_transaksi,
                product_list=None
            )
            return question
        
        # Step 5: All fields complete, proceed with transaction
        logger.info(f"[{trace_id}] ✅ All fields complete, proceeding with transaction")
        logger.info(f"[{trace_id}] Merged entities: {json.dumps(merged_entities, indent=2, ensure_ascii=False)}")
        
        # Update intent_response with merged entities
        merged_entities["_merged_from_clarification"] = True  # Flag to indicate merge
        intent_response.entities_json = json.dumps(merged_entities, ensure_ascii=False)
        
        # Force intent to transaction_record (critical for routing!)
        intent_response.intent = "transaction_record"
        logger.info(f"[{trace_id}] 🔄 Updated intent_response.intent to transaction_record after merge")
        
        # Return None to signal proceed with normal transaction flow
        return None

