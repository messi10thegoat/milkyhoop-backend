"""
business_parser/app/grpc_server.py

Business Parser - Intent Classification for Tenant Mode Queries
Purpose: Classify financial analytics, inventory, and accounting queries
Architecture: Called by tenant_orchestrator → routes to financial services

Author: MilkyHoop Team
Version: 1.0.0
"""

import asyncio
import signal
import logging
import json
import re
from typing import Dict, Any
from datetime import datetime
import hashlib

import grpc
import redis.asyncio as redis
from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from google.protobuf import empty_pb2, timestamp_pb2

# Import generated proto stubs
import business_parser_pb2 as pb
import business_parser_pb2_grpc as pb_grpc

# Import config
from config import settings

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Try to import LLM parser
try:
    from services.llm_parser import parse_tenant_intent_entities
    LLM_AVAILABLE = True
    logger.info("LLM parser loaded successfully")
except ImportError as e:
    logger.warning(f"LLM parser not available, using fallback: {e}")
    LLM_AVAILABLE = False


class BusinessParserService(pb_grpc.BusinessParserServicer):
    """
    Business Parser Service Implementation
    
    Classifies tenant mode queries into financial/analytics intents
    """
    
    def __init__(self):
        # Tenant mode intents (read-only analytics)
        self.tenant_intents = [
            "transaction_record",    # Record financial transactions
            "financial_report",      # SAK EMKM reports
            "top_products",          # Best sellers
            "low_sell_products",     # Slow moving
            "inventory_query",       # Stock level/movement
            "accounting_query",      # Journal/CoA
            "general_inquiry",       # Business questions
            "out_of_scope"           # Outside domain
        ]
        
        # Initialize Redis client for caching
        self.redis_client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD,
            db=settings.REDIS_DB,
            decode_responses=True
        )

        logger.info(f"Business Parser initialized | LLM: {LLM_AVAILABLE}")
        logger.info(f"Tenant intents: {self.tenant_intents}")
    
    async def ClassifyIntent(
        self, 
        request: pb.ClassifyIntentRequest, 
        context
    ) -> pb.ClassifyIntentResponse:
        """
        Classify user query into tenant mode intent
        
        Flow:
        1. Try LLM classification (GPT-4o)
        2. Fall back to rule-based if LLM fails
        3. Return intent + entities + confidence
        """
        
        try:
            logger.info(f"ClassifyIntent called | tenant={request.tenant_id} | message='{request.message[:100]}...'")
            



            # Generate cache key (hash of tenant + message)
            cache_key = f"bp:intent:{hashlib.md5(f'{request.tenant_id}:{request.message}'.encode()).hexdigest()}"
            
            # Try cache first
            try:
                cached = await self.redis_client.get(cache_key)
                if cached:
                    logger.info(f"✅ Cache HIT | key={cache_key[:24]}...")
                    cached_data = json.loads(cached)
                    return pb.ClassifyIntentResponse(
                        intent=cached_data['intent'],
                        entities_json=cached_data['entities_json'],
                        confidence=cached_data['confidence'],
                        reasoning="cached result",
                        model_used=cached_data['model_used'],
                        processing_time_ms=0,
                        timestamp=timestamp_pb2.Timestamp(seconds=int(datetime.now().timestamp()))
                    )
            except Exception as e:
                logger.warning(f"Cache read failed: {e}")
            
            logger.info(f"❌ Cache MISS | key={cache_key[:24]}... | Calling LLM")
            # PRIMARY: LLM-based classification
            if LLM_AVAILABLE:
                try:
                    logger.info("Using LLM classification (GPT-4o)")
                    
                    # Extract context if provided
                    context_str = None
                    if request.context and request.context.strip():
                        logger.info(f"Context provided | length={len(request.context)}")
                        context_str = request.context
                    
                    # Call LLM parser
                    parsed = parse_tenant_intent_entities(
                        text=request.message,
                        context=context_str,
                        tenant_id=request.tenant_id
                    )
                    
                    intent = parsed.get("intent", "general_inquiry")
                    entities = parsed.get("entities", {})
                    confidence = parsed.get("confidence", 0.0)
                    reasoning = parsed.get("reasoning", "")
                    model_used = parsed.get("model_used", "gpt-4o")
                    
                    logger.info(
                        f"LLM classification | intent={intent} | "
                        f"confidence={confidence:.2f} | model={model_used}"
                    )
                    


                    # Store in cache (TTL from config)
                    try:
                        cache_data = {
                            'intent': intent,
                            'entities_json': json.dumps(entities, ensure_ascii=False),
                            'confidence': confidence,
                            'model_used': model_used
                        }
                        await self.redis_client.setex(
                            cache_key,
                            settings.REDIS_CACHE_TTL,
                            json.dumps(cache_data)
                        )
                        logger.info(f"✅ Cached result | TTL={settings.REDIS_CACHE_TTL}s")
                    except Exception as e:
                        logger.warning(f"Cache write failed: {e}")
                    return pb.ClassifyIntentResponse(
                        intent=intent,
                        entities_json=json.dumps(entities, ensure_ascii=False),
                        confidence=confidence,
                        reasoning=reasoning,
                        model_used=model_used,
                        processing_time_ms=0,  # TODO: track timing
                        timestamp=timestamp_pb2.Timestamp(seconds=int(datetime.now().timestamp()))
                    )
                    
                except Exception as e:
                    logger.error(f"LLM classification failed: {e}")
                    logger.info("Falling back to rule-based classification")
                    # Fall through to rule-based fallback
            
            # FALLBACK: Rule-based classification
            logger.info("Using rule-based fallback classification")
            from services.llm_parser import _rule_fallback
            parsed = _rule_fallback(request.message)
            intent = parsed.get("intent", "general_inquiry")
            entities = parsed.get("entities", {})
            confidence = parsed.get("confidence", 0.60)
            
            logger.info(f"Rule-based classification | intent={intent} | confidence={confidence:.2f}")
            
            return pb.ClassifyIntentResponse(
                intent=intent,
                entities_json=json.dumps(entities, ensure_ascii=False),
                confidence=confidence,
                reasoning="rule-based fallback",
                model_used="regex",
                processing_time_ms=0,
                timestamp=timestamp_pb2.Timestamp(seconds=int(datetime.now().timestamp()))
            )
            
        except Exception as e:
            logger.error(f"ClassifyIntent error: {e}")
            import traceback
            traceback.print_exc()
            
            # Return error as general_inquiry
            return pb.ClassifyIntentResponse(
                intent="general_inquiry",
                entities_json="{}",
                confidence=0.3,
                reasoning=f"error: {str(e)}",
                model_used="error_fallback",
                processing_time_ms=0,
                timestamp=timestamp_pb2.Timestamp(seconds=int(datetime.now().timestamp()))
            )
    
        def _rule_based_classify(self, message: str) -> tuple:
            """
            Rule-based intent classification (fallback) - OPTIMIZED VERSION
        Enhanced with rich Indonesian vocabulary (formal, colloquial, UMKM slang)
        
        Returns:
            (intent, confidence, entities)
        """
        text_lower = message.lower()
        
        # ========================================================================
        # 1. FINANCIAL REPORT TRIGGERS
        # ========================================================================
        # Vocabulary: Formal accounting terms + everyday business slang + regional variations
        # INCLUDES: Salary payment queries (e.g. "sudah bayar gaji siapa saja")
        financial_keywords = [
            # Formal/Standard terms
            "untung", "rugi", "laba", "neraca", "kas", "aset", "laporan", 
            "keuangan", "finansial", "omzet", "profit", "pendapatan",
            
            # Colloquial/Everyday terms
            "duit masuk", "duit keluar", "modal", "keuntungan", "kerugian",
            "lap keu", "laporan keuangan", "laporan finansial", "lap finansial",
            
            # UMKM slang & abbreviations
            "cuan", "rugi cuan", "untung rugi", "laba rugi", "laba/rugi",
            "cashflow", "arus kas", "cash flow", "aliran kas",
            "penjualan bersih", "pendapatan kotor", "pendapatan bersih",
            "biaya operasional", "biaya oprasional", "biaya ops",
            
            # Report-specific terms
            "laporan laba", "laporan neraca", "balance sheet", "income statement",
            "profit loss", "p&l", "p/l", "pl statement",
            
            # Regional variations & typos (common in chat)
            "finansialnya", "keuangannya", "pendap", "penjualannya",
            "lap keuangan", "laporan keu", "data keuangan",
            
            # Salary payment queries (should be classified as financial_report)
            "sudah bayar gaji", "bayar gaji siapa", "gaji siapa saja", "belum bayar gaji",
            "yang belum dibayar", "gaji bulan", "total pengeluaran gaji", "pengeluaran gaji",
            "gaji sudah dibayar", "daftar gaji", "riwayat gaji"
        ]
        
        if any(kw in text_lower for kw in financial_keywords):
            return ("financial_report", 0.85, {
                "report_type": "laba_rugi",
                "periode_pelaporan": datetime.now().strftime("%Y-%m")
            })
        
        # ========================================================================
        # 2. TOP PRODUCTS TRIGGERS
        # ========================================================================
        # Vocabulary: Bestseller terminology in Indonesian retail/UMKM context
        top_keywords = [
            # Standard terms
            "terlaris", "paling laku", "best seller", "best-seller", "bestseller",
            "top", "ranking", "peringkat", "urutan",
            
            # Colloquial expressions
            "laku keras", "laku banget", "paling dicari", "favorit", 
            "favorit pelanggan", "favorit customer", "paling banyak dibeli",
            "paling banyak terjual", "hot seller", "hot item", "hot product",
            
            # UMKM slang
            "barang laris", "produk laris", "dagangan laris", "jualan laris",
            "cepet laku", "cepat laku", "laku cepat", "laris manis",
            "paling diminati", "paling laku", "top seller", "top selling",
            
            # Query variations
            "produk apa yang laris", "barang apa yang laku", "mana yang laris",
            "yang paling banyak", "best item", "top item", "produk unggulan"
        ]
        
        if any(kw in text_lower for kw in top_keywords):
            return ("top_products", 0.90, {
                "time_range": "monthly",
                "limit": 10
            })
        
        # ========================================================================
        # 3. LOW-SELL PRODUCTS TRIGGERS
        # ========================================================================
        # Vocabulary: Slow-moving inventory terminology
        low_keywords = [
            # Standard terms
            "kurang laku", "slow moving", "slow-moving", "jarang laku", 
            "menumpuk", "stok lama", "dead stock", "deadstock",
            
            # Colloquial expressions
            "ngendon", "numpuk", "stagnan", "mandeg", "gak laku",
            "ga laku", "ngga laku", "nggak laku", "susah laku",
            "males laku", "lama laku", "jarang dibeli", "jarang terjual",
            
            # UMKM specific terms
            "barang numpuk", "produk numpuk", "stok menggunung", 
            "stok menumpuk", "stok mati", "barang mati", "produk mati",
            "kadaluwarsa", "hampir kadaluwarsa", "expired", "almost expired",
            
            # Descriptive phrases
            "tidak laku", "sulit laku", "jarang diminati", "kurang diminati",
            "sepi pembeli", "sepi peminat", "stok berdebu", "dormant stock",
            "barang lambat", "produk lambat", "slow item", "slow product"
        ]
        
        if any(kw in text_lower for kw in low_keywords):
            return ("low_sell_products", 0.88, {
                "time_range": "30_hari",
                "limit": 10
            })

        # ========================================================================
        # 4. TRANSACTION TRIGGERS (ENHANCED)
        # ========================================================================
        # Vocabulary: All transaction types with Indonesian business terminology
        transaction_keywords = [
            # Purchase terms
            "beli", "belanja", "pembelian", "beliannya", "pembelian:", 
            "kulakan", "kulak", "restok", "restock", "re-stock",
            "beli barang", "beli produk", "purchase", "procurement",
            
            # Sales terms
            "jual", "jualan", "penjualan", "penjualannya", "jual barang",
            "transaksi jual", "sales", "selling", "sold", "terjual",
            
            # Payment terms
            "bayar", "bayaran", "pembayaran", "setor", "setoran",
            "tarik", "ambil", "penarikan", "pengambilan", "payment",
            
            # Expense/Cost terms
            "beban", "biaya", "pengeluaran", "expense", "cost",
            "ongkos", "ongkir", "biaya kirim", "biaya operasional",
            
            # General transaction terms
            "transaksi", "trx", "trans", "nota", "nota jual", "nota beli",
            "faktur", "invoice", "bon", "struk", "receipt", "kwitansi",
            
            # Colloquial variations
            "masuk duit", "keluar duit", "kas masuk", "kas keluar",
            "uang masuk", "uang keluar", "cash in", "cash out"
        ]
        
        if any(kw in text_lower for kw in transaction_keywords):
            # Determine jenis_transaksi with expanded keyword matching
            if any(k in text_lower for k in ["beli", "pembelian", "belanja", "kulakan", 
                                            "kulak", "restok", "restock", "purchase"]):
                jenis_transaksi = "pembelian"
            elif any(k in text_lower for k in ["jual", "penjualan", "jualan", "sales", 
                                                "selling", "terjual", "sold"]):
                jenis_transaksi = "penjualan"
            else:
                jenis_transaksi = "beban"
            
            entities = {"jenis_transaksi": jenis_transaksi, "items": []}
            
            # ====================================================================
            # ENHANCED PRICE EXTRACTION
            # ====================================================================
            # Support formats: Rp5000, 5.000, 5000, 5rb, 5ribu, 5k, 5juta, 5jt, 
            #                  @Rp5000, IDR 5k, Rp 5.000.000, dll
            price_patterns = [
                # Format: Rp/IDR prefix dengan angka dan optional unit
                r'(?:rp|@rp|idr)[\s]*([0-9]{1,3}(?:[.,][0-9]{3})*(?:[.,][0-9]+)?)\s*(?:rb|ribu|juta|jt|k|m)?',
                
                # Format: harga/@ dengan angka
                r'(?:harga|@)[\s]*(?:rp)?[\s]*([0-9]{1,3}(?:[.,][0-9]{3})*(?:[.,][0-9]+)?)',
                
                # Format: angka standalone dengan unit (5rb, 10juta, 3k)
                r'([0-9]{1,3}(?:[.,][0-9]{3})*(?:[.,][0-9]+)?)\s*(?:rb|ribu|juta|jt|k|m)\b',
                
                # Format: nominal natural (5000, 10000)
                r'\b([0-9]{4,})\b'
            ]
            
            for pattern in price_patterns:
                match = re.search(pattern, text_lower)
                if match:
                    price_str = match.group(1).replace('.', '').replace(',', '')
                    try:
                        base_price = float(price_str)
                        
                        # Handle multipliers with comprehensive detection
                        multiplier_check = text_lower[max(0, match.start()-10):match.end()+10]
                        
                        if any(x in multiplier_check for x in ['juta', 'jt', 'm', 'million']):
                            base_price *= 1000000
                        elif any(x in multiplier_check for x in ['rb', 'ribu', 'k', 'thousand']):
                            base_price *= 1000
                        
                        entities["total_nominal"] = int(base_price)
                        break
                    except:
                        pass
            
            # ====================================================================
            # ENHANCED QUANTITY EXTRACTION
            # ====================================================================
            # Support: pcs, unit, pack, bungkus, lusin, koli, box, dus, karton, dll
            qty_patterns = [
                r'(\d+)[\s]*(pcs|pc|piece)',
                r'(\d+)[\s]*(unit|buah|biji|item)',
                r'(\d+)[\s]*(pack|pak|bungkus|kemasan)',
                r'(\d+)[\s]*(lusin|dozen|dz)',
                r'(\d+)[\s]*(koli|karton|box|dus|kardus)',
                r'(\d+)[\s]*(set|pasang|pair)',
                r'(\d+)[\s]*(kg|kilo|kilogram|gr|gram)',
                r'(\d+)[\s]*(liter|ml|cc)'
            ]
            
            for pattern in qty_patterns:
                qty_match = re.search(pattern, text_lower)
                if qty_match:
                    entities["items"] = [{
                        "jumlah": int(qty_match.group(1)),
                        "satuan": qty_match.group(2)
                    }]
                    break
            
            return ("transaction_record", 0.70, entities)
        
        # ========================================================================
        # 5. INVENTORY QUERY TRIGGERS (ENHANCED)
        # ========================================================================
        # Vocabulary: Stock checking terminology
        inventory_keywords = [
            # Standard terms
            "stok", "stock", "persediaan", "inventory",
            
            # Query phrases
            "cek stok", "check stock", "cekstok", "cek persediaan",
            "berapa stok", "berapa stock", "ada stok", "ada stock",
            "sisa stok", "sisa stock", "stok tersisa", "stock tersisa",
            
            # Colloquial
            "barang ada", "produk ada", "masih ada", "masih tersedia",
            "ada ga", "ada gak", "ada ngga", "ada nggak", "ready stock",
            "ready stok", "available", "tersedia", "ketersediaan",
            
            # UMKM terms
            "stok gudang", "barang gudang", "stok toko", "barang toko",
            "stok digudang", "stok di gudang", "di gudang ada"
        ]
        
        if any(kw in text_lower for kw in inventory_keywords):
            # Enhanced product name extraction
            product_name = ""
            
            # Comprehensive extraction patterns
            patterns = [
                r'(?:stok|stock)\s+([a-zA-Z0-9\s\-_./]+?)(?:\?|$|\.|\bberapa|\bada)',
                r'(?:cek|check)\s+(?:stok|stock)\s+([a-zA-Z0-9\s\-_./]+?)(?:\?|$|\.)',
                r'berapa\s+(?:stok|stock)\s+([a-zA-Z0-9\s\-_./]+?)(?:\?|$|\.)',
                r'(?:ada|sisa|tersedia)\s+(?:stok|stock)\s+([a-zA-Z0-9\s\-_./]+?)(?:\?|$|\.)',
                r'(?:persediaan|inventory)\s+([a-zA-Z0-9\s\-_./]+?)(?:\?|$|\.)',
                r'(?:barang|produk)\s+([a-zA-Z0-9\s\-_./]+?)\s+(?:ada|tersedia|masih)'
            ]
            
            for pattern in patterns:
                match = re.search(pattern, text_lower)
                if match:
                    product_name = match.group(1).strip()
                    # Clean up common words at end
                    product_name = re.sub(r'\s+(ga|gak|ngga|nggak|dong|nih|yah?)$', '', product_name)
                    break
            
            return ("inventory_query", 0.80, {
                "query_type": "stock_level",
                "product_name": product_name
            })
        
        # ========================================================================
        # 6. ACCOUNTING QUERY TRIGGERS (ENHANCED)
        # ========================================================================
        # Vocabulary: Accounting & bookkeeping terminology
        accounting_keywords = [
            # Standard accounting terms
            "jurnal", "journal", "journal entry", "jurnal entri",
            "bagan akun", "chart of account", "chart of accounts", "coa",
            "debit", "kredit", "credit", "d/k", "dk",
            
            # Colloquial accounting
            "akun", "rekening", "account", "posting", "posting jurnal",
            "buku besar", "general ledger", "ledger", "gl",
            
            # UMKM bookkeeping terms
            "catat transaksi", "pencatatan", "pembukuan", "bookkeeping",
            "laporan akuntansi", "laporan pembukuan", "buku kas",
            "kas buku", "buku harian", "daily book"
        ]
        
        if any(kw in text_lower for kw in accounting_keywords):
            return ("accounting_query", 0.75, {
                "query_type": "journal_entries"
            })
        
        # ========================================================================
        # 7. DEFAULT: GENERAL INQUIRY
        # ========================================================================
        return ("general_inquiry", 0.6, {})
    
    async def HealthCheck(self, request: empty_pb2.Empty, context) -> empty_pb2.Empty:
        """Health check endpoint"""
        return empty_pb2.Empty()


