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


def format_financial_line(label: str, amount: str, width: int = 50) -> str:
    """
    Format financial report line with right-aligned amount
    
    Args:
        label: Line label (e.g., "Pendapatan Penjualan")
        amount: Formatted rupiah (e.g., "Rp100.000.000")
        width: Total width for alignment (default 50)
    
    Returns:
        Padded line: "Pendapatan Penjualan                      Rp100.000.000"
    """
    spaces_needed = width - len(label) - len(amount)
    if spaces_needed < 2:
        spaces_needed = 2  # Minimum 2 spaces
    return f"{label}{' ' * spaces_needed}{amount}"


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
        Also handles salary payment queries
        """
        logger.info(f"[{trace_id}] Handling financial_report intent")
        
        # Check if this is a salary payment query
        message_lower = request.message.lower()
        salary_keywords = ["sudah bayar gaji", "bayar gaji siapa", "gaji siapa saja", "belum bayar gaji", "yang belum dibayar"]
        if any(kw in message_lower for kw in salary_keywords):
            logger.info(f"[{trace_id}] Detected salary payment query, routing to salary handler")
            return await FinancialHandler.handle_salary_payment_query(
                request, ctx_response, intent_response,
                trace_id, service_calls, progress, client_manager
            )
        
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
                
                # Format response - reporting_service returns full Rupiah, NOT cents
                laba_bersih = report_response.laba_bersih
                total_pendapatan = report_response.total_pendapatan
                total_hpp = report_response.total_hpp
                laba_kotor = report_response.laba_kotor
                total_beban = report_response.total_beban_operasional
                margin = report_response.margin_laba_bersih
                
                # Simplified SAK EMKM format with proper alignment
                if laba_bersih >= 0:
                    milky_response = f"✅ LABA BERSIH {periode}: {format_rupiah(laba_bersih)}\n\n"
                else:
                    milky_response = f"⚠️ RUGI {periode}: {format_rupiah(abs(laba_bersih))}\n\n"
                
                milky_response += f"📊 Ringkasan:\n"
                milky_response += f"├─ {format_financial_line('Pendapatan Penjualan', format_rupiah(total_pendapatan))}\n"
                milky_response += f"├─ {format_financial_line('HPP (Pembelian)', format_rupiah(total_hpp))}\n"
                milky_response += f"├─ {format_financial_line('Laba Kotor', format_rupiah(laba_kotor))}\n"
                milky_response += f"├─ {format_financial_line('Beban Operasional', format_rupiah(total_beban))}\n"
                milky_response += f"└─ {format_financial_line('Laba Bersih', format_rupiah(laba_bersih))}\n\n"
                milky_response += f"📈 Margin: {margin:.1f}%\n"
                milky_response += f"📋 Dari {report_response.jumlah_transaksi} transaksi\n\n"
                milky_response += f"💡 Mau lihat laporan lengkap?"
                
            elif report_type == "neraca":
                report_response = await client_manager.stubs['reporting'].GetNeraca(
                    report_request
                )
                
                total_aset = report_response.total_aset
                total_liabilitas = report_response.total_liabilitas
                total_ekuitas = report_response.total_ekuitas
                
                milky_response = f"📊 Neraca per {periode}:\n\n"
                milky_response += f"Total Aset: {format_rupiah(total_aset)}\n"
                milky_response += f"Total Liabilitas: {format_rupiah(total_liabilitas)}\n"
                milky_response += f"Total Ekuitas: {format_rupiah(total_ekuitas)}\n\n"
                
                if report_response.is_balanced:
                    milky_response += "✅ Neraca balance!"
                else:
                    milky_response += "⚠️ Neraca tidak balance, perlu dicek"
            
            elif report_type == "arus_kas":
                report_response = await client_manager.stubs['reporting'].GetArusKas(
                    report_request
                )
                
                kas_akhir = report_response.kas_akhir_periode
                arus_operasi = report_response.arus_kas_operasi
                
                milky_response = f"💰 Arus Kas periode {periode}:\n\n"
                milky_response += f"Kas Akhir: {format_rupiah(kas_akhir)}\n"
                milky_response += f"Arus Kas Operasi: {format_rupiah(arus_operasi)}"
            
            elif report_type == "perubahan_ekuitas":
                report_response = await client_manager.stubs['reporting'].GetPerubahanEkuitas(
                    report_request
                )
                
                # Extract values - reporting_service returns full Rupiah, NOT cents
                ekuitas_awal = report_response.ekuitas_awal_periode
                modal_akhir = report_response.modal_akhir
                laba_bersih = report_response.laba_bersih_periode_berjalan
                prive = report_response.prive_periode_berjalan
                ekuitas_akhir = report_response.ekuitas_akhir_periode
                
                milky_response = f"📊 Perubahan Ekuitas periode {periode}:\n\n"
                milky_response += f"Ekuitas Awal: {format_rupiah(ekuitas_awal)}\n"
                milky_response += f"Modal Akhir: {format_rupiah(modal_akhir)}\n"
                milky_response += f"Laba Bersih: {format_rupiah(laba_bersih)}\n"
                milky_response += f"Prive: {format_rupiah(prive)}\n\n"
                milky_response += f"✅ Ekuitas Akhir: {format_rupiah(ekuitas_akhir)}"
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
    
    @staticmethod
    async def handle_salary_payment_query(
        request,
        ctx_response,
        intent_response,
        trace_id: str,
        service_calls: list,
        progress: int,
        client_manager
    ) -> str:
        """
        Handle salary payment history queries
        Examples:
        - "Saya sudah bayar gaji siapa saja ya?"
        - "Adakah yang belum saya bayar gajinya?"
        - "Gaji bulan November sudah bayar siapa saja?"
        """
        logger.info(f"[{trace_id}] Handling salary payment query")
        
        # Parse entities
        try:
            entities = json.loads(intent_response.entities_json)
            query_entities = entities.get("entities", {})
        except:
            query_entities = {}
        
        # Extract periode if specified
        periode = query_entities.get("periode_pelaporan") or query_entities.get("periode_gaji")
        if not periode:
            # Try to parse from message
            message_lower = request.message.lower()
            bulan_map = {
                "januari": "01", "februari": "02", "maret": "03", "april": "04",
                "mei": "05", "juni": "06", "juli": "07", "agustus": "08",
                "september": "09", "oktober": "10", "november": "11", "desember": "12"
            }
            for bulan, num in bulan_map.items():
                if bulan in message_lower:
                    from datetime import datetime
                    current_year = datetime.now().year
                    periode = f"{current_year}-{num}"
                    break
        
        # Calculate date range
        from datetime import datetime, timedelta
        if periode:
            year, month = periode.split("-")
            start_date = int(datetime(int(year), int(month), 1).timestamp() * 1000)
            if int(month) == 12:
                end_date = int(datetime(int(year) + 1, 1, 1).timestamp() * 1000) - 1
            else:
                end_date = int(datetime(int(year), int(month) + 1, 1).timestamp() * 1000) - 1
        else:
            # Default: current month
            now = datetime.now()
            start_date = int(datetime(now.year, now.month, 1).timestamp() * 1000)
            if now.month == 12:
                end_date = int(datetime(now.year + 1, 1, 1).timestamp() * 1000) - 1
            else:
                end_date = int(datetime(now.year, now.month + 1, 1).timestamp() * 1000) - 1
        
        # Query salary transactions
        try:
            list_request = transaction_service_pb2.ListTransactionsRequest(
                tenant_id=request.tenant_id,
                jenis_transaksi="beban",
                status="approved",
                start_timestamp=start_date,
                end_timestamp=end_date,
                page=1,
                page_size=200
            )
            
            list_response = await client_manager.stubs['transaction'].ListTransactions(
                list_request
            )
            
            # Filter for beban_gaji and extract detail_karyawan
            salary_transactions = []
            for tx in list_response.transactions:
                # Check if it's salary (kategori_beban = beban_gaji)
                if hasattr(tx, 'kategoriBeban') and tx.kategoriBeban == 'beban_gaji':
                    # Extract detail_karyawan from payload
                    import json as json_lib
                    payload = {}
                    if hasattr(tx, 'payload') and tx.payload:
                        try:
                            payload = json_lib.loads(tx.payload) if isinstance(tx.payload, str) else tx.payload
                        except:
                            pass
                    
                    detail_karyawan = payload.get('detail_karyawan', 'Tidak disebutkan')
                    total_nominal = tx.totalNominal if hasattr(tx, 'totalNominal') else 0
                    timestamp = tx.timestamp if hasattr(tx, 'timestamp') else 0
                    
                    salary_transactions.append({
                        'detail_karyawan': detail_karyawan,
                        'total_nominal': total_nominal,
                        'timestamp': timestamp,
                        'transaction_id': tx.id
                    })
            
            # Format response
            if not salary_transactions:
                periode_text = periode.replace("-", " ") if periode else "bulan ini"
                return f"📋 Belum ada pembayaran gaji yang tercatat untuk periode {periode_text}.\n\n💡 Mau catat pembayaran gaji sekarang?"
            
            # Group by employee
            from collections import defaultdict
            employee_payments = defaultdict(list)
            for tx in salary_transactions:
                employee_payments[tx['detail_karyawan']].append(tx)
            
            # Build response
            periode_text = periode.replace("-", " ") if periode else "bulan ini"
            response = f"📋 Daftar Pembayaran Gaji Periode {periode_text}:\n\n"
            
            total_all = 0
            for employee, payments in employee_payments.items():
                total_employee = sum(p['total_nominal'] for p in payments)
                total_all += total_employee
                response += f"✅ {employee}: {format_rupiah(total_employee)}\n"
                if len(payments) > 1:
                    response += f"   ({len(payments)}x pembayaran)\n"
            
            response += f"\n💰 Total: {format_rupiah(total_all)}\n"
            response += f"👥 Jumlah karyawan: {len(employee_payments)}\n\n"
            
            # Check if query is about unpaid employees
            message_lower = request.message.lower()
            if any(k in message_lower for k in ["belum", "belum bayar", "yang belum"]):
                response += "💡 Untuk mengecek karyawan yang belum dibayar, perlu data master karyawan. Fitur ini akan segera tersedia!"
            
            return response
            
        except Exception as e:
            logger.error(f"[{trace_id}] Salary query failed: {e}")
            return f"Maaf, ada kendala ambil data pembayaran gaji. Error: {str(e)[:100]}"