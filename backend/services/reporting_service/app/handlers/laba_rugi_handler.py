"""
Laba Rugi Handler
Handles Income Statement (Laporan Laba Rugi) generation

Extracted from grpc_server.py - IDENTIK, no logic changes
"""

import logging
from datetime import datetime
import grpc

from app.prisma_rls_extension import RLSPrismaClient
from queries.financial_queries import build_where_clause

logger = logging.getLogger(__name__)


class LabaRugiHandler:
    """Handler for Laba Rugi (Income Statement) operations"""
    
    @staticmethod
    async def query_laba_rugi(rls_client: RLSPrismaClient, where: dict, pb, prisma) -> any:
        """
        Query and compute Laporan Laba Rugi (Income Statement).
        
        Formula:
        - Laba Kotor = Pendapatan - HPP
        - Laba Bersih = Laba Kotor - Beban Operasional
        """
        
        # PENDAPATAN (Revenue)
        pendapatan_penjualan_list = await rls_client.transaksiharian.find_many(
            where={**where, 'jenisTransaksi': 'penjualan'}
        )
        pendapatan_penjualan = sum(tx.totalNominal or 0 for tx in pendapatan_penjualan_list)
        
        logger.info(f"💰 Found {len(pendapatan_penjualan_list)} penjualan transactions")
        logger.info(f"💰 Total pendapatan: Rp{pendapatan_penjualan:,}")
        for tx in pendapatan_penjualan_list:
            logger.info(f"   ├─ {tx.id}: Rp{tx.totalNominal:,} at {tx.timestamp}")
        
        # HPP (Cost of Goods Sold) - dari transaksi PEMBELIAN
        pembelian_list = await rls_client.transaksiharian.find_many(
            where={**where, 'jenisTransaksi': 'pembelian'}
        )
        total_hpp = sum(tx.totalNominal or 0 for tx in pembelian_list)
        
        logger.info(f"📦 Found {len(pembelian_list)} pembelian transactions")
        logger.info(f"📦 Total HPP: Rp{total_hpp:,}")
        for tx in pembelian_list:
            logger.info(f"   ├─ {tx.id}: Rp{tx.totalNominal:,} at {tx.timestamp}")
        
        # Breakdown HPP (optional - set to 0 if no hppbreakdown data)
        hpp_bahan_baku = 0
        hpp_tenaga_kerja = 0
        hpp_overhead = 0
        
        # BEBAN (Expenses)
        beban_list = await rls_client.transaksiharian.find_many(
            where={**where, 'jenisTransaksi': 'beban'}
        )
        
        logger.info(f"💸 Found {len(beban_list)} beban transactions")
        for tx in beban_list:
            logger.info(f"   ├─ {tx.id}: Rp{tx.totalNominal:,} ({tx.kategoriBeban}) at {tx.timestamp}")
        
        beban_gaji = sum(tx.totalNominal or 0 for tx in beban_list if tx.kategoriBeban == 'beban_gaji')
        beban_sewa = sum(tx.totalNominal or 0 for tx in beban_list if tx.kategoriBeban == 'beban_sewa')
        beban_utilitas = sum(tx.totalNominal or 0 for tx in beban_list if tx.kategoriBeban == 'beban_utilitas')
        beban_transportasi = sum(tx.totalNominal or 0 for tx in beban_list if tx.kategoriBeban == 'beban_transportasi')
        
        # Beban penyusutan (depreciation)
        beban_penyusutan = sum((tx.penyusutanPerTahun or 0) // 12 for tx in beban_list if tx.penyusutanPerTahun)
        
        beban_operasional_lainnya = sum(
            tx.totalNominal or 0 for tx in beban_list 
            if tx.kategoriBeban not in ['beban_gaji', 'beban_sewa', 'beban_utilitas', 'beban_transportasi']
        )
        
        total_beban_operasional = (
            beban_gaji + beban_sewa + beban_utilitas + 
            beban_transportasi + beban_penyusutan + beban_operasional_lainnya
        )
        
        # PERHITUNGAN LABA
        total_pendapatan = pendapatan_penjualan
        laba_kotor = total_pendapatan - total_hpp
        laba_operasional = laba_kotor - total_beban_operasional
        laba_bersih = laba_operasional  # Simplified: no interest expense yet
        
        # MARGIN ANALYSIS
        margin_laba_kotor = (laba_kotor / total_pendapatan * 100) if total_pendapatan > 0 else 0.0
        margin_laba_bersih = (laba_bersih / total_pendapatan * 100) if total_pendapatan > 0 else 0.0
        
        jumlah_transaksi = len(pendapatan_penjualan_list) + len(pembelian_list) + len(beban_list)
        
        logger.info(f"📊 CALCULATION SUMMARY:")
        logger.info(f"   ├─ Total Pendapatan: Rp{total_pendapatan:,}")
        logger.info(f"   ├─ Total HPP: Rp{total_hpp:,}")
        logger.info(f"   ├─ Total Beban: Rp{total_beban_operasional:,}")
        logger.info(f"   ├─ Laba Bersih: Rp{laba_bersih:,}")
        logger.info(f"   └─ Jumlah Transaksi: {jumlah_transaksi}")
        
        # ============================================
        # MONTH-OVER-MONTH COMPARISON (Phase 2)
        # ============================================
        comparison_data = {}
        try:
            # Calculate last month period
            from datetime import datetime, timedelta
            # Extract periode from where clause or request
            periode_str = where.get('periodePelaporan')
            if not periode_str and where.get('timestamp'):
                # Derive periode from timestamp if not explicitly provided
                ts = where['timestamp'].get('gte', 0)
                periode_str = datetime.fromtimestamp(ts/1000).strftime('%Y-%m')
            
            if periode_str:
                current_period = datetime.strptime(periode_str, '%Y-%m')
                last_month = current_period - timedelta(days=current_period.day)
                last_month_periode = last_month.strftime('%Y-%m')
                
                # Query last month data using timestamp range
                from queries.financial_queries import parse_periode_pelaporan
                last_month_range = parse_periode_pelaporan(last_month_periode)
                last_month_where = {
                    'tenantId': where['tenantId'],
                    'status': 'approved',
                    'timestamp': {
                        'gte': last_month_range['start'],
                        'lte': last_month_range['end']
                    }
                }
                
                last_month_pendapatan_list = await rls_client.transaksiharian.find_many(
                    where={**last_month_where, 'jenisTransaksi': 'penjualan'}
                )
                last_month_pendapatan = sum(tx.totalNominal or 0 for tx in last_month_pendapatan_list)
                
                last_month_beban_list = await rls_client.transaksiharian.find_many(
                    where={**last_month_where, 'jenisTransaksi': 'beban'}
                )
                last_month_beban = sum(tx.totalNominal or 0 for tx in last_month_beban_list)
                
                last_month_laba_bersih = last_month_pendapatan - last_month_beban
                
                comparison_data = {
                    'laba_bersih': last_month_laba_bersih,
                    'total_pendapatan': last_month_pendapatan,
                    'total_beban': last_month_beban
                }
                
                logger.info(f"📊 Month-over-month: current={laba_bersih}, last={last_month_laba_bersih}")
        except Exception as e:
            logger.warning(f"Failed to calculate month-over-month: {e}")
            comparison_data = None
        
        return pb.LaporanLabaRugi(
            tenant_id=where['tenantId'],
            periode_pelaporan=where.get('periodePelaporan', ''),
            generated_at=int(datetime.utcnow().timestamp() * 1000),
            pendapatan_penjualan=pendapatan_penjualan,
            pendapatan_lainnya=0,
            total_pendapatan=total_pendapatan,
            hpp_bahan_baku=hpp_bahan_baku,
            hpp_tenaga_kerja=hpp_tenaga_kerja,
            hpp_overhead=hpp_overhead,
            total_hpp=total_hpp,
            beban_gaji=beban_gaji,
            beban_sewa=beban_sewa,
            beban_utilitas=beban_utilitas,
            beban_transportasi=beban_transportasi,
            beban_penyusutan=beban_penyusutan,
            beban_operasional_lainnya=beban_operasional_lainnya,
            total_beban_operasional=total_beban_operasional,
            laba_kotor=laba_kotor,
            laba_operasional=laba_operasional,
            beban_bunga=0,
            laba_bersih=laba_bersih,
            margin_laba_kotor=margin_laba_kotor,
            margin_laba_bersih=margin_laba_bersih,
            jumlah_transaksi=jumlah_transaksi
        )
    
    @staticmethod
    async def handle_get_laba_rugi(
        request,
        context: grpc.aio.ServicerContext,
        pb
    ):
        """Generate Laporan Laba Rugi (Income Statement)"""
        logger.info(f"📊 GetLabaRugi: tenant={request.tenant_id}, periode={request.periode_pelaporan}")
        
        rls_client = RLSPrismaClient(tenant_id=request.tenant_id, bypass_rls=True)
        
        try:
            await rls_client.connect()
            
            # Import prisma for global access
            from app.prisma_client import prisma
            
            where = build_where_clause(
                request.tenant_id,
                request.periode_pelaporan,
                request.start_date,
                request.end_date
            )
            
            result = await LabaRugiHandler.query_laba_rugi(rls_client, where, pb, prisma)
            logger.info(f"✅ Laba Rugi generated: laba_bersih={result.laba_bersih}")
            return result
            
        except Exception as e:
            logger.error(f"❌ GetLabaRugi failed: {str(e)}")
            await context.abort(grpc.StatusCode.INTERNAL, f"Failed to generate report: {str(e)}")
        finally:
            await rls_client.disconnect()