async def serve() -> None:
    """Start gRPC server"""
    
    logger.info("Starting Business Parser gRPC server...")
    
    # Create server
    server = aio.server()
    
    # Add servicer
    servicer = BusinessParserService()
    pb_grpc.add_BusinessParserServicer_to_server(servicer, server)
    
    # Add health check
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    health_servicer.set("business_parser.BusinessParser", health_pb2.HealthCheckResponse.SERVING)
    
    # Listen on port
    listen_addr = f"0.0.0.0:{settings.GRPC_PORT}"
    server.add_insecure_port(listen_addr)
    
    logger.info(f"🚀 Business Parser gRPC server listening on port {settings.GRPC_PORT}")
    logger.info(f"📊 Tenant intents: financial_report, top_products, inventory_query, accounting_query")
    
    # Start server
    await server.start()
    
    # Graceful shutdown handler
    stop_event = asyncio.Event()
    
    def handle_shutdown(*_):
        logger.info("🛑 Shutdown signal received. Cleaning up...")
        stop_event.set()
    
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)
    
    # Wait for termination
    try:
        await stop_event.wait()
    finally:
        logger.info("Stopping server...")
        await server.stop(grace=5)
        logger.info("✅ Shutdown complete")


if __name__ == "__main__":
    asyncio.run(serve())