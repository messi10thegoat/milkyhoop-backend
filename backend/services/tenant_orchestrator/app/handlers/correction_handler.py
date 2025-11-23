"""
Correction Handler for Tenant Orchestrator
Handles transaction corrections/updates in multi-turn conversation

Author: MilkyHoop Team
Version: 1.0.0
"""

import logging
import json
import re
from datetime import datetime
from typing import Dict, Any, Optional

import transaction_service_pb2

logger = logging.getLogger(__name__)


def format_rupiah(rupiah_amount):
    """Format rupiah to Indonesian Rupiah"""
    formatted = f"{int(rupiah_amount):,}".replace(",", ".")
    return f"Rp{formatted}"


async def get_last_transaction_context(
    request,
    client_manager,
    trace_id: str
) -> Optional[Dict[str, Any]]:
    """
    Get last transaction context from conversation history
    
    Returns:
        {
            "transaction_id": "tx_xxx",
            "action": "transaction_record",
            "jenis_transaksi": "penjualan",
            "total_nominal": 450000
        } or None if not found
    """
    try:
        import conversation_service_pb2
        
        # Get last message from chat history
        history_request = conversation_service_pb2.GetChatHistoryRequest(
            user_id=request.user_id if hasattr(request, 'user_id') else request.tenant_id,
            tenant_id=request.tenant_id,
            limit=1,  # Get only last message
            offset=0
        )
        
        history_response = await client_manager.stubs['conversation'].GetChatHistory(
            history_request
        )
        
        if not history_response.messages or len(history_response.messages) == 0:
            logger.info(f"[{trace_id}] No chat history found")
            return None
        
        last_message = history_response.messages[0]
        
        # Parse metadata
        if not last_message.metadata_json:
            logger.info(f"[{trace_id}] Last message has no metadata")
            return None
        
        try:
            metadata = json.loads(last_message.metadata_json)
        except:
            logger.warning(f"[{trace_id}] Failed to parse metadata JSON")
            return None
        
        # Check if last message was a transaction
        if not metadata.get("last_transaction_id"):
            logger.info(f"[{trace_id}] Last message was not a transaction")
            return None
        
        context = {
            "transaction_id": metadata.get("last_transaction_id"),
            "action": metadata.get("last_action", ""),
            "jenis_transaksi": metadata.get("last_jenis_transaksi", ""),
            "total_nominal": metadata.get("last_total_nominal", 0)
        }
        
        logger.info(f"[{trace_id}] ✅ Found last transaction context: {context['transaction_id']}")
        return context
        
    except Exception as e:
        logger.error(f"[{trace_id}] Error getting last transaction context: {e}")
        return None


