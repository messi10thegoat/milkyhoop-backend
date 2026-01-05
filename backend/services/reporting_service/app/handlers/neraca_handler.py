"""
Neraca Handler
Handles Balance Sheet (Laporan Neraca) generation

Supports two data sources:
1. Legacy: transaksiharian table (Prisma)
2. New: Accounting Kernel General Ledger (asyncpg)

Set USE_ACCOUNTING_KERNEL=true to use the new data source.
"""

import logging
import os
from datetime import datetime
import grpc

from app.prisma_rls_extension import RLSPrismaClient
from queries.financial_queries import build_where_clause

logger = logging.getLogger(__name__)

# Feature flag for Accounting Kernel
USE_ACCOUNTING_KERNEL = os.getenv('USE_ACCOUNTING_KERNEL', 'false').lower() == 'true'


class NeracaHandler:
    """Handler for Neraca (Balance Sheet) operations"""
    
    @staticmethod
    async def query_neraca(rls_client: RLSPrismaClient, where: dict, pb) -> any:
        """
        Query and compute Laporan Neraca (Balance Sheet).
        
        Formula:
        - Total Aset = Aset Lancar + Aset Tidak Lancar
        - Total Ekuitas = Total Aset - Total Liabilitas
        """
        
        all_transactions = await rls_client.transaksiharian.find_many(where=where)
        
        # ASET LANCAR (Current Assets)
        kas = sum(tx.totalNominal or 0 for tx in all_transactions if tx.metodePembayaran == 'cash')
        bank = sum(tx.totalNominal or 0 for tx in all_transactions if tx.metodePembayaran in ['transfer', 'bank'])
        piutang_usaha = sum(
            tx.sisaPiutangHutang or 0 for tx in all_transactions 
            if (tx.sisaPiutangHutang or 0) > 0 and tx.pihakType == 'customer'
        )
        persediaan = 0  # TODO: Calculate from InventoryImpact
        total_aset_lancar = kas + bank + piutang_usaha + persediaan
        
        # ASET TIDAK LANCAR (Non-Current Assets)
        peralatan = sum(tx.totalNominal or 0 for tx in all_transactions if tx.jenisAset and 'peralatan' in tx.jenisAset)
        kendaraan = sum(tx.totalNominal or 0 for tx in all_transactions if tx.jenisAset and 'kendaraan' in tx.jenisAset)
        bangunan = sum(tx.totalNominal or 0 for tx in all_transactions if tx.jenisAset and 'bangunan' in tx.jenisAset)
        
        # Akumulasi penyusutan (negative value)
        akumulasi_penyusutan = -sum(
            (tx.penyusutanPerTahun or 0) * (tx.umurManfaat or 0) // 12 
            for tx in all_transactions if tx.penyusutanPerTahun
        )
        
        total_aset_tidak_lancar = peralatan + kendaraan + bangunan + akumulasi_penyusutan
        total_aset = total_aset_lancar + total_aset_tidak_lancar
        
        # LIABILITAS (Liabilities)
        hutang_usaha = sum(
            tx.sisaPiutangHutang or 0 for tx in all_transactions 
            if (tx.sisaPiutangHutang or 0) > 0 and tx.pihakType == 'supplier'
        )
        hutang_bank_jangka_pendek = 0  # TODO: Add logic for short-term loans
        total_liabilitas_jangka_pendek = hutang_usaha + hutang_bank_jangka_pendek
        
        hutang_bank_jangka_panjang = 0  # TODO: Add logic for long-term loans
        total_liabilitas_jangka_panjang = hutang_bank_jangka_panjang
        
        total_liabilitas = total_liabilitas_jangka_pendek + total_liabilitas_jangka_panjang
        
        # EKUITAS (Equity)
        modal_awal = sum(tx.totalNominal or 0 for tx in all_transactions if tx.isModal)
        laba_ditahan = 0  # TODO: Calculate from accumulated profits
        prive = -sum(tx.totalNominal or 0 for tx in all_transactions if tx.isPrive)
        total_ekuitas = modal_awal + laba_ditahan + prive
        
        # BALANCE CHECK
        total_liabilitas_dan_ekuitas = total_liabilitas + total_ekuitas
        is_balanced = abs(total_aset - total_liabilitas_dan_ekuitas) < 100  # Allow small rounding errors
        
        return pb.LaporanNeraca(
            tenant_id=where['tenantId'],
            periode_pelaporan=where.get('periodePelaporan', ''),
            generated_at=int(datetime.utcnow().timestamp() * 1000),
            kas=kas,
            bank=bank,
            piutang_usaha=piutang_usaha,
            persediaan=persediaan,
            total_aset_lancar=total_aset_lancar,
            peralatan=peralatan,
            kendaraan=kendaraan,
            bangunan=bangunan,
            akumulasi_penyusutan=akumulasi_penyusutan,
            total_aset_tidak_lancar=total_aset_tidak_lancar,
            total_aset=total_aset,
            hutang_usaha=hutang_usaha,
            hutang_bank_jangka_pendek=hutang_bank_jangka_pendek,
            total_liabilitas_jangka_pendek=total_liabilitas_jangka_pendek,
            hutang_bank_jangka_panjang=hutang_bank_jangka_panjang,
            total_liabilitas_jangka_panjang=total_liabilitas_jangka_panjang,
            total_liabilitas=total_liabilitas,
            modal_awal=modal_awal,
            laba_ditahan=laba_ditahan,
            prive=prive,
            total_ekuitas=total_ekuitas,
            total_liabilitas_dan_ekuitas=total_liabilitas_dan_ekuitas,
            is_balanced=is_balanced
        )
    
    @staticmethod
    async def handle_get_neraca(
        request,
        context: grpc.aio.ServicerContext,
        pb
    ):
        """Generate Laporan Neraca (Balance Sheet)"""
        logger.info(f"📊 GetNeraca: tenant={request.tenant_id}, periode={request.periode_pelaporan}, use_kernel={USE_ACCOUNTING_KERNEL}")

        # Use Accounting Kernel if enabled
        if USE_ACCOUNTING_KERNEL:
            try:
                from adapters.accounting_kernel_adapter import get_kernel_adapter
                adapter = await get_kernel_adapter()

                data = await adapter.get_neraca(
                    tenant_id=request.tenant_id,
                    periode=request.periode_pelaporan,
                    company_name=getattr(request, 'company_name', '')
                )

                # Map accounting kernel format to Proto format
                result = pb.LaporanNeraca(
                    tenant_id=data['tenant_id'],
                    periode_pelaporan=data['periode_pelaporan'],
                    generated_at=data['generated_at'],
                    # Current Assets
                    kas=data.get('kas_dan_setara_kas', 0),
                    bank=0,  # Merged into kas_dan_setara_kas
                    piutang_usaha=data.get('piutang_usaha', 0),
                    persediaan=data.get('persediaan', 0),
                    total_aset_lancar=data.get('total_aset_lancar', 0),
                    # Fixed Assets
                    peralatan=data.get('aset_tetap', 0),
                    kendaraan=0,
                    bangunan=0,
                    akumulasi_penyusutan=data.get('akumulasi_penyusutan', 0),
                    total_aset_tidak_lancar=data.get('aset_tetap_neto', 0),
                    total_aset=data.get('total_aset', 0),
                    # Liabilities
                    hutang_usaha=data.get('hutang_usaha', 0),
                    hutang_bank_jangka_pendek=0,
                    total_liabilitas_jangka_pendek=data.get('total_liabilitas_lancar', 0),
                    hutang_bank_jangka_panjang=data.get('hutang_jangka_panjang', 0),
                    total_liabilitas_jangka_panjang=data.get('hutang_jangka_panjang', 0),
                    total_liabilitas=data.get('total_liabilitas', 0),
                    # Equity
                    modal_awal=data.get('modal_pemilik', 0),
                    laba_ditahan=data.get('laba_ditahan', 0),
                    prive=0,
                    total_ekuitas=data.get('total_ekuitas', 0),
                    total_liabilitas_dan_ekuitas=data.get('total_liabilitas_ekuitas', 0),
                    is_balanced=data.get('is_balanced', False)
                )
                logger.info(f"✅ Neraca (Kernel): total_aset={result.total_aset}, balanced={result.is_balanced}")
                return result

            except Exception as e:
                logger.error(f"❌ Kernel failed, falling back to legacy: {e}")
                # Fall through to legacy implementation

        # Legacy implementation using transaksiharian
        rls_client = RLSPrismaClient(tenant_id=request.tenant_id, bypass_rls=True)

        try:
            await rls_client.connect()

            where = build_where_clause(
                request.tenant_id,
                request.periode_pelaporan,
                request.start_date,
                request.end_date
            )

            result = await NeracaHandler.query_neraca(rls_client, where, pb)
            logger.info(f"✅ Neraca (Legacy): total_aset={result.total_aset}, balanced={result.is_balanced}")
            return result

        except Exception as e:
            logger.error(f"❌ GetNeraca failed: {str(e)}")
            await context.abort(grpc.StatusCode.INTERNAL, f"Failed to generate report: {str(e)}")
        finally:
            await rls_client.disconnect()