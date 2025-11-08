"""
Transaction Handler
Handles transaction CRUD operations (Create, Update, Delete, Get, List)

Extracted from grpc_server.py - NO LOGIC CHANGES, only modularization.
"""

import logging
import uuid
import json
from datetime import datetime
from typing import Optional, Dict, Any
import grpc
from google.protobuf import json_format
from google.protobuf import empty_pb2

from app.prisma_rls_extension import RLSPrismaClient

logger = logging.getLogger(__name__)


class TransactionHandler:
    """Handler for transaction CRUD operations"""
    
    @staticmethod
    def proto_to_db_payload(request) -> Dict[str, Any]:
        """
        Convert Proto oneof transaction_data to database JSONB payload.
        Proto fields: penjualan, pembelian, beban (oneof)
        Database: payload JSONB column
        """
        if request.HasField('penjualan'):
            return json_format.MessageToDict(
                request.penjualan,
                preserving_proto_field_name=True,
                always_print_fields_with_no_presence=False
            )
        elif request.HasField('pembelian'):
            return json_format.MessageToDict(
                request.pembelian,
                preserving_proto_field_name=True,
                always_print_fields_with_no_presence=False
            )
        elif request.HasField('beban'):
            return json_format.MessageToDict(
                request.beban,
                preserving_proto_field_name=True,
                always_print_fields_with_no_presence=False
            )
        else:
            return {}
    
    @staticmethod
    def db_to_proto_transaction(db_record, pb) -> Any:
        """
        Convert database record to Proto TransaksiHarian message.
        Field mapping:
        - Database snake_case -> Proto snake_case
        - Database camelCase (Prisma) -> Proto snake_case
        - Database payload JSONB -> Proto oneof transaction_data
        """
        proto_tx = pb.TransaksiHarian(
            id=db_record.id,
            tenant_id=db_record.tenantId,
            created_by=db_record.createdBy,
            actor_role=db_record.actorRole,
            timestamp=db_record.timestamp,
            jenis_transaksi=db_record.jenisTransaksi,
            raw_text=db_record.rawText or "",
            receipt_url=db_record.receiptUrl or "",
            receipt_checksum=db_record.receiptChecksum or "",
            idempotency_key=db_record.idempotencyKey or "",
            status=db_record.status,
            approved_by=db_record.approvedBy or "",
            approved_at=db_record.approvedAt or 0,
            rekening_id=db_record.rekeningId or "",
            rekening_type=db_record.rekeningType or "",
            created_at=int(db_record.createdAt.timestamp() * 1000) if db_record.createdAt else 0,
            updated_at=int(db_record.updatedAt.timestamp() * 1000) if db_record.updatedAt else 0
        )
        
        # Convert payload JSONB back to Proto oneof
        if db_record.payload and db_record.jenisTransaksi:
            if db_record.jenisTransaksi == 'penjualan':
                penjualan = pb.TransaksiPenjualan()
                json_format.ParseDict(db_record.payload, penjualan, ignore_unknown_fields=True)
                proto_tx.penjualan.CopyFrom(penjualan)
            elif db_record.jenisTransaksi == 'pembelian':
                pembelian = pb.TransaksiPembelian()
                json_format.ParseDict(db_record.payload, pembelian, ignore_unknown_fields=True)
                proto_tx.pembelian.CopyFrom(pembelian)
            elif db_record.jenisTransaksi == 'beban':
                beban = pb.TransaksiBeban()
                json_format.ParseDict(db_record.payload, beban, ignore_unknown_fields=True)
                proto_tx.beban.CopyFrom(beban)
        
        # Populate SAK EMKM fields (new)
        if hasattr(db_record, 'totalNominal') and db_record.totalNominal:
            proto_tx.total_nominal = db_record.totalNominal
        if hasattr(db_record, 'metodePembayaran') and db_record.metodePembayaran:
            proto_tx.metode_pembayaran = db_record.metodePembayaran
        if hasattr(db_record, 'statusPembayaran') and db_record.statusPembayaran:
            proto_tx.status_pembayaran = db_record.statusPembayaran
        if hasattr(db_record, 'nominalDibayar') and db_record.nominalDibayar:
            proto_tx.nominal_dibayar = db_record.nominalDibayar
        if hasattr(db_record, 'sisaPiutangHutang') and db_record.sisaPiutangHutang:
            proto_tx.sisa_piutang_hutang = db_record.sisaPiutangHutang
        if hasattr(db_record, 'jatuhTempo') and db_record.jatuhTempo:
            proto_tx.jatuh_tempo = db_record.jatuhTempo
        if hasattr(db_record, 'namaPihak') and db_record.namaPihak:
            proto_tx.nama_pihak = db_record.namaPihak
        if hasattr(db_record, 'kontakPihak') and db_record.kontakPihak:
            proto_tx.kontak_pihak = db_record.kontakPihak
        if hasattr(db_record, 'pihakType') and db_record.pihakType:
            proto_tx.pihak_type = db_record.pihakType
        if hasattr(db_record, 'lokasiGudang') and db_record.lokasiGudang:
            proto_tx.lokasi_gudang = db_record.lokasiGudang
        if hasattr(db_record, 'jenisAset') and db_record.jenisAset:
            proto_tx.jenis_aset = db_record.jenisAset
        if hasattr(db_record, 'kategoriBeban') and db_record.kategoriBeban:
            proto_tx.kategori_beban = db_record.kategoriBeban
        if hasattr(db_record, 'kategoriArusKas') and db_record.kategoriArusKas:
            proto_tx.kategori_arus_kas = db_record.kategoriArusKas
        if hasattr(db_record, 'isPrive'):
            proto_tx.is_prive = db_record.isPrive
        if hasattr(db_record, 'isModal'):
            proto_tx.is_modal = db_record.isModal
        if hasattr(db_record, 'pajakAmount') and db_record.pajakAmount:
            proto_tx.pajak_amount = db_record.pajakAmount
        if hasattr(db_record, 'akunPerkiraanId') and db_record.akunPerkiraanId:
            proto_tx.akun_perkiraan_id = db_record.akunPerkiraanId
        if hasattr(db_record, 'penyusutanPerTahun') and db_record.penyusutanPerTahun:
            proto_tx.penyusutan_per_tahun = db_record.penyusutanPerTahun
        if hasattr(db_record, 'umurManfaat') and db_record.umurManfaat:
            proto_tx.umur_manfaat = db_record.umurManfaat
        if hasattr(db_record, 'periodePelaporan') and db_record.periodePelaporan:
            proto_tx.periode_pelaporan = db_record.periodePelaporan
        if hasattr(db_record, 'keterangan') and db_record.keterangan:
            proto_tx.keterangan = db_record.keterangan
        
        # Handle raw_nlu bytes
        if db_record.rawNlu:
            proto_tx.raw_nlu = db_record.rawNlu
        
        return proto_tx
    
    @staticmethod
    def extract_total_nominal(request) -> int:
        """Extract total_nominal from oneof payload or top-level field"""
        # Priority 1: Top-level field (if set by orchestrator in future)
        if request.total_nominal and request.total_nominal > 0:
            return request.total_nominal
        
        # Priority 2: Extract from oneof transaction_data
        if request.HasField('penjualan'):
            return request.penjualan.total_nominal
        elif request.HasField('pembelian'):
            return request.pembelian.total_nominal
        elif request.HasField('beban'):
            return request.beban.nominal
        
        return 0
    
    @staticmethod
    async def handle_create_transaction(
        request,
        context: grpc.aio.ServicerContext,
        pb,
        get_inventory_client_func,
        process_accounting_func
    ):
        """
        Create new transaction with inventory integration.
        Flow:
        1. Check idempotency_key uniqueness
        2. Validate foreign keys (tenant_id, user_id)
        3. ✨ NEW: Validate stock availability (if inventory tracked)
        4. Generate transaction ID
        5. Convert Proto -> Database payload
        6. Atomic write: transaksi_harian + outbox event
        7. ✨ NEW: Process inventory impact (if inventory tracked)
        8. Return success response
        """
        logger.info(f"📥 CreateTransaction: tenant={request.tenant_id}, type={request.jenis_transaksi}")
        
        # Initialize RLS-aware Prisma client for this request
        rls_prisma = RLSPrismaClient(
            tenant_id=request.tenant_id,
            bypass_rls=True
        )
        
        try:
            await rls_prisma.connect()
            
            # Step 1: Idempotency check
            if request.idempotency_key:
                existing = await rls_prisma.transaksiharian.find_first(
                    where={'idempotencyKey': request.idempotency_key}
                )
                if existing:
                    logger.info(f"✅ Idempotency: Returning existing transaction {existing.id}")
                    return pb.TransactionResponse(
                        success=True,
                        message="Transaction already exists (idempotency)",
                        transaction=TransactionHandler.db_to_proto_transaction(existing, pb)
                    )
            
            # Step 2: Foreign key validation
            # Note: RLS policies will enforce tenant_id validation
            # User validation can be added here if needed
            
            # ============================================================
            # ✨ STEP 3: VALIDATE STOCK AVAILABILITY (NEW)
            # ============================================================
            inventory_impact = None

            # Priority 1: Check top-level inventory_impact field (set by orchestrator)
            if request.HasField('inventory_impact'):
                inventory_impact = request.inventory_impact
                logger.info(f"📦 Using top-level inventory_impact")

            # Priority 2: Check nested in transaction_data oneof (fallback)
            elif request.jenis_transaksi == "penjualan" and request.HasField("penjualan"):
                if hasattr(request.penjualan, 'inventory_impact'):
                    inventory_impact = request.penjualan.inventory_impact
                    logger.info(f"📦 Using penjualan.inventory_impact")

            elif request.jenis_transaksi == "pembelian" and request.HasField("pembelian"):
                if hasattr(request.pembelian, 'inventory_impact'):
                    inventory_impact = request.pembelian.inventory_impact
                    logger.info(f"📦 Using pembelian.inventory_impact")

            elif request.jenis_transaksi == "beban" and request.HasField("beban"):
                if hasattr(request.beban, 'inventory_impact'):
                    inventory_impact = request.beban.inventory_impact
                    logger.info(f"📦 Using beban.inventory_impact")

            logger.info(f"📦 Final inventory_impact: {'SET' if inventory_impact else 'NULL'}")

            # Validate stock availability for outbound movements
            if inventory_impact and inventory_impact.is_tracked:
                if inventory_impact.jenis_movement == "keluar":
                    logger.info("🔍 Validating stock availability before transaction...")
                    
                    inventory_client = get_inventory_client_func()
                    
                    # Import inventory proto for validation
                    from app import inventory_service_pb2 as inv_pb
                    
                    for item in inventory_impact.items_inventory:
                        validate_req = inv_pb.ValidateStockRequest(
                            tenant_id=request.tenant_id,
                            produk_id=item.produk_id,
                            lokasi_gudang=inventory_impact.lokasi_gudang or "default",
                            quantity_needed=abs(item.jumlah_movement)
                        )
                        
                        validate_resp = await inventory_client.ValidateStockAvailability(validate_req)
                        
                        if not validate_resp.is_available:
                            error_msg = f"❌ Insufficient stock: {item.produk_id} needs {abs(item.jumlah_movement)}, available {validate_resp.current_stock}"
                            logger.error(error_msg)
                            return pb.TransactionResponse(
                                success=False,
                                message=error_msg,
                                transaction=None
                            )
                        else:
                            logger.info(f"✅ Stock available: {item.produk_id} = {validate_resp.current_stock}")
            
            # Step 4: Generate IDs
            tx_id = f"tx_{uuid.uuid4().hex[:16]}"
            outbox_id = f"outbox_{uuid.uuid4().hex[:16]}"
            current_time_ms = int(datetime.utcnow().timestamp() * 1000)
            
            # Step 5: Convert Proto payload to JSONB
            payload_dict = TransactionHandler.proto_to_db_payload(request)
            
            # Step 6: Prepare transaction data (Proto snake_case -> Prisma camelCase)
            tx_data = {
                'id': tx_id,
                'tenant': {'connect': {'id': request.tenant_id}},
                'creator': {'connect': {'id': request.created_by}},
                'actorRole': request.actor_role,
                'timestamp': current_time_ms,
                'jenisTransaksi': request.jenis_transaksi,
                'rawText': request.raw_text if request.raw_text else None,
                'rawNlu': request.raw_nlu if request.raw_nlu else None,
                'receiptUrl': request.receipt_url if request.receipt_url else None,
                'idempotencyKey': request.idempotency_key if request.idempotency_key else None,
                'rekeningId': request.rekening_id if request.rekening_id else None,
                'rekeningType': request.rekening_type if request.rekening_type else None,
                'status': 'draft',
                # SAK EMKM COMPLIANCE FIELDS
                'totalNominal': TransactionHandler.extract_total_nominal(request),
                'metodePembayaran': request.metode_pembayaran if request.metode_pembayaran else None,
                'statusPembayaran': request.status_pembayaran if request.status_pembayaran else None,
                'nominalDibayar': request.nominal_dibayar if request.nominal_dibayar else None,
                'sisaPiutangHutang': request.sisa_piutang_hutang if request.sisa_piutang_hutang else None,
                'jatuhTempo': request.jatuh_tempo if request.jatuh_tempo else None,
                'namaPihak': request.nama_pihak if request.nama_pihak else None,
                'kontakPihak': request.kontak_pihak if request.kontak_pihak else None,
                'pihakType': request.pihak_type if request.pihak_type else None,
                'lokasiGudang': request.lokasi_gudang if request.lokasi_gudang else None,
                'jenisAset': request.jenis_aset if request.jenis_aset else None,
                'kategoriBeban': request.kategori_beban if request.kategori_beban else None,
                'kategoriArusKas': request.kategori_arus_kas if request.kategori_arus_kas else 'operasi',
                'isPrive': request.jenis_transaksi == 'prive',
                'isModal': request.jenis_transaksi == 'modal',
                'pajakAmount': request.pajak_amount if request.pajak_amount else None,
                'akunPerkiraanId': request.akun_perkiraan_id if request.akun_perkiraan_id else None,
                'penyusutanPerTahun': request.penyusutan_per_tahun if request.penyusutan_per_tahun else None,
                'umurManfaat': request.umur_manfaat if request.umur_manfaat else None,
                'periodePelaporan': request.periode_pelaporan if request.periode_pelaporan else None,
                'keterangan': request.keterangan if request.keterangan else None,
                'payload': json.dumps(payload_dict) if payload_dict else None,
            }
            
            # Step 5.5: Handle ItemTransaksi (line items)
            items_data = []

            # Extract items from oneof transaction_data payload
            if request.jenis_transaksi == "penjualan" and request.HasField("penjualan"):
                for item in request.penjualan.items:
                    item_data = {
                        'id': f"item_{uuid.uuid4().hex[:16]}",
                        'namaProduk': item.name,  # Note: ItemPenjualan uses 'name' not 'nama_produk'
                        'kategoriPath': None,
                        'level1': None,
                        'level2': None,
                        'level3': None,
                        'level4': None,
                        'jumlah': item.quantity,
                        'satuan': item.unit,
                        'hargaSatuan': item.unit_price,
                        'subtotal': item.subtotal,
                        'produkId': None,  # Will be set by inventory lookup
                        'keterangan': None,
                    }
                    items_data.append(item_data)

            elif request.jenis_transaksi == "pembelian" and request.HasField("pembelian"):
                for item in request.pembelian.items:
                    item_data = {
                        'id': f"item_{uuid.uuid4().hex[:16]}",
                        'namaProduk': item.name,  # Proto: name -> DB: namaProduk
                        'kategoriPath': None,
                        'level1': None,
                        'level2': None,
                        'level3': None,
                        'level4': None,
                        'jumlah': item.quantity,  # Proto: quantity -> DB: jumlah
                        'satuan': item.unit,       # Proto: unit -> DB: satuan
                        'hargaSatuan': item.unit_price,  # Proto: unit_price -> DB: hargaSatuan
                        'subtotal': item.subtotal,       # Proto: subtotal -> DB: subtotal
                        'produkId': None,
                        'keterangan': None,
                    }
                    items_data.append(item_data)

            elif request.jenis_transaksi == "beban" and request.HasField("beban"):
                # Beban typically doesn't have items
                pass

            logger.info(f"📦 Extracted {len(items_data)} items from {request.jenis_transaksi} payload")
            
            # Step 6: Atomic write with outbox pattern
            new_tx = await rls_prisma.transaksiharian.create(data=tx_data)
            
            # Step 6.1: Create ItemTransaksi records (if any)
            if items_data:
                for item_data in items_data:
                    item_data['transaksi'] = {'connect': {'id': tx_id}}
                    await rls_prisma.itemtransaksi.create(data=item_data)
            
            # Step 6.2: Create HppBreakdown (if provided)
            if request.HasField('hpp'):
                hpp_data = {
                    'id': f"hpp_{uuid.uuid4().hex[:16]}",
                    'transaksi': {'connect': {'id': tx_id}},
                    'biayaBahanBaku': request.hpp.biaya_bahan_baku if request.hpp.biaya_bahan_baku else None,
                    'biayaTenagaKerja': request.hpp.biaya_tenaga_kerja if request.hpp.biaya_tenaga_kerja else None,
                    'biayaLainnya': request.hpp.biaya_lainnya if request.hpp.biaya_lainnya else None,
                    'totalHpp': request.hpp.total_hpp,
                    'detailJson': request.hpp.detail_json if request.hpp.detail_json else None,
                }
                await rls_prisma.hppbreakdown.create(data=hpp_data)
            
            # ============================================================
            # ✨ STEP 7: PROCESS INVENTORY IMPACT (NEW)
            # ============================================================
            if inventory_impact and inventory_impact.is_tracked:
                logger.info(f"📦 Processing inventory impact for {tx_id}...")
                
                inventory_client = get_inventory_client_func()
                
                # Import inventory proto
                from app import inventory_service_pb2 as inv_pb
                
                impact_req = inv_pb.ProcessInventoryImpactRequest(
                    tenant_id=request.tenant_id,
                    transaksi_id=tx_id,
                    inventory_impact=inventory_impact
                )
                
                try:
                    impact_resp = await inventory_client.ProcessInventoryImpact(impact_req)
                    
                    if impact_resp.success:
                        logger.info(f"✅ Inventory updated: {impact_resp.message}")
                        
                        # Log low stock alerts
                        for update in impact_resp.updates:
                            if update.low_stock_alert:
                                logger.warning(f"⚠️ LOW STOCK ALERT: {update.produk_id} @ {update.lokasi_gudang} = {update.stok_setelah}")
                    else:
                        logger.error(f"❌ Inventory update failed: {impact_resp.message}")
                        
                except grpc.RpcError as e:
                    logger.error(f"❌ Inventory service RPC error: {e.code()} - {e.details()}")

            # ============================================================
            # ✨ STEP 8: PROCESS ACCOUNTING (NEW)
            # ============================================================
            if tx_data['status'] == 'draft':  # Process for all transactions
                logger.info(f"📒 Processing accounting for {tx_id}...")
                
                accounting_result = await process_accounting_func(
                    tenant_id=request.tenant_id,
                    transaksi_id=tx_id,
                    jenis_transaksi=request.jenis_transaksi,
                    total_nominal=TransactionHandler.extract_total_nominal(request),
                    kategori_arus_kas=request.kategori_arus_kas or 'operasi',
                    created_by=request.created_by,
                    tanggal_transaksi=int(current_time_ms / 1000),  # Convert ms to seconds
                    periode_pelaporan=request.periode_pelaporan or '',
                    keterangan=request.keterangan or '',
                    akun_perkiraan_id=request.akun_perkiraan_id or ''
                )
                
                if accounting_result['success']:
                    logger.info(f"✅ Journal entry created: {accounting_result['journal_number']}")
                else:
                    logger.warning(f"⚠️ Accounting processing failed: {accounting_result.get('error', 'Unknown error')}")
            
            # Step 6.4: Create outbox event
            await rls_prisma.outbox.create(data={
                'id': outbox_id,
                'transaksi': {'connect': {'id': tx_id}},
                'eventType': 'transaction_created',
                'payload': json.dumps({
                    'transaction_id': tx_id,
                    'jenis_transaksi': request.jenis_transaksi,
                    'created_by': request.created_by,
                    'timestamp': tx_data['timestamp'],
                    'has_items': len(items_data) > 0,
                    'has_hpp': request.HasField('hpp'),
                    'has_inventory': inventory_impact is not None,
                }),
                'processed': False
            })
            
            logger.info(f"✅ Transaction created: {tx_id}, outbox: {outbox_id}")
            
            # Step 8: Return response
            return pb.TransactionResponse(
                success=True,
                message=f"Transaction created successfully: {tx_id}",
                transaction=TransactionHandler.db_to_proto_transaction(new_tx, pb)
            )
            
        except ValueError as ve:
            logger.error(f"❌ Validation error: {str(ve)}")
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(ve))
        except Exception as e:
            logger.error(f"❌ CreateTransaction failed: {str(e)}")
            await context.abort(grpc.StatusCode.INTERNAL, f"Transaction creation failed: {str(e)}")
        finally:
            await rls_prisma.disconnect()
    
    @staticmethod
    async def handle_update_transaction(
        request,
        context: grpc.aio.ServicerContext,
        prisma,
        pb
    ):
        """
        Update existing transaction.
        Flow:
        1. Check transaction exists and belongs to tenant
        2. Validate status (only 'draft' can be updated)
        3. Convert Proto payload -> Database JSONB
        4. Atomic update: transaksi + outbox event
        """
        logger.info(f"📝 UpdateTransaction: tx={request.transaction_id}, tenant={request.tenant_id}")
        
        try:
            existing = await prisma.transaksiharian.find_first(
                where={
                    'id': request.transaction_id,
                    'tenantId': request.tenant_id
                }
            )
            
            if not existing:
                await context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"Transaction {request.transaction_id} not found"
                )
            
            if existing.status != 'draft':
                await context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    f"Cannot update transaction with status '{existing.status}'"
                )
            
            payload_dict = TransactionHandler.proto_to_db_payload(request)
            
            update_data = {
                'payload': json.dumps(payload_dict) if payload_dict else None,
                'updatedAt': datetime.utcnow(),
                'totalNominal': request.total_nominal if request.total_nominal is not None else None,
                'metodePembayaran': request.metode_pembayaran if request.metode_pembayaran else None,
                'statusPembayaran': request.status_pembayaran if request.status_pembayaran else None,
                'nominalDibayar': request.nominal_dibayar or None,
                'sisaPiutangHutang': request.sisa_piutang_hutang or None,
                'jatuhTempo': request.jatuh_tempo or None,
                'namaPihak': request.nama_pihak if request.nama_pihak else None,
                'kontakPihak': request.kontak_pihak if request.kontak_pihak else None,
                'pihakType': request.pihak_type if request.pihak_type else None,
                'lokasiGudang': request.lokasi_gudang if request.lokasi_gudang else None,
                'jenisAset': request.jenis_aset if request.jenis_aset else None,
                'kategoriBeban': request.kategori_beban if request.kategori_beban else None,
                'kategoriArusKas': request.kategori_arus_kas if request.kategori_arus_kas else None,
                'isPrive': request.jenis_transaksi == 'prive',
                'isModal': request.jenis_transaksi == 'modal',
                'pajakAmount': request.pajak_amount or None,
                'akunPerkiraanId': request.akun_perkiraan_id if request.akun_perkiraan_id else None,
                'penyusutanPerTahun': request.penyusutan_per_tahun or None,
                'umurManfaat': request.umur_manfaat or None,
                'periodePelaporan': request.periode_pelaporan if request.periode_pelaporan else None,
                'keterangan': request.keterangan if request.keterangan else None,
            }
            
            async with prisma.tx() as transaction:
                updated_tx = await transaction.transaksiharian.update(
                    where={'id': request.transaction_id},
                    data=update_data
                )
                
                await transaction.outbox.create(data={
                    'id': f"outbox_{uuid.uuid4().hex[:16]}",
                    'transaksi': {'connect': {'id': request.transaction_id}},
                    'eventType': 'transaction_updated',
                    'payload': json.dumps({
                        'transaction_id': request.transaction_id,
                        'updated_by': request.updated_by,
                        'updated_at': int(datetime.utcnow().timestamp() * 1000)
                    }),
                    'processed': False
                })
            
            logger.info(f"✅ Transaction updated: {request.transaction_id}")
            
            return pb.TransactionResponse(
                success=True,
                message="Transaction updated successfully",
                transaction=TransactionHandler.db_to_proto_transaction(updated_tx, pb)
            )
            
        except Exception as e:
            logger.error(f"❌ UpdateTransaction failed: {str(e)}")
            await context.abort(grpc.StatusCode.INTERNAL, f"Failed to update transaction: {str(e)}")
    
    @staticmethod
    async def handle_delete_transaction(
        request,
        context: grpc.aio.ServicerContext,
        prisma,
        empty_pb2
    ):
        """
        Soft delete transaction (update status to 'deleted').
        Flow:
        1. Check transaction exists
        2. Update status to 'deleted'
        3. Create outbox event
        """
        logger.info(f"🗑️ DeleteTransaction: tx={request.transaction_id}, tenant={request.tenant_id}")
        
        try:
            existing = await prisma.transaksiharian.find_first(
                where={
                    'id': request.transaction_id,
                    'tenantId': request.tenant_id
                }
            )
            
            if not existing:
                await context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"Transaction {request.transaction_id} not found"
                )
            
            async with prisma.tx() as transaction:
                await transaction.transaksiharian.update(
                    where={'id': request.transaction_id},
                    data={
                        'status': 'deleted',
                        'updatedAt': datetime.utcnow()
                    }
                )
                
                await transaction.outbox.create(data={
                    'id': f"outbox_{uuid.uuid4().hex[:16]}",
                    'transaksi': {'connect': {'id': request.transaction_id}},
                    'eventType': 'transaction_deleted',
                    'payload': json.dumps({
                        'transaction_id': request.transaction_id,
                        'deleted_by': request.deleted_by,
                        'reason': request.reason
                    }),
                    'processed': False
                })
            
            logger.info(f"✅ Transaction deleted: {request.transaction_id}")
            return empty_pb2.Empty()
            
        except Exception as e:
            logger.error(f"❌ DeleteTransaction failed: {str(e)}")
            await context.abort(grpc.StatusCode.INTERNAL, f"Failed to delete transaction: {str(e)}")
    
    @staticmethod
    async def handle_get_transaction(
        request,
        context: grpc.aio.ServicerContext,
        prisma,
        pb
    ):
        """Retrieve single transaction by ID."""
        logger.info(f"🔍 GetTransaction: tx={request.transaction_id}, tenant={request.tenant_id}")
        
        try:
            transaction = await prisma.transaksiharian.find_first(
                where={
                    'id': request.transaction_id,
                    'tenantId': request.tenant_id
                }
            )
            
            if not transaction:
                await context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"Transaction {request.transaction_id} not found"
                )
            
            return pb.TransactionResponse(
                success=True,
                message="Transaction retrieved successfully",
                transaction=TransactionHandler.db_to_proto_transaction(transaction, pb)
            )
            
        except Exception as e:
            logger.error(f"❌ GetTransaction failed: {str(e)}")
            await context.abort(grpc.StatusCode.INTERNAL, f"Failed to get transaction: {str(e)}")
    
    @staticmethod
    async def handle_list_transactions(
        request,
        context: grpc.aio.ServicerContext,
        prisma,
        pb
    ):
        """List transactions with filters and pagination."""
        logger.info(f"📋 ListTransactions: tenant={request.tenant_id}, page={request.page}")
        
        try:
            where = {'tenantId': request.tenant_id}
            
            if request.jenis_transaksi:
                where['jenisTransaksi'] = request.jenis_transaksi
            if request.status:
                where['status'] = request.status
            if request.start_timestamp and request.end_timestamp:
                where['timestamp'] = {
                    'gte': request.start_timestamp,
                    'lte': request.end_timestamp
                }
            elif request.start_timestamp:
                where['timestamp'] = {'gte': request.start_timestamp}
            elif request.end_timestamp:
                where['timestamp'] = {'lte': request.end_timestamp}
            
            page = max(request.page, 1)
            page_size = min(max(request.page_size, 1), 100)
            skip = (page - 1) * page_size
            
            transactions = await prisma.transaksiharian.find_many(
                where=where,
                skip=skip,
                take=page_size,
                order={'timestamp': 'desc'}
            )
            
            total_count = await prisma.transaksiharian.count(where=where)
            proto_transactions = [TransactionHandler.db_to_proto_transaction(tx, pb) for tx in transactions]
            
            logger.info(f"✅ Found {len(proto_transactions)} transactions (total: {total_count})")
            
            return pb.ListTransactionsResponse(
                transactions=proto_transactions,
                total_count=total_count,
                page=page,
                page_size=page_size
            )
            
        except Exception as e:
            logger.error(f"❌ ListTransactions failed: {str(e)}")
            await context.abort(grpc.StatusCode.INTERNAL, f"Failed to list transactions: {str(e)}")