def parse_correction_fields(
    message: str,
    entities: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Parse multiple fields to update from user message (support complex corrections)
    
    Returns:
        {
            "total_nominal": 50000000,
            "detail_karyawan": "2 karyawan: Anton, Dona",
            "periode_gaji": "november",
            ...
        }
    """
    message_lower = message.lower()
    corrections = {}
    
    # Get from entities first (LLM extracted)
    field_from_entities = entities.get("field_to_update")
    value_from_entities = entities.get("new_value")
    
    if field_from_entities and value_from_entities:
        corrections[field_from_entities] = value_from_entities
    
    # Parse total_nominal (multiple patterns)
    nominal_patterns = [
        r'(?:nominal|harga|total|jumlah|uang|masing-masing)\s*(?:nya|nya\s+)?(?:harusnya|jadi|adalah|seharusnya)?\s*(?:rp\s*)?([0-9.,]+)\s*(?:rb|jt|juta|ribu)?',
        r'rp\s*([0-9.,]+)\s*(?:rb|jt|juta|ribu)?',
        r'([0-9.,]+)\s*(?:rb|jt|juta|ribu)',
    ]
    
    for pattern in nominal_patterns:
        match = re.search(pattern, message_lower)
        if match:
            # Get original match to check for dots
            original_match = match.group(1)
            nominal_str = original_match.replace('.', '').replace(',', '')
            
            # Get context around match to check for juta/ribu
            match_start = match.start()
            match_end = match.end()
            context = message_lower[max(0, match_start-15):min(len(message_lower), match_end+15)]
            
            # If original has dots and is long (e.g., "25.000.000"), it's already in rupiah format
            has_dots = '.' in original_match
            is_long_number = len(nominal_str) > 6
            
            # Handle "rb" = ribu, "jt" = juta
            if 'rb' in context or 'ribu' in context:
                per_item = int(nominal_str) * 1000
            elif 'jt' in context or 'juta' in context:
                per_item = int(nominal_str) * 1000000
            elif has_dots and is_long_number:
                # "25.000.000" → already in rupiah format, don't multiply
                per_item = int(nominal_str)
            else:
                # Check if it's a large number (likely juta)
                if int(nominal_str) > 1000:
                    per_item = int(nominal_str) * 1000000
                else:
                    per_item = int(nominal_str)
            
            # If "masing-masing" is mentioned, calculate total from count
            if "masing-masing" in message_lower or "per" in message_lower:
                # Try to find count (e.g., "2 karyawan, masing-masing Rp 25jt")
                count_match = re.search(r'(\d+)\s*(?:orang|karyawan|pegawai)', message_lower)
                if count_match:
                    count = int(count_match.group(1))
                    total_nominal = per_item * count
                    corrections["total_nominal"] = total_nominal
                    logger.info(f"[parse_correction] Calculated total: {count} × {per_item} = {total_nominal}")
                else:
                    corrections["total_nominal"] = per_item
            else:
                corrections["total_nominal"] = per_item
            break
    
    # Parse detail_karyawan (for gaji corrections)
    # Patterns: "2 karyawan", "1. Anton, 2. Dona", "karyawan: Anton, Dona"
    if "karyawan" in message_lower:
        # Pattern: "2 karyawan" or "untuk 2 karyawan"
        count_match = re.search(r'(\d+)\s*(?:orang|karyawan|pegawai)', message_lower)
        if count_match:
            count = count_match.group(1)
            # Try to extract names - improved pattern
            names = []
            # Skip common words that are not names
            skip_words = {"maaf", "november", "desember", "januari", "februari", "maret", "april", "mei", "juni", "juli", "agustus", "september", "oktober", "bulan", "untuk", "gaji", "koreksi", "yang", "tadi", "maksudnya"}
            
            # Pattern: "1. Anton, 2. Dona" or "Anton, Dona" or "1 Anton, 2 Dona"
            # Look for capitalized words after numbers or after "1.", "2.", etc
            name_patterns = [
                r'(?:\d+\.\s*)([A-Z][a-z]+)',  # "1. Anton", "2. Dona"
                r'(?:,\s*)([A-Z][a-z]+)(?=\s|$|,|\.)',  # ", Anton", ", Dona"
            ]
            
            for pattern in name_patterns:
                name_matches = re.finditer(pattern, message)
                for match in name_matches:
                    name = match.group(1)
                    # Filter out skip words and short names
                    if name and len(name) > 2 and name.lower() not in skip_words:
                        names.append(name)
            
            # Remove duplicates and clean
            names = list(dict.fromkeys(names))  # Remove duplicates while preserving order
            
            if names:
                corrections["detail_karyawan"] = f"{count} karyawan: {', '.join(names)}"
            else:
                corrections["detail_karyawan"] = f"{count} karyawan"
    
    # Parse periode_gaji
    bulan_patterns = [
        r'bulan\s+(november|desember|januari|februari|maret|april|mei|juni|juli|agustus|september|oktober)',
        r'(november|desember|januari|februari|maret|april|mei|juni|juli|agustus|september|oktober)',
    ]
    for pattern in bulan_patterns:
        match = re.search(pattern, message_lower)
        if match:
            bulan = match.group(1) if match.lastindex else match.group(0)
            corrections["periode_gaji"] = bulan
            break
    
    # Payment method correction
    if any(k in message_lower for k in ["tunai", "cash"]):
        corrections["metode_pembayaran"] = "cash"
    elif any(k in message_lower for k in ["transfer", "bank"]):
        corrections["metode_pembayaran"] = "transfer"
    elif any(k in message_lower for k in ["tempo", "kredit"]):
        corrections["metode_pembayaran"] = "tempo"
    
    return corrections


class CorrectionHandler:
    """Handler for transaction corrections in tenant mode"""
    
    @staticmethod
    async def handle_correction(
        request,
        ctx_response,
        intent_response,
        trace_id: str,
        service_calls: list,
        progress: int,
        client_manager
    ) -> str:
        """
        Handle transaction correction intent - returns string response
        """
        logger.info(f"[{trace_id}] Handling koreksi intent")
        
        # Parse entities from intent_response
        try:
            correction_entities = json.loads(intent_response.entities_json)
        except:
            correction_entities = {}
        
        logger.info(f"[{trace_id}] Correction entities: {correction_entities}")
        
        # Step 1: Get last transaction context
        last_tx_context = await get_last_transaction_context(
            request, client_manager, trace_id
        )
        
        if not last_tx_context:
            return "Maaf, saya tidak menemukan transaksi sebelumnya untuk dikoreksi. Silakan catat transaksi dulu ya."
        
        transaction_id = last_tx_context["transaction_id"]
        
        # Handle truncated transaction_id (if only 8 chars saved)
        # Query recent transactions to find full ID
        full_transaction_id = transaction_id
        if len(transaction_id) < 20:  # UUID is usually 36 chars
            logger.warning(f"[{trace_id}] ⚠️ Transaction ID seems truncated: {transaction_id}, trying to find full ID")
            try:
                # Query recent transactions to find matching ID
                list_request = transaction_service_pb2.ListTransactionsRequest(
                    tenant_id=request.tenant_id,
                    page=1,
                    page_size=10
                )
                list_response = await client_manager.stubs['transaction'].ListTransactions(
                    list_request
                )
                
                # Find transaction with matching prefix
                for tx in list_response.transactions:
                    if tx.id.startswith(transaction_id):
                        full_transaction_id = tx.id
                        logger.info(f"[{trace_id}] ✅ Found full transaction ID: {full_transaction_id}")
                        break
            except Exception as e:
                logger.warning(f"[{trace_id}] Failed to find full ID: {e}, using truncated ID")
        
        logger.info(f"[{trace_id}] 📝 Correcting transaction: {full_transaction_id}")
        
        # Step 2: Parse multiple fields to update (support complex corrections)
        corrections = parse_correction_fields(
            request.message,
            correction_entities.get("entities", {})
        )
        
        logger.info(f"[{trace_id}] Fields to update: {corrections}")
        
        if not corrections:
            return "Maaf, saya tidak bisa memahami koreksi yang diminta. Bisa tolong sebutkan dengan lebih jelas?"
        
        # Step 3: Build update request (support multiple fields)
        try:
            # Start with base request
            update_request = transaction_service_pb2.UpdateTransactionRequest(
                transaction_id=full_transaction_id,
                tenant_id=request.tenant_id,
                updated_by=request.user_id
            )
            
            # Add fields to update
            if "total_nominal" in corrections:
                update_request.total_nominal = int(corrections["total_nominal"])
                logger.info(f"[{trace_id}] ✅ Updating total_nominal: {corrections['total_nominal']}")
            
            if "metode_pembayaran" in corrections:
                update_request.metode_pembayaran = str(corrections["metode_pembayaran"])
                logger.info(f"[{trace_id}] ✅ Updating metode_pembayaran: {corrections['metode_pembayaran']}")
            
            # For beban_gaji, store detail_karyawan and periode_gaji in keterangan
            # (since UpdateTransactionRequest doesn't have these fields directly)
            keterangan_parts = []
            if "detail_karyawan" in corrections:
                keterangan_parts.append(f"Karyawan: {corrections['detail_karyawan']}")
            if "periode_gaji" in corrections:
                keterangan_parts.append(f"Periode: {corrections['periode_gaji']}")
            
            if keterangan_parts:
                update_request.keterangan = "; ".join(keterangan_parts)
                logger.info(f"[{trace_id}] ✅ Updating keterangan with detail: {update_request.keterangan}")
            elif "keterangan" in corrections:
                update_request.keterangan = str(corrections["keterangan"])
                logger.info(f"[{trace_id}] ✅ Updating keterangan: {corrections['keterangan']}")
            
            # Validate at least one field is being updated
            if not update_request.total_nominal and not update_request.metode_pembayaran and not update_request.keterangan:
                return "Maaf, saya tidak bisa memahami koreksi yang diminta. Bisa tolong sebutkan dengan lebih jelas?"
            
            # Step 4: Call transaction_service.UpdateTransaction
            update_response = await client_manager.stubs['transaction'].UpdateTransaction(
                update_request
            )
            
            if update_response.success:
                # Store transaction_id in service_calls for context saving (same as transaction_handler)
                service_calls.append({
                    "service_name": "transaction",
                    "method": "UpdateTransaction",
                    "duration_ms": 0,  # Will be calculated in grpc_server
                    "status": "success",
                    "transaction_id": full_transaction_id  # Store full ID for context
                })
                
                # Build user-friendly response
                response = f"✅ Koreksi berhasil!\n\n"
                response += f"Transaksi {full_transaction_id[:8]}... sudah diupdate:\n"
                
                # List all updated fields in user-friendly format
                updates = []
                if "total_nominal" in corrections:
                    updates.append(f"├─ Nominal: {format_rupiah(corrections['total_nominal'])}")
                if "metode_pembayaran" in corrections:
                    updates.append(f"├─ Metode pembayaran: {corrections['metode_pembayaran']}")
                if "detail_karyawan" in corrections:
                    updates.append(f"├─ Detail karyawan: {corrections['detail_karyawan']}")
                if "periode_gaji" in corrections:
                    updates.append(f"├─ Periode: {corrections['periode_gaji']}")
                if "keterangan" in corrections:
                    updates.append(f"├─ Keterangan: {corrections['keterangan']}")
                
                if updates:
                    response += "\n".join(updates) + "\n"
                
                response += f"└─ ID: {full_transaction_id[:8]}...\n\n"
                response += f"Terima kasih sudah mengoreksi! 😊"
                
                logger.info(f"[{trace_id}] ✅ Transaction corrected: {full_transaction_id}, fields: {list(corrections.keys())}")
                return response
            else:
                return f"⚠️ Gagal mengoreksi transaksi: {update_response.message}"
            
        except Exception as e:
            logger.error(f"[{trace_id}] Correction failed: {e}", exc_info=True)
            return f"Maaf, ada kendala saat mengoreksi transaksi. Error: {str(e)[:100]}"

