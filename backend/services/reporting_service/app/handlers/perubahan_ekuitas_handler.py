"""
Perubahan Ekuitas Handler
Handles Changes in Equity (Laporan Perubahan Ekuitas) generation

Extracted from grpc_server.py - IDENTIK, no logic changes
"""

import logging
from datetime import datetime
import grpc

from app.prisma_rls_extension import RLSPrismaClient
from queries.financial_queries import build_where_clause

logger = logging.getLogger(__name__)


class PerubahanEkuitasHandler:
    """Handler for Perubahan Ekuitas (Changes in Equity) operations"""
    
    @staticmethod
    async def query_perubahan_ekuitas(rls_client: RLSPrismaClient, where: dict, pb) -> any:
        """
        Query and compute Laporan Perubahan Ekuitas (Changes in Equity).
        
        Formula:
        - Ekuitas Akhir = Modal + Laba Ditahan - Prive
        """
        
        all_transactions = await rls_client.transaksiharian.find_many(where=where)
        
        # EKUITAS AWAL PERIODE (simplified to 0 for now)
        ekuitas_awal_periode = 0  # TODO: Calculate from previous period
        
        # MODAL
        modal_awal = 0  # TODO: Get from inception
        penambahan_modal = sum(tx.totalNominal or 0 for tx in all_transactions if tx.isModal)
        pengurangan_modal = 0  # Rarely happens
        modal_akhir = modal_awal + penambahan_modal - pengurangan_modal
        
        # LABA RUGI (get from Laba Rugi report)
        laba_bersih_periode_berjalan = 0  # TODO: Get from query_laba_rugi
        rugi_periode_berjalan = 0
        
        # PRIVE
        prive_periode_berjalan = sum(tx.totalNominal or 0 for tx in all_transactions if tx.isPrive)
        
        # LABA DITAHAN
        laba_ditahan_awal = 0  # TODO: Accumulated from previous periods
        laba_ditahan_akhir = laba_ditahan_awal + laba_bersih_periode_berjalan - prive_periode_berjalan
        
        # EKUITAS AKHIR PERIODE
        ekuitas_akhir_periode = modal_akhir + laba_ditahan_akhir
        
        # VALIDASI
        ekuitas_from_neraca = 0  # TODO: Cross-check with Neraca
        is_reconciled = True
        
        return pb.LaporanPerubahanEkuitas(
            tenant_id=where['tenantId'],
            periode_pelaporan=where.get('periodePelaporan', ''),
            generated_at=int(datetime.utcnow().timestamp() * 1000),
            ekuitas_awal_periode=ekuitas_awal_periode,
            modal_awal=modal_awal,
            penambahan_modal=penambahan_modal,
            pengurangan_modal=pengurangan_modal,
            modal_akhir=modal_akhir,
            laba_bersih_periode_berjalan=laba_bersih_periode_berjalan,
            rugi_periode_berjalan=rugi_periode_berjalan,
            prive_periode_berjalan=prive_periode_berjalan,
            laba_ditahan_awal=laba_ditahan_awal,
            laba_ditahan_akhir=laba_ditahan_akhir,
            ekuitas_akhir_periode=ekuitas_akhir_periode,
            ekuitas_from_neraca=ekuitas_from_neraca,
            is_reconciled=is_reconciled
        )
    
    @staticmethod
    async def handle_get_perubahan_ekuitas(
        request,
        context: grpc.aio.ServicerContext,
        pb
    ):
        """Generate Laporan Perubahan Ekuitas (Changes in Equity)"""
        logger.info(f"📊 GetPerubahanEkuitas: tenant={request.tenant_id}, periode={request.periode_pelaporan}")
        
        rls_client = RLSPrismaClient(tenant_id=request.tenant_id, bypass_rls=True)
        
        try:
            await rls_client.connect()
            
            where = build_where_clause(
                request.tenant_id,
                request.periode_pelaporan,
                request.start_date,
                request.end_date
            )
            
            result = await PerubahanEkuitasHandler.query_perubahan_ekuitas(rls_client, where, pb)
            logger.info(f"✅ Perubahan Ekuitas generated: ekuitas_akhir={result.ekuitas_akhir_periode}")
            return result
            
        except Exception as e:
            logger.error(f"❌ GetPerubahanEkuitas failed: {str(e)}")
            await context.abort(grpc.StatusCode.INTERNAL, f"Failed to generate report: {str(e)}")
        finally:
            await rls_client.disconnect()