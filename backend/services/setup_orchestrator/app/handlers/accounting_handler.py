"""
Accounting Handler
Extracted from setup_orchestrator grpc_server.py for better modularity

Handles:
- accounting_query: Query journal entries by period
- Future: Chart of Accounts listing
"""

import logging
import json
import grpc
from datetime import datetime

# Proto imports
import setup_orchestrator_pb2
import accounting_service_pb2

# Setup logging
logger = logging.getLogger(__name__)


def format_rupiah(rupiah_amount):
    """
    Format rupiah to Indonesian Rupiah (PUEBI + SAK EMKM compliant)
    
    Args:
        rupiah_amount: Amount in rupiah (integer)
        
    Returns:
        Formatted string: "Rp300.000" (titik sebagai pemisah ribuan)
    """
    formatted = f"{int(rupiah_amount):,}".replace(",", ".")
    return f"Rp{formatted}"


class AccountingHandler:
    """
    Static class for accounting-related operations
    All methods are async and work with GrpcClientManager
    """
    
    @staticmethod
    async def handle_accounting_query(
        request,
        ctx_response,
        intent_response,
        trace_id: str,
        service_calls: list,
        progress: int,
        client_manager
    ) -> setup_orchestrator_pb2.ProcessSetupChatResponse:
        """
        Handle accounting journal query
        Routes to accounting_service.GetJournalEntriesByPeriod()
        """
        logger.info(f"[{trace_id}] Handling accounting_query intent")
        
        # Parse entities
        try:
            entities = json.loads(intent_response.entities_json)
            accounting_entities = entities.get("entities", {})
        except:
            accounting_entities = {}
        
        logger.info(f"[{trace_id}] Accounting entities: {accounting_entities}")
        
        # Extract query parameters
        query_type = accounting_entities.get("query_type", "journal_entries")
        periode = accounting_entities.get("periode_pelaporan", datetime.now().strftime("%Y-%m"))
        
        # Only handle journal_entries for now
        if query_type != "journal_entries":
            milky_response = f"Fitur lihat {query_type} belum tersedia. Coba 'cek jurnal bulan ini'?"
            return setup_orchestrator_pb2.ProcessSetupChatResponse(
                status="success",
                milky_response=milky_response,
                current_state="accounting_queried",
                session_id=request.session_id,
                progress_percentage=progress,
                next_action="continue"
            )
        
        # Call accounting_service.GetJournalEntriesByPeriod
        try:
            accounting_start = datetime.now()
            
            journal_request = accounting_service_pb2.GetJournalEntriesByPeriodRequest(
                tenant_id=request.tenant_id,
                periode_pelaporan=periode,
                status="posted",  # Only show posted entries
                limit=50  # Limit to last 50 entries
            )
            
            journal_response = await client_manager.stubs['accounting'].GetJournalEntriesByPeriod(
                journal_request
            )
            
            accounting_duration = (datetime.now() - accounting_start).total_seconds() * 1000
            service_calls.append({
                "service_name": "accounting",
                "method": "GetJournalEntriesByPeriod",
                "duration_ms": int(accounting_duration),
                "status": "success"
            })
            
            # Build response
            if not journal_response.success or not journal_response.journal_entries:
                milky_response = f"Belum ada jurnal untuk periode {periode}. "
                milky_response += "Coba catat transaksi dulu ya!"
                
                return setup_orchestrator_pb2.ProcessSetupChatResponse(
                    status="success",
                    milky_response=milky_response,
                    current_state="accounting_queried",
                    session_id=request.session_id,
                    progress_percentage=progress,
                    next_action="continue"
                )
            
            # Format journal entries
            entries = journal_response.journal_entries
            total_entries = journal_response.total_count
            
            milky_response = f"📒 Jurnal Bulan {periode}:\n\n"
            
            # Show max 10 entries in response
            display_limit = min(10, len(entries))
            
            for i, entry in enumerate(entries[:display_limit], 1):
                # Format date
                entry_date = datetime.fromtimestamp(entry.tanggal_jurnal).strftime("%d %b")
                
                milky_response += f"{i}. {entry_date} - {entry.nomor_jurnal}\n"
                
                # Show debit/kredit lines
                for detail in entry.details:
                    if detail.debit > 0:
                        amount = format_rupiah(detail.debit)
                        milky_response += f"   Debit: {detail.nama_akun} {amount}\n"
                    if detail.kredit > 0:
                        amount = format_rupiah(detail.kredit)
                        milky_response += f"   Kredit: {detail.nama_akun} {amount}\n"
                
                milky_response += "\n"
            
            # Summary
            total_debit = sum(entry.total_debit for entry in entries)
            total_kredit = sum(entry.total_kredit for entry in entries)
            
            milky_response += f"Total: {total_entries} jurnal, "
            milky_response += f"Debit {format_rupiah(total_debit)} = Kredit {format_rupiah(total_kredit)}"
            
            if total_debit == total_kredit:
                milky_response += " ✅"
            else:
                milky_response += " ⚠️ (tidak balance!)"
            
            if total_entries > display_limit:
                milky_response += f"\n\n(Menampilkan {display_limit} dari {total_entries} jurnal)"
            
            logger.info(f"[{trace_id}] Retrieved {total_entries} journal entries for {periode}")
            
        except grpc.RpcError as e:
            logger.error(f"[{trace_id}] Accounting service error: {e}")
            milky_response = f"Maaf, gagal ambil jurnal. Error: {e.details()[:100]}"
        except Exception as e:
            logger.error(f"[{trace_id}] Unexpected error: {e}")
            milky_response = f"Ada kendala ambil jurnal. Error: {str(e)[:100]}"
        
        return setup_orchestrator_pb2.ProcessSetupChatResponse(
            status="success",
            milky_response=milky_response,
            current_state="accounting_queried",
            session_id=request.session_id,
            progress_percentage=progress,
            next_action="continue"
        )