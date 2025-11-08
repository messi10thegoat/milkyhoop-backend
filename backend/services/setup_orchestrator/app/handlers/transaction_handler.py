"""
Transaction Handler
Extracted from setup_orchestrator grpc_server.py for better modularity
Handles:
- transaction_record: Financial transaction recording with inventory integration
"""

import logging
import json
from datetime import datetime

import setup_orchestrator_pb2
import transaction_service_pb2
import inventory_service_pb2

logger = logging.getLogger(__name__)


def format_rupiah(rupiah_amount):
    """
    Format rupiah to Indonesian Rupiah (PUEBI + SAK EMKM compliant)
    
    Args:
        rupiah_amount: Amount in rupiah (integer)
        
    Returns:
        Formatted string: "Rp300.000" (titik sebagai pemisah ribuan)
    """
    formatted = f"{int(rupiah_amount):,}".replace(",", ".")
    return f"Rp{formatted}"


class TransactionHandler:
    """Handler for transaction recording operations"""
    
    @staticmethod
    async def handle_transaction_record(
        request,
        ctx_response,
        intent_response,
        trace_id: str,
        service_calls: list,
        progress: int,
        message_hash: str,
        client_manager
    ) -> setup_orchestrator_pb2.ProcessSetupChatResponse:
        """
        Handle financial transaction recording
        Route to transaction_service for SAK EMKM compliant transaction creation
        """
        logger.info(f"[{trace_id}] Handling transaction_record intent")
        
        # Parse entities from intent_response
        try:
            entities = json.loads(intent_response.entities_json)
            transaction_entities = entities.get("entities", {})
        except:
            transaction_entities = {}
        
        logger.info(f"[{trace_id}] Transaction entities: {transaction_entities}")
        
        # Validate required fields
        jenis_transaksi = transaction_entities.get("jenis_transaksi")
        total_nominal = transaction_entities.get("total_nominal")
        
        if not jenis_transaksi:
            milky_response = "Hmm, transaksinya jenis apa nih? Penjualan, pembelian, atau beban?"
            return setup_orchestrator_pb2.ProcessSetupChatResponse(
                status="success",
                milky_response=milky_response,
                current_state="awaiting_transaction_type",
                session_id=request.session_id,
                progress_percentage=progress,
                next_action="clarify_transaction_type"
            )
        
        if not total_nominal or total_nominal == 0:
            milky_response = "Nominalnya berapa nih? Biar aku catat dengan benar."
            return setup_orchestrator_pb2.ProcessSetupChatResponse(
                status="success",
                milky_response=milky_response,
                current_state="awaiting_nominal",
                session_id=request.session_id,
                progress_percentage=progress,
                next_action="clarify_nominal"
            )
        

        # ============================================
        # DETECT MODAL & PRIVE (Bug Fix #3)
        # ============================================
        is_modal = transaction_entities.get("is_modal", False)
        is_prive = transaction_entities.get("is_prive", False)
        
        # Fallback: detect from jenis_transaksi or keywords
        if not is_modal and not is_prive:
            message_lower = request.message.lower()
            if "setor modal" in message_lower or "tambah modal" in message_lower or jenis_transaksi == "modal":
                is_modal = True
                jenis_transaksi = "beban"  # Use beban with is_modal flag
            elif "prive" in message_lower or "ambil" in message_lower and "pribadi" in message_lower:
                is_prive = True
                jenis_transaksi = "beban"  # Use beban with is_prive flag
        
        logger.info(f"[{trace_id}] is_modal={is_modal}, is_prive={is_prive}")






        # Build confirmation message
        nominal_display = format_rupiah(int(total_nominal))
        pihak = transaction_entities.get("nama_pihak", "")
        metode = transaction_entities.get("metode_pembayaran", "cash")
        status_bayar = transaction_entities.get("status_pembayaran", "lunas")
        
        # Build natural language confirmation based on type
        if is_modal:
            milky_response = f"Ok setor modal {nominal_display} "
        elif is_prive:
            milky_response = f"Ok ambil {nominal_display} untuk keperluan pribadi "
        else:
            jenis_display = {
                "penjualan": "jual",
                "pembelian": "beli",
                "beban": "bayar"
            }.get(jenis_transaksi, jenis_transaksi)
            milky_response = f"Ok {jenis_display} "
        
        # Add items if present
        items = transaction_entities.get("items", [])
        if items and len(items) > 0:
            first_item = items[0]
            jumlah = int(first_item.get("jumlah", 0))  # Remove .0
            satuan = first_item.get("satuan", "pcs")
            nama_produk = first_item.get("nama_produk", "item").lower()
            milky_response += f"{jumlah} {satuan} {nama_produk} "
        
        # Add pihak
        if pihak:
            if jenis_transaksi == "penjualan":
                milky_response += f"ke {pihak} "
            elif jenis_transaksi == "pembelian":
                milky_response += f"dari {pihak} "
        
        # Add payment info
        if status_bayar == "dp":
            nominal_dibayar = transaction_entities.get("nominal_dibayar", 0)
            if nominal_dibayar > 0:
                bayar_display = format_rupiah(int(nominal_dibayar))
                milky_response += f"DP {bayar_display} "
        
        # Indonesian payment method
        metode_display = {
            "cash": "secara tunai",
            "transfer": "secara transfer",
            "tempo": "secara tempo"
        }.get(metode.lower(), metode)
        
        milky_response += f"{metode_display}. "
        milky_response += f"Total {nominal_display}, Bilang ya kak kalau ada koreksi 😊"

        # Call transaction_service to create transaction
        trans_start = datetime.now()
        
        try:
            # Extract items from transaction entities
            items = transaction_entities.get("items", [])
            
            # ============================================================
            # BUILD TRANSACTION PAYLOAD BASED ON TYPE
            # ============================================================
            transaction_payload = None

            


            
            if jenis_transaksi == "penjualan":
                # Build ItemPenjualan array
                items_penjualan = []
                for item in items:
                    item_proto = transaction_service_pb2.ItemPenjualan(
                        name=item.get("nama_produk", ""),
                        quantity=int(item.get("jumlah", 0)),
                        unit=item.get("satuan", "pcs"),
                        unit_price=int(item.get("harga_satuan", 0)),
                        subtotal=int(item.get("subtotal", 0))
                    )
                    items_penjualan.append(item_proto)
                
                # Build TransaksiPenjualan
                transaction_payload = transaction_service_pb2.TransaksiPenjualan(
                    customer_name=transaction_entities.get("nama_pihak", ""),
                    items=items_penjualan,
                    subtotal=int(total_nominal),
                    discount=0,
                    tax=0,
                    total_nominal=int(total_nominal),
                    payment_method=metode,
                    payment_status=status_bayar,
                    amount_paid=int(transaction_entities.get("nominal_dibayar", total_nominal)),
                    amount_due=int(transaction_entities.get("sisa_piutang_hutang", 0)),
                    notes=transaction_entities.get("keterangan", request.message)
                )
                logger.info(f"[{trace_id}] 📦 Built penjualan payload with {len(items_penjualan)} items")
            
            elif jenis_transaksi == "pembelian":
                # Build ItemPembelian array
                items_pembelian = []
                for item in items:
                    item_proto = transaction_service_pb2.ItemPembelian(
                        name=item.get("nama_produk", ""),
                        quantity=int(item.get("jumlah", 0)),
                        unit=item.get("satuan", "pcs"),
                        unit_price=int(item.get("harga_satuan", 0)),
                        subtotal=int(item.get("subtotal", 0))
                    )
                    items_pembelian.append(item_proto)
                
                # Build TransaksiPembelian
                transaction_payload = transaction_service_pb2.TransaksiPembelian(
                    vendor_name=transaction_entities.get("nama_pihak", "supplier"),
                    items=items_pembelian,
                    subtotal=int(total_nominal),
                    discount=0,
                    tax=0,
                    total_nominal=int(total_nominal),
                    payment_method=metode,
                    payment_status=status_bayar,
                    amount_paid=int(transaction_entities.get("nominal_dibayar", total_nominal)),
                    amount_due=int(transaction_entities.get("sisa_piutang_hutang", 0)),
                    notes=transaction_entities.get("keterangan", request.message)
                )
                logger.info(f"[{trace_id}] 📦 Built pembelian payload with {len(items_pembelian)} items")
            

            elif is_modal or is_prive:
                # Modal/Prive menggunakan TransaksiBeban dengan kategori khusus
                kategori_khusus = "modal" if is_modal else "prive"
                transaction_payload = transaction_service_pb2.TransaksiBeban(
                    kategori=kategori_khusus,
                    deskripsi=transaction_entities.get("keterangan", request.message),
                    nominal=int(total_nominal),
                    payment_method=metode,
                    recipient=transaction_entities.get("nama_pihak", "owner"),
                    notes=request.message
                )
                logger.info(f"[{trace_id}] 📦 Built {kategori_khusus} as TransaksiBeban payload")






            elif jenis_transaksi == "beban":
                # Build TransaksiBeban (no items)
                transaction_payload = transaction_service_pb2.TransaksiBeban(
                    kategori=transaction_entities.get("kategori_beban", "operasional"),
                    deskripsi=transaction_entities.get("keterangan", request.message),
                    nominal=int(total_nominal),
                    payment_method=metode,
                    recipient=transaction_entities.get("nama_pihak", ""),
                    notes=request.message
                )
                logger.info(f"[{trace_id}] 📦 Built beban payload")

            # ============================================================
            # BUILD INVENTORY IMPACT PROTO
            # ============================================================
            inventory_impact_proto = None
            inventory_impact_data = transaction_entities.get("inventory_impact")
            logger.info(f"[{trace_id}] DEBUG: inventory_impact_data = {inventory_impact_data}")
            
            # Force inventory tracking for pembelian/penjualan
            if inventory_impact_data and isinstance(inventory_impact_data, dict) and inventory_impact_data.get("is_tracked") and jenis_transaksi in ["pembelian", "penjualan"]:
                # Build items_inventory list
                items_inventory_proto = []
                for item_inv in inventory_impact_data.get("items_inventory", []):
                    # Handle 'unknown' stok_setelah for penjualan
                    stok_setelah_value = item_inv.get("stok_setelah", 0)
                    
                    # If stok_setelah is 'unknown' or 0, query current stock
                    if stok_setelah_value == 'unknown' or stok_setelah_value == 'Unknown' or stok_setelah_value == 0 or stok_setelah_value == 0.0:
                        try:
                            produk_id = item_inv.get("produk_id", "")
                            # Normalize lokasi_gudang (underscore to dash)
                            lokasi = inventory_impact_data.get("lokasi_gudang", "gudang-utama").replace("_", "-")
                            
                            # Query current stock from inventory service
                            stock_req = inventory_service_pb2.GetStockLevelRequest(
                                tenant_id=request.tenant_id,
                                produk_id=produk_id,
                                lokasi_gudang=lokasi
                            )
                            stock_resp = await client_manager.stubs['inventory'].GetStockLevel(stock_req)
                            
                            # Calculate stok_setelah
                            current_stock = stock_resp.current_stock
                            jumlah_movement = float(item_inv.get("jumlah_movement", 0))
                            stok_setelah_value = current_stock + jumlah_movement  # movement is negative for keluar
                            
                            logger.info(f"[{trace_id}] 📊 Calculated stok_setelah: {current_stock} + ({jumlah_movement}) = {stok_setelah_value}")
                        except Exception as e:
                            logger.error(f"[{trace_id}] Failed to query stock for {produk_id}: {e}")
                            stok_setelah_value = 0  # Fallback to 0
                    
                    item_inv_proto = inventory_service_pb2.ItemInventory(
                        produk_id=item_inv.get("produk_id", ""),
                        jumlah_movement=float(item_inv.get("jumlah_movement", 0)),
                        stok_setelah=float(stok_setelah_value),
                        nilai_per_unit=float(item_inv.get("nilai_per_unit", 0))
                    )
                    items_inventory_proto.append(item_inv_proto)
                    logger.info(f"[{trace_id}] DEBUG: ItemInventory created - produk_id={item_inv.get('produk_id')}, jumlah_movement={item_inv.get('jumlah_movement')}")
                
                # Build InventoryImpact proto
                # Normalize lokasi_gudang at source: underscore to dash
                raw_lokasi = inventory_impact_data.get("lokasi_gudang", "gudang-utama")
                normalized_lokasi = raw_lokasi.replace('_', '-')
                
                inventory_impact_proto = inventory_service_pb2.InventoryImpact(
                    is_tracked=True,
                    jenis_movement=inventory_impact_data.get("jenis_movement", ""),
                    lokasi_gudang=normalized_lokasi,
                    items_inventory=items_inventory_proto
                )

            # ============================================================
            # BUILD CREATE TRANSACTION REQUEST (CORRECT ONEOF)
            # ============================================================
            if jenis_transaksi == "penjualan":
                create_request = transaction_service_pb2.CreateTransactionRequest(
                    tenant_id=request.tenant_id,
                    created_by=request.user_id,
                    actor_role="owner",
                    jenis_transaksi=jenis_transaksi,
                    penjualan=transaction_payload,
                    raw_text=request.message,
                    idempotency_key=f"{request.session_id}_{message_hash}", 
                    inventory_impact=inventory_impact_proto,
                    is_modal=is_modal,
                    is_prive=is_prive,
                )
            elif jenis_transaksi == "pembelian":
                create_request = transaction_service_pb2.CreateTransactionRequest(
                    tenant_id=request.tenant_id,
                    created_by=request.user_id,
                    actor_role="owner",
                    jenis_transaksi=jenis_transaksi,
                    pembelian=transaction_payload,
                    raw_text=request.message,
                    idempotency_key=f"{request.session_id}_{message_hash}",
                    inventory_impact=inventory_impact_proto,
                )


            elif jenis_transaksi == "beban" or is_modal or is_prive:
                create_request = transaction_service_pb2.CreateTransactionRequest(
                    tenant_id=request.tenant_id,
                    created_by=request.user_id,
                    actor_role="owner",
                    jenis_transaksi=jenis_transaksi,
                    beban=transaction_payload,
                    raw_text=request.message,
                    idempotency_key=f"{request.session_id}_{message_hash}",
                    is_modal=is_modal,
                    is_prive=is_prive,
                )
            
            


            
            logger.info(f"[{trace_id}] DEBUG: CreateTransactionRequest built - inventory_impact={'SET' if inventory_impact_proto else 'NULL'}")
            
            # ============================================================
            # CALL TRANSACTION SERVICE
            # ============================================================
            trans_response = await client_manager.stubs['transaction'].CreateTransaction(
                create_request
            )
            
            trans_duration = (datetime.now() - trans_start).total_seconds() * 1000
            service_calls.append({
                "service_name": "transaction",
                "method": "CreateTransaction",
                "duration_ms": int(trans_duration),
                "status": "success"
            })

            # ============================================================
            # BUILD SUCCESS RESPONSE
            # ============================================================
            if trans_response.success:
                milky_response = f"✅ Transaksi dicatat! {milky_response}"
                milky_response += f"\n\nID: {trans_response.transaction.id[:8]}..."
                
                # Get updated stock info if inventory was tracked
                stock_info = ""
                if inventory_impact_proto and inventory_impact_proto.items_inventory:
                    for item in inventory_impact_proto.items_inventory:
                        try:
                            # Normalize lokasi_gudang before query (defense in depth)
                            normalized_lokasi_query = inventory_impact_proto.lokasi_gudang.replace('_', '-')
                            
                            # Query current stock after update
                            stock_req = inventory_service_pb2.GetStockLevelRequest(
                                tenant_id=request.tenant_id,
                                produk_id=item.produk_id,
                                lokasi_gudang=normalized_lokasi_query
                            )
                            stock_resp = await client_manager.stubs['inventory'].GetStockLevel(stock_req)
                            
                            stok_sebelum = stock_resp.current_stock - item.jumlah_movement
                            stock_info += f"\n\n📦 Stok {item.produk_id} sekarang: {int(stock_resp.current_stock)} {stock_resp.satuan}"
                            if stok_sebelum != 0:
                                stock_info += f" (sebelumnya {int(stok_sebelum)} {stock_resp.satuan})"
                        except:
                            pass  # Skip if stock query fails
                
                # Append stock info to milky_response
                if stock_info:
                    milky_response += stock_info
                
                # Check for low stock alerts
                if inventory_impact_proto and inventory_impact_proto.items_inventory:
                    # Import InventoryHandler for alert generation
                    from handlers.inventory_handler import InventoryHandler
                    
                    for item in inventory_impact_proto.items_inventory:
                        try:
                            # Query stock level for alert check
                            alert_req = inventory_service_pb2.GetStockLevelRequest(
                                tenant_id=request.tenant_id,
                                produk_id=item.produk_id,
                                lokasi_gudang=inventory_impact_proto.lokasi_gudang.replace('_', '-')
                            )
                            alert_resp = await client_manager.stubs['inventory'].GetStockLevel(alert_req)
                            
                            # Generate alert message
                            alert_message = await InventoryHandler.generate_stock_alert(
                                alert_resp, item.produk_id, trace_id
                            )
                            
                            if alert_message:
                                milky_response += alert_message
                        except:
                            pass  # Skip if alert check fails
                
                current_state = "transaction_recorded"
            else:
                milky_response = f"⚠️ Gagal catat transaksi: {trans_response.message}"
                current_state = "transaction_failed"
            
        except Exception as e:
            logger.error(f"[{trace_id}] Transaction creation failed: {e}")
            milky_response = f"Maaf, ada kendala catat transaksi. Error: {str(e)[:100]}"
            current_state = "transaction_error"
        
        return setup_orchestrator_pb2.ProcessSetupChatResponse(
            status="success",
            milky_response=milky_response,
            current_state=current_state,
            session_id=request.session_id,
            progress_percentage=progress,
            next_action="continue"
        )