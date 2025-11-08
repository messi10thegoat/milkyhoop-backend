"""
Financial Handler for Tenant Orchestrator
Handles financial analytics queries and report routing

Adapted from setup_orchestrator for tenant mode:
- Stateless operation (no session management)
- Returns string instead of ProcessSetupChatResponse
- Direct business data access
"""

import logging
import json
from datetime import datetime
import transaction_service_pb2
import reporting_service_pb2

from formatters.financial_formatter import (
    format_rupiah,
    format_top_products_response,
    format_low_sell_products_response
)

logger = logging.getLogger(__name__)


class FinancialHandler:
    """Handler for financial analytics and reporting in tenant mode"""
    
    @staticmethod
    def parse_time_range(user_message: str, entities: dict) -> str:
        """
        Parse time range from user message
        Returns: 'daily', 'weekly', 'monthly'
        """
        message_lower = user_message.lower()
        
        # Check entities first
        time_range = entities.get("time_range")
        if time_range:
            return time_range
        
        # Parse from message
        if any(word in message_lower for word in ['hari ini', 'today', 'harian']):
            return 'daily'
        elif any(word in message_lower for word in ['minggu ini', 'weekly', 'mingguan', 'seminggu']):
            return 'weekly'
        elif any(word in message_lower for word in ['bulan ini', 'monthly', 'bulanan', 'sebulan']):
            return 'monthly'
        else:
            # Default to monthly
            return 'monthly'
    
    @staticmethod
    async def handle_top_products_query(
        request,
        ctx_response,
        intent_response,
        trace_id: str,
        service_calls: list,
        progress: int,
        client_manager
    ) -> str:
        """Handle top products analytics query - returns string response"""
        logger.info(f"[{trace_id}] Handling top_products query")
        
        # Parse entities
        try:
            entities = json.loads(intent_response.entities_json)
            query_entities = entities.get("entities", {})
        except:
            query_entities = {}
        
        # Determine time range
        time_range = FinancialHandler.parse_time_range(request.message, query_entities)
        
        logger.info(f"[{trace_id}] Time range: {time_range}")
        
        # Call transaction_service.GetTopProducts
        try:
            analytics_start = datetime.now()
            
            top_products_request = transaction_service_pb2.GetTopProductsRequest(
                tenant_id=request.tenant_id,
                time_range=time_range,
                limit=10
            )
            
            top_products_response = await client_manager.stubs['transaction'].GetTopProducts(
                top_products_request
            )
            
            analytics_duration = (datetime.now() - analytics_start).total_seconds() * 1000
            service_calls.append({
                "service_name": "transaction",
                "method": "GetTopProducts",
                "duration_ms": int(analytics_duration),
                "status": "success"
            })
            
            # Format response using formatter
            milky_response = format_top_products_response(
                top_products_response.products,
                time_range
            )
            
            logger.info(f"[{trace_id}] Top products returned: {len(top_products_response.products)} items")
            
            return milky_response
            
        except Exception as e:
            logger.error(f"[{trace_id}] Top products query failed: {e}")
            return f"Maaf, ada kendala ambil data produk terlaris. Error: {str(e)[:100]}"
    
    @staticmethod
    async def handle_low_sell_products_query(
        request,
        ctx_response,
        intent_response,
        trace_id: str,
        service_calls: list,
        progress: int,
        client_manager
    ) -> str:
        """Handle low-sell products analytics query - returns string response"""
        logger.info(f"[{trace_id}] Handling low_sell_products query")
        
        # Parse entities
        try:
            entities = json.loads(intent_response.entities_json)
            query_entities = entities.get("entities", {})
        except:
            query_entities = {}
        
        # Determine time range
        time_range = FinancialHandler.parse_time_range(request.message, query_entities)
        
        logger.info(f"[{trace_id}] Time range: {time_range}")
        
        # Call transaction_service.GetLowSellProducts
        try:
            analytics_start = datetime.now()
            
            low_sell_request = transaction_service_pb2.GetLowSellProductsRequest(
                tenant_id=request.tenant_id,
                time_range=time_range,
                turnover_threshold=10.0,  # 10% threshold
                limit=10
            )
            
            low_sell_response = await client_manager.stubs['transaction'].GetLowSellProducts(
                low_sell_request
            )
            
            analytics_duration = (datetime.now() - analytics_start).total_seconds() * 1000
            service_calls.append({
                "service_name": "transaction",
                "method": "GetLowSellProducts",
                "duration_ms": int(analytics_duration),
                "status": "success"
            })
            
            # Format response using formatter
            milky_response = format_low_sell_products_response(
                low_sell_response.products,
                time_range
            )
            
            logger.info(f"[{trace_id}] Low-sell products returned: {len(low_sell_response.products)} items")
            
            return milky_response
            
        except Exception as e:
            logger.error(f"[{trace_id}] Low-sell products query failed: {e}")
            return f"Maaf, ada kendala ambil data produk kurang laku. Error: {str(e)[:100]}"
    
    @staticmethod
    async def handle_financial_report(
        request,
        ctx_response,
        intent_response,
        trace_id: str,
        service_calls: list,
        progress: int,
        client_manager
    ) -> str:
        """
        Handle financial report requests - returns string response
        Route to reporting_service for SAK EMKM compliant reports
        """
        logger.info(f"[{trace_id}] Handling financial_report intent")
        
        # Parse entities
        try:
            entities = json.loads(intent_response.entities_json)
            report_entities = entities.get("entities", {})
        except:
            report_entities = {}
        
        logger.info(f"[{trace_id}] Report entities: {report_entities}")
        
        report_type = report_entities.get("report_type", "laba_rugi")
        periode = report_entities.get("periode_pelaporan", datetime.now().strftime("%Y-%m"))
        
        # Call reporting_service
        report_start = datetime.now()
        
        try:
            report_request = reporting_service_pb2.ReportRequest(
                tenant_id=request.tenant_id,
                periode_pelaporan=periode,
                created_by=request.user_id
            )
            
            # Route to appropriate report method
            if report_type == "laba_rugi":
                report_response = await client_manager.stubs['reporting'].GetLabaRugi(
                    report_request
                )
                
                # Format response
                laba_bersih = report_response.laba_bersih // 100
                total_pendapatan = report_response.total_pendapatan // 100
                total_beban = report_response.total_beban_operasional // 100
                
                if laba_bersih >= 0:
                    milky_response = f"✅ Laba bersih periode {periode}: {format_rupiah(laba_bersih * 100)}\n\n"
                else:
                    milky_response = f"⚠️ Rugi periode {periode}: Rp {abs(laba_bersih):,}\n\n"
                
                milky_response += f"Pendapatan: {format_rupiah(total_pendapatan * 100)}\n"
                milky_response += f"Beban: {format_rupiah(total_beban * 100)}\n"
                milky_response += f"Dari {report_response.jumlah_transaksi} transaksi"
                
            elif report_type == "neraca":
                report_response = await client_manager.stubs['reporting'].GetNeraca(
                    report_request
                )
                
                total_aset = report_response.total_aset // 100
                total_liabilitas = report_response.total_liabilitas // 100
                total_ekuitas = report_response.total_ekuitas // 100
                
                milky_response = f"📊 Neraca per {periode}:\n\n"
                milky_response += f"Total Aset: {format_rupiah(total_aset * 100)}\n"
                milky_response += f"Total Liabilitas: {format_rupiah(total_liabilitas * 100)}\n"
                milky_response += f"Total Ekuitas: {format_rupiah(total_ekuitas * 100)}\n\n"
                
                if report_response.is_balanced:
                    milky_response += "✅ Neraca balance!"
                else:
                    milky_response += "⚠️ Neraca tidak balance, perlu dicek"
            
            elif report_type == "arus_kas":
                report_response = await client_manager.stubs['reporting'].GetArusKas(
                    report_request
                )
                
                kas_akhir = report_response.kas_akhir_periode // 100
                arus_operasi = report_response.arus_kas_operasi // 100
                
                milky_response = f"💰 Arus Kas periode {periode}:\n\n"
                milky_response += f"Kas Akhir: {format_rupiah(kas_akhir * 100)}\n"
                milky_response += f"Arus Kas Operasi: {format_rupiah(arus_operasi * 100)}"
            
            elif report_type == "perubahan_ekuitas":
                report_response = await client_manager.stubs['reporting'].GetPerubahanEkuitas(
                    report_request
                )
                
                # Extract values (in cents, convert to rupiah)
                ekuitas_awal = report_response.ekuitas_awal_periode // 100
                modal_akhir = report_response.modal_akhir // 100
                laba_bersih = report_response.laba_bersih_periode_berjalan // 100
                prive = report_response.prive_periode_berjalan // 100
                ekuitas_akhir = report_response.ekuitas_akhir_periode // 100
                
                milky_response = f"📊 Perubahan Ekuitas periode {periode}:\n\n"
                milky_response += f"Ekuitas Awal: {format_rupiah(ekuitas_awal * 100)}\n"
                milky_response += f"Modal Akhir: {format_rupiah(modal_akhir * 100)}\n"
                milky_response += f"Laba Bersih: {format_rupiah(laba_bersih * 100)}\n"
                milky_response += f"Prive: {format_rupiah(prive * 100)}\n\n"
                milky_response += f"✅ Ekuitas Akhir: {format_rupiah(ekuitas_akhir * 100)}"
            else:
                milky_response = f"Report {report_type} belum didukung. Coba laba_rugi, neraca, atau arus_kas?"
            
            report_duration = (datetime.now() - report_start).total_seconds() * 1000
            service_calls.append({
                "service_name": "reporting",
                "method": f"Get{report_type.title().replace('_', '')}",
                "duration_ms": int(report_duration),
                "status": "success"
            })
            
            return milky_response
            
        except Exception as e:
            logger.error(f"[{trace_id}] Reporting service error: {e}")
            return f"Maaf, lagi ada kendala ambil laporan. Error: {str(e)[:100]}"