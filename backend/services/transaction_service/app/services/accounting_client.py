"""
Accounting Service gRPC Client for Transaction Service
Handles automatic journal entry creation when transactions are approved.
"""

import grpc
from grpc import aio
import logging
import sys

# Import accounting service stubs
sys.path.insert(0, '/app/backend/services/transaction_service/app')
import accounting_service_pb2 as acc_pb
import accounting_service_pb2_grpc as acc_grpc

logger = logging.getLogger(__name__)

# Global channel and stub (singleton pattern)
_accounting_channel = None
_accounting_stub = None


def get_accounting_client():
    """
    Get or create accounting service gRPC stub (singleton).
    
    Returns:
        AccountingServiceStub: gRPC stub for accounting service
    """
    global _accounting_channel, _accounting_stub
    
    if _accounting_stub is None:
        logger.info("🔌 Connecting to AccountingService on accounting_service:7050")
        _accounting_channel = aio.insecure_channel('accounting_service:7050')
        _accounting_stub = acc_grpc.AccountingServiceStub(_accounting_channel)
        logger.info("✅ AccountingService client initialized")
    
    return _accounting_stub


async def close_accounting_client():
    """
    Close accounting service gRPC channel gracefully.
    """
    global _accounting_channel
    
    if _accounting_channel:
        logger.info("🔌 Closing AccountingService connection")
        await _accounting_channel.close()
        _accounting_channel = None
        logger.info("✅ AccountingService connection closed")


async def process_transaction_accounting(
    tenant_id: str,
    transaksi_id: str,
    jenis_transaksi: str,
    total_nominal: int,
    kategori_arus_kas: str,
    created_by: str,
    tanggal_transaksi: int,
    periode_pelaporan: str,
    keterangan: str = "",
    akun_perkiraan_id: str = ""
) -> dict:
    """
    Process transaction in accounting service to create journal entry.
    
    Args:
        tenant_id: Tenant identifier
        transaksi_id: Transaction ID from transaction_service
        jenis_transaksi: Transaction type ('penjualan', 'pembelian', 'beban')
        total_nominal: Total amount in cents
        kategori_arus_kas: Cash flow category ('operasi', 'investasi', 'pendanaan')
        created_by: User ID who created the transaction
        tanggal_transaksi: Transaction timestamp (Unix seconds)
        periode_pelaporan: Reporting period (e.g., '2025-11')
        keterangan: Transaction notes (optional)
        akun_perkiraan_id: Chart of accounts ID (optional, AI auto-assigns if empty)
    
    Returns:
        dict: Result with success status, journal_id, and journal_number
    
    Raises:
        grpc.RpcError: If accounting service call fails
    """
    client = get_accounting_client()
    
    logger.info(f"📒 Processing accounting for transaction {transaksi_id}")
    
    request = acc_pb.ProcessTransactionRequest(
        tenant_id=tenant_id,
        transaksi_id=transaksi_id,
        jenis_transaksi=jenis_transaksi,
        total_nominal=total_nominal,
        kategori_arus_kas=kategori_arus_kas,
        akun_perkiraan_id=akun_perkiraan_id,
        created_by=created_by,
        tanggal_transaksi=tanggal_transaksi,
        periode_pelaporan=periode_pelaporan,
        keterangan=keterangan
    )
    
    try:
        response = await client.ProcessTransaction(request)
        
        if response.success:
            logger.info(f"✅ Journal entry created: {response.nomor_jurnal}")
            logger.info(f"   Journal ID: {response.jurnal_entry_id}")
            logger.info(f"   Debit: {response.total_debit / 100:,.0f}, Kredit: {response.total_kredit / 100:,.0f}")
            
            return {
                "success": True,
                "journal_id": response.jurnal_entry_id,
                "journal_number": response.nomor_jurnal,
                "total_debit": response.total_debit,
                "total_kredit": response.total_kredit,
                "message": response.message
            }
        else:
            logger.error(f"❌ Accounting failed: {response.message}")
            return {
                "success": False,
                "error": response.message
            }
    
    except grpc.RpcError as e:
        logger.error(f"❌ AccountingService RPC error: {e.code()} - {e.details()}")
        return {
            "success": False,
            "error": f"RPC Error: {e.code().name} - {e.details()}"
        }
    except Exception as e:
        logger.error(f"❌ Unexpected error calling AccountingService: {str(e)}")
        return {
            "success": False,
            "error": f"Unexpected error: {str(e)}"
        }


async def get_journal_by_transaction(tenant_id: str, transaksi_id: str) -> dict:
    """
    Get journal entry associated with a transaction.
    
    Args:
        tenant_id: Tenant identifier
        transaksi_id: Transaction ID
    
    Returns:
        dict: Journal entry data or error
    """
    client = get_accounting_client()
    
    logger.info(f"📒 Querying journal for transaction {transaksi_id}")
    
    # Note: This requires implementing GetJournalByTransaction RPC in accounting_service
    # For now, this is a placeholder for future implementation
    
    return {
        "success": False,
        "error": "GetJournalByTransaction not yet implemented"
    }


async def health_check_accounting() -> bool:
    """
    Check if accounting service is healthy and reachable.
    
    Returns:
        bool: True if healthy, False otherwise
    """
    client = get_accounting_client()
    
    try:
        from google.protobuf import empty_pb2
        response = await client.HealthCheck(empty_pb2.Empty())
        
        if response.status == "SERVING":
            logger.info("✅ AccountingService is healthy")
            return True
        else:
            logger.warning(f"⚠️ AccountingService status: {response.status}")
            return False
    
    except grpc.RpcError as e:
        logger.error(f"❌ AccountingService health check failed: {e.code()}")
        return False
    except Exception as e:
        logger.error(f"❌ Unexpected error in health check: {str(e)}")
        return False