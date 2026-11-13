"""K8 (14 Nov 2026; putusan pemilik: opsi A): lokasi TRANSIT pemindahan dua langkah ala Accurate = gudang SISTEM per tenant (warehouses.is_system, V497).
Dibuat KODE saat Kirim pertama (tak ada backfill/seed). Kartu stok: asal -> Transit -> tujuan; Σ on_hand ledger tak berubah selama transit (penjaga
verify_inventory_wac_reconciliation_all tak memakai gudang), GL tak bergerak (satu akun Persediaan) -> TANPA jurnal."""
from typing import Optional
from uuid import UUID

KODE = "SYS-TRANSIT"
NAMA = "Transit"


async def id_transit(conn, tid: str) -> Optional[UUID]:
    return await conn.fetchval("SELECT id FROM warehouses WHERE tenant_id = $1 AND is_system AND code = $2", tid, KODE)


async def pastikan_transit(conn, tid: str) -> UUID:
    """-> id gudang Transit tenant; dibuat bila belum ada (idempoten, dikunci per tenant). Di dalam transaksi pemanggil."""
    ada = await id_transit(conn, tid)
    if ada:
        return ada
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"GUDANG_TRANSIT:{tid}")
    ada = await id_transit(conn, tid)
    if ada:
        return ada
    return await conn.fetchval(
        "INSERT INTO warehouses (tenant_id, code, name, is_default, is_active, is_branch, is_system) "
        "VALUES ($1, $2, $3, false, true, false, true) RETURNING id", tid, KODE, NAMA)
