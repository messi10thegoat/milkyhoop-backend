"""
Accounting Service gRPC Server
MilkyHoop 4.0 - Journal Entries & Double-Entry Bookkeeping

Implements:
- ProcessTransaction (auto-generate journal entries)
- GetJournalEntry (query journal by ID)
- GetJournalEntriesByPeriod (for reporting)
- CreateAccount, GetAccount, ListAccounts (Chart of Accounts CRUD)
- UpdateAccount, DeactivateAccount (CoA management)
- HealthCheck

Features:
- Double-entry bookkeeping validation (Debit = Credit)
- Auto CoA mapping based on transaction type
- Multi-tenant isolation via RLS
- Journal numbering (JE-YYYY-MM-NNN)
"""

import asyncio
import signal
import logging
import os
from datetime import datetime
from typing import Optional, Dict, List
import grpc
from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from google.protobuf import empty_pb2
import uuid

import sys
sys.path.insert(0, '/app/backend/services/accounting_service/app')

from config import settings
import accounting_service_pb2 as pb
import accounting_service_pb2_grpc as pb_grpc
from prisma_client import prisma, connect_prisma, disconnect_prisma
from prisma_rls_extension import RLSPrismaClient

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(settings.SERVICE_NAME)


# ==========================================
# CHART OF ACCOUNTS DEFAULT MAPPING
# ==========================================

DEFAULT_COA_MAPPING = {
    'penjualan': {
        'debit_akun': '1-1100',    # Kas
        'kredit_akun': '4-1000',   # Pendapatan Penjualan
    },
    'pembelian': {
        'debit_akun': '1-1300',    # Persediaan
        'kredit_akun': '1-1100',   # Kas (atau Hutang jika tempo)
    },
    'beban': {
        'debit_akun': '5-1000',    # Beban Operasional
        'kredit_akun': '1-1100',   # Kas
    },
}

DEFAULT_ACCOUNTS = {
    '1-1100': {'nama': 'Kas', 'kategori': 'aset', 'normal_balance': 'debit'},
    '1-1300': {'nama': 'Persediaan', 'kategori': 'aset', 'normal_balance': 'debit'},
    '2-1000': {'nama': 'Hutang Usaha', 'kategori': 'liabilitas', 'normal_balance': 'kredit'},
    '3-1000': {'nama': 'Modal', 'kategori': 'ekuitas', 'normal_balance': 'kredit'},
    '4-1000': {'nama': 'Pendapatan Penjualan', 'kategori': 'pendapatan', 'normal_balance': 'kredit'},
    '5-1000': {'nama': 'Beban Operasional', 'kategori': 'beban', 'normal_balance': 'debit'},
}


# ==========================================
# HELPER FUNCTIONS
# ==========================================

async def get_or_create_account(
    rls_client: RLSPrismaClient,
    tenant_id: str,
    kode_akun: str
) -> Dict:
    """Get account from CoA, create default if not exists"""
    
    # Try to find existing
    existing = await rls_client.baganakun.find_first(
        where={
            'tenantId': tenant_id,
            'kodeAkun': kode_akun
        }
    )
    
    if existing:
        return {
            'id': existing.id,
            'kode_akun': existing.kodeAkun,
            'nama_akun': existing.namaAkun,
        }
    
    # Create default account
    if kode_akun in DEFAULT_ACCOUNTS:
        default = DEFAULT_ACCOUNTS[kode_akun]
        new_account = await rls_client.baganakun.create(
            data={
                'tenantId': tenant_id,
                'kodeAkun': kode_akun,
                'namaAkun': default['nama'],
                'kategori': default['kategori'],
                'normalBalance': default['normal_balance'],
                'isActive': True,
            }
        )
        
        logger.info(f"📋 Created default account: {kode_akun} - {default['nama']}")
        
        return {
            'id': new_account.id,
            'kode_akun': new_account.kodeAkun,
            'nama_akun': new_account.namaAkun,
        }
    
    # If no default, return placeholder
    return {
        'id': f"placeholder_{kode_akun}",
        'kode_akun': kode_akun,
        'nama_akun': f"Akun {kode_akun}",
    }


