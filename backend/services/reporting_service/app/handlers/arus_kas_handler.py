"""
Arus Kas Handler
Handles Cash Flow Statement (Laporan Arus Kas) generation

Extracted from grpc_server.py - IDENTIK, no logic changes
"""

import logging
from datetime import datetime
import grpc

from app.prisma_rls_extension import RLSPrismaClient
from queries.financial_queries import build_where_clause

logger = logging.getLogger(__name__)


class ArusKasHandler:
    """Handler for Arus Kas (Cash Flow Statement) operations"""
    
    @staticmethod
    async def query_arus_kas(rls_client: RLSPrismaClient, where: dict, pb) -> any:
        """
        Query and compute Laporan Arus Kas (Cash Flow Statement).
        
        Formula:
        - Kas Akhir = Kas Awal + Operasi + Investasi + Pendanaan
        """
        
        all_transactions = await rls_client.transaksiharian.find_many(where=where)
        
        # KAS AWAL PERIODE (from previous period - simplified to 0 for now)
        kas_awal_periode = 0  # TODO: Calculate from previous period's ending cash
        
        # ARUS KAS OPERASI
        penerimaan_dari_pelanggan = sum(
            tx.nominalDibayar or 0 for tx in all_transactions 
            if tx.kategoriArusKas == 'operasi' and tx.jenisTransaksi == 'penjualan'
        )
        pembayaran_ke_supplier = sum(
            tx.nominalDibayar or 0 for tx in all_transactions 
            if tx.kategoriArusKas == 'operasi' and tx.jenisTransaksi == 'pembelian'
        )
        pembayaran_beban_operasional = sum(
            tx.nominalDibayar or 0 for tx in all_transactions 
            if tx.kategoriArusKas == 'operasi' and tx.jenisTransaksi == 'beban'
        )
        arus_kas_operasi = penerimaan_dari_pelanggan - pembayaran_ke_supplier - pembayaran_beban_operasional
        
        # ARUS KAS INVESTASI
        pembelian_aset_tetap = sum(
            tx.totalNominal or 0 for tx in all_transactions 
            if tx.kategoriArusKas == 'investasi' and tx.jenisAset and 'aset_tidak_lancar' in tx.jenisAset
        )
        penjualan_aset_tetap = 0  # TODO: Add logic for asset sales
        arus_kas_investasi = -pembelian_aset_tetap + penjualan_aset_tetap
        
        # ARUS KAS PENDANAAN
        penerimaan_modal = sum(
            tx.totalNominal or 0 for tx in all_transactions 
            if tx.kategoriArusKas == 'pendanaan' and tx.isModal
        )
        penerimaan_pinjaman = 0  # TODO: Add logic
        pembayaran_pinjaman = 0  # TODO: Add logic
        prive_pemilik = sum(
            tx.totalNominal or 0 for tx in all_transactions 
            if tx.kategoriArusKas == 'pendanaan' and tx.isPrive
        )
        arus_kas_pendanaan = penerimaan_modal + penerimaan_pinjaman - pembayaran_pinjaman - prive_pemilik
        
        # KENAIKAN/PENURUNAN KAS
        kenaikan_penurunan_kas = arus_kas_operasi + arus_kas_investasi + arus_kas_pendanaan
        
        # KAS AKHIR PERIODE
        kas_akhir_periode = kas_awal_periode + kenaikan_penurunan_kas
        
        # VALIDASI (cross-check with Neraca)
        kas_actual = 0  # TODO: Get from Neraca query
        is_reconciled = True  # Simplified
        
        return pb.LaporanArusKas(
            tenant_id=where['tenantId'],
            periode_pelaporan=where.get('periodePelaporan', ''),
            generated_at=int(datetime.utcnow().timestamp() * 1000),
            kas_awal_periode=kas_awal_periode,
            penerimaan_dari_pelanggan=penerimaan_dari_pelanggan,
            pembayaran_ke_supplier=pembayaran_ke_supplier,
            pembayaran_beban_operasional=pembayaran_beban_operasional,
            arus_kas_operasi=arus_kas_operasi,
            pembelian_aset_tetap=pembelian_aset_tetap,
            penjualan_aset_tetap=penjualan_aset_tetap,
            arus_kas_investasi=arus_kas_investasi,
            penerimaan_modal=penerimaan_modal,
            penerimaan_pinjaman=penerimaan_pinjaman,
            pembayaran_pinjaman=pembayaran_pinjaman,
            prive_pemilik=prive_pemilik,
            arus_kas_pendanaan=arus_kas_pendanaan,
            kenaikan_penurunan_kas=kenaikan_penurunan_kas,
            kas_akhir_periode=kas_akhir_periode,
            kas_actual=kas_actual,
            is_reconciled=is_reconciled
        )
    
    @staticmethod
    async def handle_get_arus_kas(
        request,
        context: grpc.aio.ServicerContext,
        pb
    ):
        """Generate Laporan Arus Kas (Cash Flow Statement)"""
        logger.info(f"📊 GetArusKas: tenant={request.tenant_id}, periode={request.periode_pelaporan}")
        
        rls_client = RLSPrismaClient(tenant_id=request.tenant_id, bypass_rls=True)
        
        try:
            await rls_client.connect()
            
            where = build_where_clause(
                request.tenant_id,
                request.periode_pelaporan,
                request.start_date,
                request.end_date
            )
            
            result = await ArusKasHandler.query_arus_kas(rls_client, where, pb)
            logger.info(f"✅ Arus Kas generated: kas_akhir={result.kas_akhir_periode}")
            return result
            
        except Exception as e:
            logger.error(f"❌ GetArusKas failed: {str(e)}")
            await context.abort(grpc.StatusCode.INTERNAL, f"Failed to generate report: {str(e)}")
        finally:
            await rls_client.disconnect()