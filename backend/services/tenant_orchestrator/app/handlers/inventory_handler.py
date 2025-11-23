"""
Inventory Handler for Tenant Orchestrator
Adapted from setup_orchestrator for tenant mode operations

Handles:
- inventory_query: Check stock levels (READ-ONLY analytics)
- inventory_update: Manual stock adjustments
- generate_stock_alert: Low stock warnings

Key differences from setup mode:
- Returns string response instead of ProcessSetupChatResponse
- No session/progress tracking (stateless)
- Simplified error handling
"""

import logging
import json
import grpc
import hashlib
import uuid
from datetime import datetime

# Proto imports
import inventory_service_pb2

# Setup logging
logger = logging.getLogger(__name__)


class InventoryHandler:
    """
    Static class for inventory-related operations in tenant mode
    All methods are async and work with GrpcClientManager
    """
    
    @staticmethod
    async def handle_inventory_query(
        request,
        ctx_response,
        intent_response,
        trace_id: str,
        service_calls: list,
        progress: int,
        client_manager
    ) -> str:
        """
        Handle inventory stock query
        
        CRITICAL FIX: Proper entity extraction without double nesting
        Pattern from setup_orchestrator that works:
        entities.get("entities", {}) then extract product_name
        """
        logger.info(f"[{trace_id}] Handling inventory_query intent")
        
        # Parse entities from intent_response (direct, no nesting)
        try:
            inventory_entities = json.loads(intent_response.entities_json)
        except Exception as e:
            logger.error(f"[{trace_id}] Failed to parse entities: {e}")
            inventory_entities = {}
        
        logger.info(f"[{trace_id}] Inventory entities: {inventory_entities}")
        
        # Extract product name - Multiple fallback patterns
        # Support complex product names like "kain cotton combet 24s warna hitam"
        product_name = (
            inventory_entities.get("product_name") or 
            inventory_entities.get("produk_id") or
            inventory_entities.get("nama_produk") or
            inventory_entities.get("product")
        )
        
        # If still not found, try to extract from message directly
        # This handles cases where LLM doesn't extract complex product names
        if not product_name:
            # Try to extract product name from message
            message_lower = request.message.lower()
            # Look for patterns like "stok [product]" or "[product] stoknya"
            import re
            patterns = [
                r'stok\s+(.+?)(?:\s+masih|$|berapa|ada)',
                r'(.+?)\s+stoknya',
                r'(.+?)\s+stok\s+masih',
            ]
            for pattern in patterns:
                match = re.search(pattern, message_lower)
                if match:
                    product_name = match.group(1).strip()
                    # Remove common question words
                    product_name = re.sub(r'\b(berapa|masih|ada|yang|apa|saja)\b', '', product_name).strip()
                    if product_name:
                        break
        
        if not product_name:
            return "Mohon maaf, produk apa yang mau dicek stoknya? Bisa tolong sebutkan nama produknya?"
        
        logger.info(f"[{trace_id}] Product name extracted: {product_name}")
        
        # FIX: Generate produk_id UUID (same as transaction_handler)
        name_hash = hashlib.md5(f"{request.tenant_id}:{product_name}".encode()).hexdigest()
        produk_id = str(uuid.UUID(name_hash))
        
        logger.info(f"[{trace_id}] Resolved produk_id: {product_name} → {produk_id}")
        
        # Call inventory_service.GetStockLevel
        try:
            stock_request = inventory_service_pb2.GetStockLevelRequest(
                tenant_id=request.tenant_id,
                produk_id=produk_id,
                lokasi_gudang="gudang-utama"
            )
            
            stock_response = await client_manager.stubs['inventory'].GetStockLevel(
                stock_request
            )
            
            # Format response
            stok = int(stock_response.current_stock)
            satuan = stock_response.satuan or "pcs"
            
            milky_response = f"📦 Stok {product_name}:\n\n"
            milky_response += f"   Tersedia: {stok} {satuan}\n"
            milky_response += f"   Lokasi: {stock_response.lokasi_gudang or 'Semua gudang'}\n"
            
            if stock_response.is_low_stock:
                milky_response += f"\n⚠️ Stok menipis! Minimum: {int(stock_response.minimum_stock)} {satuan}"
            
            return milky_response
            
        except grpc.RpcError as e:
            logger.error(f"[{trace_id}] Inventory service error: {e}")
            if e.code() == grpc.StatusCode.NOT_FOUND:
                return f"Produk {product_name} belum terdaftar di inventory"
            else:
                return f"Maaf, gagal cek stok. Error: {e.details()[:100]}"
        except Exception as e:
            logger.error(f"[{trace_id}] Unexpected error: {e}")
            return f"Ada kendala cek stok. Error: {str(e)[:100]}"
    
    @staticmethod
    async def handle_inventory_update(
        request,
        ctx_response,
        intent_response,
        trace_id: str,
        service_calls: list,
        progress: int,
        client_manager
    ) -> str:
        """Handle inventory stock update via manual adjustment"""
        logger.info(f"[{trace_id}] Handling inventory_update intent")
        
        # Parse entities
        try:
            entities = json.loads(intent_response.entities_json)
            inventory_entities = entities.get("entities", {})
        except:
            inventory_entities = {}
        
        logger.info(f"[{trace_id}] Inventory update entities: {inventory_entities}")
        
        # Extract product name and new quantity
        product_name = (
            inventory_entities.get("product_name") or 
            inventory_entities.get("produk_id")
        )
        new_quantity = inventory_entities.get("new_quantity")
        
        # Validation: product name required
        if not product_name:
            return "Produk apa yang mau diupdate stoknya?"
        
        # Validation: new quantity required and must be >= 0
        if new_quantity is None or new_quantity < 0:
            return "Stok mau diupdate jadi berapa?"
        
        # FIX: Generate produk_id UUID (same as transaction_handler)
        name_hash = hashlib.md5(f"{request.tenant_id}:{product_name}".encode()).hexdigest()
        produk_id = str(uuid.UUID(name_hash))
        
        logger.info(f"[{trace_id}] Resolved produk_id: {product_name} → {produk_id}")
        
        # Call inventory_service.AdjustStock
        try:
            adjust_request = inventory_service_pb2.AdjustStockRequest(
                tenant_id=request.tenant_id,
                produk_id=produk_id,
                lokasi_gudang="gudang-utama",
                new_quantity=float(new_quantity),
                reason=f"Manual adjustment via tenant chat: {request.message}"
            )
            
            adjust_response = await client_manager.stubs['inventory'].AdjustStock(
                adjust_request
            )
            
            # Build success response
            if adjust_response.success:
                stok_sebelum = int(adjust_response.stok_sebelum)
                stok_setelah = int(adjust_response.stok_setelah)
                
                milky_response = f"✅ Stok {product_name} sudah diupdate "
                
                if stok_sebelum != 0:
                    milky_response += f"dari {stok_sebelum} pcs → {stok_setelah} pcs"
                else:
                    milky_response += f"jadi {stok_setelah} pcs"
                
                return milky_response
            else:
                return f"⚠️ Gagal update stok: {adjust_response.message}"
        
        except grpc.RpcError as e:
            logger.error(f"[{trace_id}] Inventory service error: {e}")
            return f"Maaf, ada kendala update stok. Error: {e.details()[:100]}"
        except Exception as e:
            logger.error(f"[{trace_id}] Unexpected error: {e}")
            return f"Ada kendala update stok. Error: {str(e)[:100]}"
    
    @staticmethod
    async def generate_stock_alert(
        stock_response,
        produk_name: str,
        trace_id: str
    ) -> str:
        """
        Generate stock alert message for low/critical stock levels
        
        Args:
            stock_response: GetStockLevelResponse from inventory_service
            produk_name: Product name for display
            trace_id: Trace ID for logging
            
        Returns:
            Alert message string (empty if no alert needed)
        """
        # Check if low stock alert
        if not stock_response.is_low_stock:
            return ""
        
        stok_sekarang = int(stock_response.current_stock)
        minimum_stock = int(stock_response.minimum_stock) if stock_response.minimum_stock else 0
        satuan = stock_response.satuan or "pcs"
        
        # Critical level: < 5 units OR < 25% of minimum
        is_critical = stok_sekarang < 5 or (minimum_stock > 0 and stok_sekarang < minimum_stock * 0.25)
        
        if is_critical:
            alert_message = f"\n\n🚨 CRITICAL: Stok {produk_name} hampir habis! "
            alert_message += f"Hanya tersisa {stok_sekarang} {satuan}!"
        else:
            alert_message = f"\n\n⚠️ Stok {produk_name} menipis! "
            alert_message += f"Tersisa {stok_sekarang} {satuan}"
            if minimum_stock > 0:
                alert_message += f" (minimum: {minimum_stock} {satuan})"
        
        logger.info(f"[{trace_id}] 📢 Stock alert generated for {produk_name}: {stok_sekarang} {satuan}")
        
        return alert_message
    
    @staticmethod
    async def handle_inventory_history(
        request,
        ctx_response,
        intent_response,
        trace_id: str,
        service_calls: list,
        progress: int,
        client_manager
    ) -> str:
        """
        Handle inventory movement history query
        Examples:
        - "riwayat stok kain cotton bulan lalu"
        - "history pergerakan stok ballpoint"
        """
        logger.info(f"[{trace_id}] Handling inventory_history intent")
        
        # Parse entities
        try:
            inventory_entities = json.loads(intent_response.entities_json)
        except Exception as e:
            logger.error(f"[{trace_id}] Failed to parse entities: {e}")
            inventory_entities = {}
        
        logger.info(f"[{trace_id}] Inventory history entities: {inventory_entities}")
        
        # Extract product name
        product_name = (
            inventory_entities.get("product_name") or 
            inventory_entities.get("produk_id") or
            inventory_entities.get("nama_produk") or
            inventory_entities.get("product")
        )
        
        if not product_name:
            return "Mohon maaf, produk apa yang mau dicek riwayatnya? Bisa tolong sebutkan nama produknya?"
        
        logger.info(f"[{trace_id}] Product name extracted: {product_name}")
        
        # Generate produk_id UUID
        name_hash = hashlib.md5(f"{request.tenant_id}:{product_name}".encode()).hexdigest()
        produk_id = str(uuid.UUID(name_hash))
        
        logger.info(f"[{trace_id}] Resolved produk_id: {product_name} → {produk_id}")
        
        # Parse date range if provided
        date_range = inventory_entities.get("date_range")
        start_date = None
        end_date = None
        
        if date_range:
            # Parse YYYY-MM format
            try:
                from datetime import datetime, timedelta
                year, month = date_range.split("-")
                start_date = int(datetime(int(year), int(month), 1).timestamp() * 1000)
                if int(month) == 12:
                    end_date = int(datetime(int(year) + 1, 1, 1).timestamp() * 1000) - 1
                else:
                    end_date = int(datetime(int(year), int(month) + 1, 1).timestamp() * 1000) - 1
            except:
                pass
        
        # If no date range, default to last 30 days
        if not start_date or not end_date:
            from datetime import datetime, timedelta
            end_date = int(datetime.now().timestamp() * 1000)
            start_date = int((datetime.now() - timedelta(days=30)).timestamp() * 1000)
        
        # Call inventory_service.GetStockMovementHistory
        try:
            history_start = datetime.now()
            
            history_request = inventory_service_pb2.GetStockMovementHistoryRequest(
                tenant_id=request.tenant_id,
                produk_id=produk_id,
                lokasi_gudang=inventory_entities.get("lokasi_gudang", ""),
                start_date=start_date,
                end_date=end_date,
                limit=50
            )
            
            history_response = await client_manager.stubs['inventory'].GetStockMovementHistory(
                history_request
            )
            
            history_duration = (datetime.now() - history_start).total_seconds() * 1000
            service_calls.append({
                "service_name": "inventory",
                "method": "GetStockMovementHistory",
                "duration_ms": int(history_duration),
                "status": "success"
            })
            
            # Format response
            if not history_response.movements or len(history_response.movements) == 0:
                return f"📋 Belum ada riwayat pergerakan stok untuk {product_name} dalam periode yang diminta.\n\n💡 Pastikan produk sudah pernah ada transaksi masuk/keluar."
            
            milky_response = f"📋 Riwayat Pergerakan Stok {product_name}:\n\n"
            
            for movement in history_response.movements[:20]:  # Limit to 20 most recent
                movement_date = datetime.fromtimestamp(movement.movement_at / 1000).strftime("%d %b %Y %H:%M")
                jenis = "➕ Masuk" if movement.jenis_movement == "masuk" else "➖ Keluar" if movement.jenis_movement == "keluar" else "🔄 Adjustment"
                jumlah = abs(movement.jumlah_movement)
                stok_sebelum = int(movement.stok_sebelum)
                stok_setelah = int(movement.stok_setelah)
                
                milky_response += f"{jenis} {jumlah} pcs\n"
                milky_response += f"   Stok: {stok_sebelum} → {stok_setelah} pcs\n"
                milky_response += f"   {movement_date}\n"
                if movement.keterangan:
                    milky_response += f"   📝 {movement.keterangan}\n"
                milky_response += "\n"
            
            if len(history_response.movements) > 20:
                milky_response += f"\n... dan {len(history_response.movements) - 20} pergerakan lainnya"
            
            return milky_response
            
        except grpc.RpcError as e:
            logger.error(f"[{trace_id}] Inventory service error: {e}")
            if e.code() == grpc.StatusCode.NOT_FOUND:
                return f"Produk {product_name} belum terdaftar di inventory"
            else:
                return f"Maaf, gagal ambil riwayat stok. Error: {e.details()[:100]}"
        except Exception as e:
            logger.error(f"[{trace_id}] Unexpected error: {e}")
            return f"Ada kendala ambil riwayat stok. Error: {str(e)[:100]}"