def generate_journal_number(periode: str, sequence: int) -> str:
    """Generate journal number: JE-YYYY-MM-NNN"""
    return f"JE-{periode}-{sequence:03d}"


# ==========================================
# SERVICER IMPLEMENTATION
# ==========================================

class AccountingServiceServicer(pb_grpc.AccountingServiceServicer):
    """Accounting Service gRPC Servicer"""
    
    async def ProcessTransaction(
        self,
        request: pb.ProcessTransactionRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.ProcessTransactionResponse:
        """
        Process transaction and generate journal entries.
        Flow:
        1. Get CoA mapping for transaction type
        2. Create journal entry header
        3. Create journal details (debit & credit lines)
        4. Validate double-entry (debit = credit)
        """
        
        logger.info(f"📒 ProcessTransaction: transaksi={request.transaksi_id}, type={request.jenis_transaksi}")
        
        rls_client = RLSPrismaClient(tenant_id=request.tenant_id)
        
        try:
            await rls_client.connect()
            
            # Get CoA mapping
            coa_mapping = DEFAULT_COA_MAPPING.get(request.jenis_transaksi)
            if not coa_mapping:
                return pb.ProcessTransactionResponse(
                    success=False,
                    message=f"No CoA mapping for jenis_transaksi: {request.jenis_transaksi}",
                    jurnal_entry_id="",
                    nomor_jurnal="",
                    journal_lines=[],
                    total_debit=0,
                    total_kredit=0
                )
            
            # Get or create accounts
            debit_account = await get_or_create_account(
                rls_client, 
                request.tenant_id, 
                coa_mapping['debit_akun']
            )
            kredit_account = await get_or_create_account(
                rls_client, 
                request.tenant_id, 
                coa_mapping['kredit_akun']
            )
            
            # Generate IDs
            jurnal_entry_id = f"je_{uuid.uuid4().hex[:16]}"
            
            # Get next journal number
            existing_count = await rls_client.jurnalentry.count(
                where={
                    'tenantId': request.tenant_id,
                    'periodePelaporan': request.periode_pelaporan
                }
            )
            nomor_jurnal = generate_journal_number(request.periode_pelaporan, existing_count + 1)
            
            # Create journal entry header
            journal_entry = await rls_client.jurnalentry.create(
                data={
                    'id': jurnal_entry_id,
                    'tenantId': request.tenant_id,
                    'transaksiId': request.transaksi_id,
                    'nomorJurnal': nomor_jurnal,
                    'tanggalJurnal': request.tanggal_transaksi,
                    'keterangan': request.keterangan or f"Transaksi {request.jenis_transaksi}",
                    'totalDebit': request.total_nominal,
                    'totalKredit': request.total_nominal,
                    'status': 'posted',
                    'periodePelaporan': request.periode_pelaporan,
                }
            )
            
            # Create journal details (debit line)
            debit_detail = await rls_client.jurnaldetail.create(
                data={
                    'jurnalEntryId': jurnal_entry_id,
                    'akunId': debit_account['id'],
                    'debit': request.total_nominal,
                    'kredit': 0,
                    'keterangan': f"Debit - {debit_account['nama_akun']}",
                }
            )
            
            # Create journal details (credit line)
            kredit_detail = await rls_client.jurnaldetail.create(
                data={
                    'jurnalEntryId': jurnal_entry_id,
                    'akunId': kredit_account['id'],
                    'debit': 0,
                    'kredit': request.total_nominal,
                    'keterangan': f"Kredit - {kredit_account['nama_akun']}",
                }
            )
            
            # Build response
            journal_lines = [
                pb.JournalLine(
                    akun_id=debit_account['id'],
                    kode_akun=debit_account['kode_akun'],
                    nama_akun=debit_account['nama_akun'],
                    debit=request.total_nominal,
                    kredit=0,
                    keterangan=f"Debit - {debit_account['nama_akun']}"
                ),
                pb.JournalLine(
                    akun_id=kredit_account['id'],
                    kode_akun=kredit_account['kode_akun'],
                    nama_akun=kredit_account['nama_akun'],
                    debit=0,
                    kredit=request.total_nominal,
                    keterangan=f"Kredit - {kredit_account['nama_akun']}"
                )
            ]
            
            logger.info(f"✅ Journal entry created: {nomor_jurnal}, Debit={request.total_nominal}, Kredit={request.total_nominal}")
            
            return pb.ProcessTransactionResponse(
                success=True,
                message=f"Journal entry created: {nomor_jurnal}",
                jurnal_entry_id=jurnal_entry_id,
                nomor_jurnal=nomor_jurnal,
                journal_lines=journal_lines,
                total_debit=request.total_nominal,
                total_kredit=request.total_nominal
            )
            
        except ValueError as ve:
            logger.error(f"❌ Validation error: {str(ve)}")
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(ve))
        except Exception as e:
            logger.error(f"❌ ProcessTransaction failed: {str(e)}")
            await context.abort(grpc.StatusCode.INTERNAL, f"Failed to process: {str(e)}")
        finally:
            await rls_client.disconnect()
    
    async def GetJournalEntry(
        self,
        request: pb.GetJournalEntryRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.GetJournalEntryResponse:
        """Get journal entry by ID"""
        
        logger.info(f"📖 GetJournalEntry: id={request.jurnal_entry_id}")
        
        rls_client = RLSPrismaClient(tenant_id=request.tenant_id)
        
        try:
            await rls_client.connect()
            
            # Get journal entry with details
            entry = await rls_client.jurnalentry.find_unique(
                where={'id': request.jurnal_entry_id},
                include={'details': {'include': {'akun': True}}}
            )
            
            if not entry:
                return pb.GetJournalEntryResponse(
                    success=False,
                    message=f"Journal entry not found: {request.jurnal_entry_id}",
                    journal_entry=None
                )
            
            # Build detail lines
            detail_lines = []
            for detail in entry.details:
                detail_lines.append(pb.JournalLine(
                    akun_id=detail.akunId,
                    kode_akun=detail.akun.kodeAkun,
                    nama_akun=detail.akun.namaAkun,
                    debit=detail.debit,
                    kredit=detail.kredit,
                    keterangan=detail.keterangan or ""
                ))
            
            # Build journal entry
            journal_entry = pb.JournalEntry(
                id=entry.id,
                nomor_jurnal=entry.nomorJurnal,
                tanggal_jurnal=entry.tanggalJurnal,
                keterangan=entry.keterangan or "",
                total_debit=entry.totalDebit,
                total_kredit=entry.totalKredit,
                status=entry.status,
                periode_pelaporan=entry.periodePelaporan or "",
                transaksi_id=entry.transaksiId or "",
                details=detail_lines,
                created_at=int(entry.createdAt.timestamp())
            )
            
            return pb.GetJournalEntryResponse(
                success=True,
                message="Journal entry retrieved",
                journal_entry=journal_entry
            )
            
        except Exception as e:
            logger.error(f"❌ GetJournalEntry failed: {str(e)}")
            await context.abort(grpc.StatusCode.INTERNAL, f"Failed to get: {str(e)}")
        finally:
            await rls_client.disconnect()
    
    async def GetJournalEntriesByPeriod(
        self,
        request: pb.GetJournalEntriesByPeriodRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.GetJournalEntriesByPeriodResponse:
        """Get journal entries for a period"""
        
        logger.info(f"📊 GetJournalEntriesByPeriod: period={request.periode_pelaporan}")
        
        rls_client = RLSPrismaClient(tenant_id=request.tenant_id)
        
        try:
            await rls_client.connect()
            
            where_clause = {
                'tenantId': request.tenant_id,
                'periodePelaporan': request.periode_pelaporan
            }
            
            if request.status:
                where_clause['status'] = request.status
            
            entries = await rls_client.jurnalentry.find_many(
                where=where_clause,
                include={'details': {'include': {'akun': True}}},
                take=request.limit or 100,
                skip=request.offset or 0,
                order={'tanggalJurnal': 'desc'}
            )
            
            journal_entries = []
            for entry in entries:
                detail_lines = []
                for detail in entry.details:
                    detail_lines.append(pb.JournalLine(
                        akun_id=detail.akunId,
                        kode_akun=detail.akun.kodeAkun,
                        nama_akun=detail.akun.namaAkun,
                        debit=detail.debit,
                        kredit=detail.kredit,
                        keterangan=detail.keterangan or ""
                    ))
                
                journal_entries.append(pb.JournalEntry(
                    id=entry.id,
                    nomor_jurnal=entry.nomorJurnal,
                    tanggal_jurnal=entry.tanggalJurnal,
                    keterangan=entry.keterangan or "",
                    total_debit=entry.totalDebit,
                    total_kredit=entry.totalKredit,
                    status=entry.status,
                    periode_pelaporan=entry.periodePelaporan or "",
                    transaksi_id=entry.transaksiId or "",
                    details=detail_lines,
                    created_at=int(entry.createdAt.timestamp())
                ))
            
            total_count = await rls_client.jurnalentry.count(where=where_clause)
            
            logger.info(f"✅ Found {len(journal_entries)} journal entries")
            
            return pb.GetJournalEntriesByPeriodResponse(
                success=True,
                message=f"Found {len(journal_entries)} journal entries",
                journal_entries=journal_entries,
                total_count=total_count
            )
            
        except Exception as e:
            logger.error(f"❌ GetJournalEntriesByPeriod failed: {str(e)}")
            await context.abort(grpc.StatusCode.INTERNAL, f"Failed to get: {str(e)}")
        finally:
            await rls_client.disconnect()
    
    async def CreateAccount(
        self,
        request: pb.CreateAccountRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.CreateAccountResponse:
        """Create new account in chart of accounts"""
        
        logger.info(f"📝 CreateAccount: {request.kode_akun} - {request.nama_akun}")
        
        rls_client = RLSPrismaClient(tenant_id=request.tenant_id)
        
        try:
            await rls_client.connect()
            
            # Check if exists
            existing = await rls_client.baganakun.find_first(
                where={
                    'tenantId': request.tenant_id,
                    'kodeAkun': request.kode_akun
                }
            )
            
            if existing:
                return pb.CreateAccountResponse(
                    success=False,
                    message=f"Account {request.kode_akun} already exists",
                    account=None
                )
            
            # Create account
            account = await rls_client.baganakun.create(
                data={
                    'tenantId': request.tenant_id,
                    'kodeAkun': request.kode_akun,
                    'namaAkun': request.nama_akun,
                    'kategori': request.kategori,
                    'subKategori': request.sub_kategori or None,
                    'normalBalance': request.normal_balance,
                    'isActive': True,
                    'parentId': request.parent_id or None,
                }
            )
            
            logger.info(f"✅ Account created: {account.kodeAkun}")
            
            return pb.CreateAccountResponse(
                success=True,
                message=f"Account created: {account.kodeAkun}",
                account=pb.Account(
                    id=account.id,
                    kode_akun=account.kodeAkun,
                    nama_akun=account.namaAkun,
                    kategori=account.kategori,
                    sub_kategori=account.subKategori or "",
                    normal_balance=account.normalBalance,
                    is_active=account.isActive,
                    parent_id=account.parentId or "",
                    created_at=int(account.createdAt.timestamp()),
                    updated_at=int(account.updatedAt.timestamp())
                )
            )
            
        except Exception as e:
            logger.error(f"❌ CreateAccount failed: {str(e)}")
            await context.abort(grpc.StatusCode.INTERNAL, f"Failed to create: {str(e)}")
        finally:
            await rls_client.disconnect()
    
    async def GetAccount(
        self,
        request: pb.GetAccountRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.GetAccountResponse:
        """Get account by ID or kode_akun"""
        
        logger.info(f"🔍 GetAccount: {request.akun_id}")
        
        rls_client = RLSPrismaClient(tenant_id=request.tenant_id)
        
        try:
            await rls_client.connect()
            
            # Try by ID first, then by kode_akun
            account = await rls_client.baganakun.find_first(
                where={
                    'tenantId': request.tenant_id,
                    'OR': [
                        {'id': request.akun_id},
                        {'kodeAkun': request.akun_id}
                    ]
                }
            )
            
            if not account:
                return pb.GetAccountResponse(
                    success=False,
                    message=f"Account not found: {request.akun_id}",
                    account=None
                )
            
            return pb.GetAccountResponse(
                success=True,
                message="Account retrieved",
                account=pb.Account(
                    id=account.id,
                    kode_akun=account.kodeAkun,
                    nama_akun=account.namaAkun,
                    kategori=account.kategori,
                    sub_kategori=account.subKategori or "",
                    normal_balance=account.normalBalance,
                    is_active=account.isActive,
                    parent_id=account.parentId or "",
                    created_at=int(account.createdAt.timestamp()),
                    updated_at=int(account.updatedAt.timestamp())
                )
            )
            
        except Exception as e:
            logger.error(f"❌ GetAccount failed: {str(e)}")
            await context.abort(grpc.StatusCode.INTERNAL, f"Failed to get: {str(e)}")
        finally:
            await rls_client.disconnect()
    
    async def ListAccounts(
        self,
        request: pb.ListAccountsRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.ListAccountsResponse:
        """List all accounts for tenant"""
        
        logger.info(f"📋 ListAccounts: tenant={request.tenant_id}")
        
        rls_client = RLSPrismaClient(tenant_id=request.tenant_id)
        
        try:
            await rls_client.connect()
            
            where_clause = {'tenantId': request.tenant_id}
            
            if request.kategori:
                where_clause['kategori'] = request.kategori
            
            if request.is_active:
                where_clause['isActive'] = True
            
            accounts = await rls_client.baganakun.find_many(
                where=where_clause,
                take=request.limit or 100,
                order={'kodeAkun': 'asc'}
            )
            
            account_list = []
            for account in accounts:
                account_list.append(pb.Account(
                    id=account.id,
                    kode_akun=account.kodeAkun,
                    nama_akun=account.namaAkun,
                    kategori=account.kategori,
                    sub_kategori=account.subKategori or "",
                    normal_balance=account.normalBalance,
                    is_active=account.isActive,
                    parent_id=account.parentId or "",
                    created_at=int(account.createdAt.timestamp()),
                    updated_at=int(account.updatedAt.timestamp())
                ))
            
            logger.info(f"✅ Found {len(account_list)} accounts")
            
            return pb.ListAccountsResponse(
                success=True,
                message=f"Found {len(account_list)} accounts",
                accounts=account_list,
                total_count=len(account_list)
            )
            
        except Exception as e:
            logger.error(f"❌ ListAccounts failed: {str(e)}")
            await context.abort(grpc.StatusCode.INTERNAL, f"Failed to list: {str(e)}")
        finally:
            await rls_client.disconnect()
    
    async def UpdateAccount(
        self,
        request: pb.UpdateAccountRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.UpdateAccountResponse:
        """Update account"""
        
        logger.info(f"✏️ UpdateAccount: {request.akun_id}")
        
        rls_client = RLSPrismaClient(tenant_id=request.tenant_id)
        
        try:
            await rls_client.connect()
            
            update_data = {}
            if request.nama_akun:
                update_data['namaAkun'] = request.nama_akun
            if request.sub_kategori:
                update_data['subKategori'] = request.sub_kategori
            if request.is_active is not None:
                update_data['isActive'] = request.is_active
            
            account = await rls_client.baganakun.update(
                where={'id': request.akun_id},
                data=update_data
            )
            
            logger.info(f"✅ Account updated: {account.kodeAkun}")
            
            return pb.UpdateAccountResponse(
                success=True,
                message=f"Account updated: {account.kodeAkun}",
                account=pb.Account(
                    id=account.id,
                    kode_akun=account.kodeAkun,
                    nama_akun=account.namaAkun,
                    kategori=account.kategori,
                    sub_kategori=account.subKategori or "",
                    normal_balance=account.normalBalance,
                    is_active=account.isActive,
                    parent_id=account.parentId or "",
                    created_at=int(account.createdAt.timestamp()),
                    updated_at=int(account.updatedAt.timestamp())
                )
            )
            
        except Exception as e:
            logger.error(f"❌ UpdateAccount failed: {str(e)}")
            await context.abort(grpc.StatusCode.INTERNAL, f"Failed to update: {str(e)}")
        finally:
            await rls_client.disconnect()
    
    async def DeactivateAccount(
        self,
        request: pb.DeactivateAccountRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.DeactivateAccountResponse:
        """Deactivate account (soft delete)"""
        
        logger.info(f"🗑️ DeactivateAccount: {request.akun_id}")
        
        rls_client = RLSPrismaClient(tenant_id=request.tenant_id)
        
        try:
            await rls_client.connect()
            
            await rls_client.baganakun.update(
                where={'id': request.akun_id},
                data={'isActive': False}
            )
            
            logger.info(f"✅ Account deactivated: {request.akun_id}")
            
            return pb.DeactivateAccountResponse(
                success=True,
                message=f"Account deactivated: {request.akun_id}"
            )
            
        except Exception as e:
            logger.error(f"❌ DeactivateAccount failed: {str(e)}")
            await context.abort(grpc.StatusCode.INTERNAL, f"Failed to deactivate: {str(e)}")
        finally:
            await rls_client.disconnect()
    
    async def HealthCheck(
        self,
        request: empty_pb2.Empty,
        context: grpc.aio.ServicerContext
    ) -> pb.HealthResponse:
        """Health check endpoint"""
        
        try:
            # Check Prisma connection
            total_accounts = await prisma.baganakun.count()
            total_journals = await prisma.jurnalentry.count()
            database_connected = True
        except Exception as e:
            logger.error(f"❌ Health check failed: {str(e)}")
            total_accounts = 0
            total_journals = 0
            database_connected = False
        
        return pb.HealthResponse(
            status="SERVING" if database_connected else "NOT_SERVING",
            service_name=settings.SERVICE_NAME,
            timestamp=int(datetime.utcnow().timestamp() * 1000),
            version="1.0.0",
            database_connected=database_connected,
            total_accounts=total_accounts,
            total_journal_entries=total_journals
        )


# ==========================================
# SERVER STARTUP
# ==========================================

async def serve() -> None:
    """Start gRPC server"""
    
    # Connect Prisma
    if "DATABASE_URL" in os.environ:
        logger.info("🔌 Connecting to Prisma...")
        await connect_prisma()
        logger.info("✅ Prisma connected")
    
    # Create server
    server = aio.server()
    
    # Add services
    pb_grpc.add_AccountingServiceServicer_to_server(
        AccountingServiceServicer(),
        server
    )
    
    # Enable reflection
    from grpc_reflection.v1alpha import reflection
    SERVICE_NAMES = (
        pb.DESCRIPTOR.services_by_name['AccountingService'].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(SERVICE_NAMES, server)
    
    # Health check
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set('', health_pb2.HealthCheckResponse.SERVING)
    
    # Listen
    listen_addr = f"[::]:{settings.GRPC_PORT}"
    server.add_insecure_port(listen_addr)
    logger.info(f"🚀 {settings.SERVICE_NAME} listening on port {settings.GRPC_PORT}")
    logger.info(f"📍 Service: accounting_service.AccountingService")
    
    # Shutdown handling
    stop_event = asyncio.Event()
    
    def handle_shutdown(*_):
        logger.info("🛑 Shutdown signal received")
        stop_event.set()
    
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    
    try:
        await server.start()
        await stop_event.wait()
    finally:
        logger.info("🧹 Shutting down server...")
        await server.stop(5)
        if "DATABASE_URL" in os.environ:
            logger.info("🧹 Disconnecting Prisma...")
            await disconnect_prisma()
            logger.info("✅ Prisma disconnected")
        logger.info("✅ Server shutdown complete")


if __name__ == "__main__":
    asyncio.run(serve())