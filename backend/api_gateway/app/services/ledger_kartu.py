"""SATU PINTU penulis inventory_ledger (unit 0 FIFO, 25 Nov 2026). Hanya MENULIS baris dengan nilai yang sudah dihitung pemanggil; tak menghitung WAC,
tak membuat jurnal. Modul lain dilarang INSERT inventory_ledger (tests/unit/test_ledger_satu_pintu). Dipanggil di transaksi pemanggil (Law 23)."""
from typing import Optional
from uuid import UUID, uuid4

SQL = """INSERT INTO inventory_ledger (id, tenant_id, product_id, product_code, product_name, movement_type, movement_date, source_type, source_id, source_number, quantity_in, quantity_out, quantity_balance, unit_cost, total_cost, average_cost, warehouse_id, journal_id, created_by, notes, batch_id, storage_location_id, transaction_unit, transaction_quantity, conversion_factor, created_at) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, CASE WHEN $26::boolean THEN clock_timestamp() ELSE now() END)"""


async def tulis_kartu(conn, *, tenant_id, product_id, product_code, product_name, movement_type, movement_date, source_type, source_id, quantity_in, quantity_out,
                      quantity_balance, unit_cost, total_cost, average_cost, warehouse_id, created_by=None, notes=None, source_number=None, journal_id=None, batch_id=None,
                      storage_location_id=None, transaction_unit=None, transaction_quantity=0, conversion_factor=None, id: Optional[UUID] = None,
                      jam_nyata: bool = False, serials: Optional[list] = None, balik_ledger: Optional[UUID] = None) -> UUID:
    """-> id baris. transaction_quantity bawaan 0 = bawaan kolom (None eksplisit tetap NULL). jam_nyata: created_at = clock_timestamp() (urutan di dalam satu transaksi)."""
    id = id or uuid4()
    from . import akses_gudang as AG
    if AG.ada_ctx():  # PARITAS-4b 4b-3: pengaman SATU jalur gerak stok (semua dokumen); penentu tiap dokumen memblok lebih dulu di pratinjau
        await AG.wajib_dok(conn, tenant_id, [warehouse_id])
    await conn.execute(SQL, id, tenant_id, product_id, product_code, product_name, movement_type, movement_date, source_type, source_id, source_number, quantity_in,
                       quantity_out, quantity_balance, unit_cost, total_cost, average_cost, warehouse_id, journal_id, created_by, notes, batch_id,
                       storage_location_id, transaction_unit, transaction_quantity, conversion_factor, jam_nyata)
    # Serial S1 (flag gelap serial_v1): nomor seri ikut baris kartu di transaksi yang sama; barang tanpa track_serial = satu SELECT, tak berubah
    from . import serial_gerak
    await serial_gerak.pada_kartu(conn, tenant_id=tenant_id, product_id=product_id, product_name=product_name, warehouse_id=warehouse_id, ledger_id=id,
                                  quantity_in=quantity_in, quantity_out=quantity_out, serials=serials, balik_ledger=balik_ledger,
                                  ref={"source_type": source_type, "source_id": source_id, "source_number": source_number, "created_by": created_by,
                                       "movement_date": movement_date})
    return id
