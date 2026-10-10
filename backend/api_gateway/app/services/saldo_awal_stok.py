"""Saldo awal persediaan = JURNAL + KARTU STOK dalam SATU transaksi (audit F1, 10 Okt 2026, MASTER GO).

Latar: POST /api/items (opening_stock) dan POST /api/inventory/products (stok_awal) dulu hanya menulis
`inventory_ledger` OPENING_BALANCE -- tanpa jurnal Dr Persediaan / Cr Modal Saldo Awal. Akibatnya kartu stok
bernilai, buku besar tidak (kaos: 1 baris Rp128.000), lalu pemakaian barang itu menjurnal Cr Persediaan pada biaya
yang masuknya tak pernah didebit. `source_id` baris kartu juga `gen_random_uuid()` -- menunjuk ke ketiadaan.

Ironlaws: Law 1/16 (neraca dari jurnal; kartu stok tak boleh bernilai tanpa jurnal), Law 4 (double-entry, kedua baris
dari satu `nilai`), Law 5 (kunci periode diperiksa SEBELUM menulis, pesan ramah), Law 6 (kartu.source_id = id jurnal;
jurnal.source_id = id produk), Law 13 (advisory lock tenant: penomoran + penolakan ganda), Law 20 (DRAFT -> baris ->
POSTED), Law 23 (WAJIB di transaksi pemanggil -- dijaga `is_in_transaction`), Law 24 (tenant eksplisit di tiap SQL),
Law 27 (akun lewat `products.inventory_account_id`, cadangan ROLE -- tanpa kode akun keras).

Law 28 (guard_opening_balance, V114): jurnal OPENING/OPENING_BALANCE ditolak DB bila usaha sudah punya transaksi
operasional -> untuk usaha berjalan saldo awal stok DITOLAK di sini dengan pesan ramah (putusan pemilik 10 Okt: tolak +
arahkan ke Penyesuaian stok; tanpa mengubah aturan buku).

Nilai nol DITOLAK: stok bernilai 0 membuat HPP 0 saat dijual. Pemanggil diminta mengisi harga pokok per unit."""
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from fastapi import HTTPException

from . import teks_galat as tg
from .role_resolver import AccountRole, AccountRoleUnmappedError, resolve_account_id_by_role

SUMBER = "OPENING_BALANCE"  # sudah terdaftar di journal_source_types (Law 6)


async def _akun(conn, tenant_id: str, akun_produk, role: str, nama: str):
    if akun_produk:
        return akun_produk
    try:
        return await resolve_account_id_by_role(conn, tenant_id, role)
    except AccountRoleUnmappedError:
        raise HTTPException(
            status_code=409,
            detail=f"Akun {nama} belum dipetakan untuk usaha ini, jadi saldo awal stok belum bisa dijurnal. "
                   "Atur di Pengaturan › Akun, atau simpan barang tanpa saldo awal.",
        )


def nilai_saldo_awal(qty, rate) -> Decimal:
    """qty x harga pokok per unit, dibulatkan 2 desimal -- SATU angka untuk jurnal DAN kartu stok."""
    return (Decimal(str(qty or 0)) * Decimal(str(rate or 0))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def periksa_nilai(qty, rate) -> Decimal:
    """Dipanggil SEBELUM produk dibuat (gagal awal, bukan setelah baris produk berdiri). qty<=0 -> 0 (tak ada saldo awal)."""
    if Decimal(str(qty or 0)) <= 0:
        return Decimal("0")
    nilai = nilai_saldo_awal(qty, rate)
    if nilai <= 0:
        raise HTTPException(
            status_code=422,
            detail="Saldo awal butuh harga pokok per unit lebih dari nol, supaya stok bernilai di pembukuan. "
                   "Isi harga pokok saldo awal, atau simpan barang tanpa saldo awal.",
        )
    return nilai


# Predikat yang SAMA dengan trigger guard_opening_balance (V114): ada jurnal POSTED bukan saldo awal / bukan pembalikan saldo awal.
SQL_SUDAH_BERJALAN = """
    SELECT EXISTS (
        SELECT 1 FROM journal_entries je
        WHERE je.tenant_id = $1 AND je.status = 'POSTED'
          AND je.source_type NOT IN ('OPENING', 'OPENING_BALANCE', 'OPENING_BALANCE_REVERSAL')
          AND NOT (je.source_type = 'REVERSAL' AND EXISTS (
                SELECT 1 FROM journal_entries orig
                WHERE orig.id = je.source_id AND orig.tenant_id = je.tenant_id AND orig.source_type IN ('OPENING', 'OPENING_BALANCE')))
    )"""


async def tolak_bila_usaha_sudah_berjalan(conn, tenant_id: str) -> None:
    if await conn.fetchval(SQL_SUDAH_BERJALAN, tenant_id):
        raise HTTPException(
            status_code=422,
            detail="Usaha ini sudah punya transaksi, jadi stok awal tidak bisa diisi saat membuat barang. "
                   "Simpan barang dulu, lalu tambahkan stoknya lewat Penyesuaian stok.",
        )


async def periksa_saldo_awal(conn, tenant_id: str, qty, rate) -> Decimal:
    """Dipanggil SEBELUM produk dibuat: nilai > 0 (murni), lalu usaha belum berjalan (DB). qty<=0 -> tak ada saldo awal."""
    nilai = periksa_nilai(qty, rate)
    if nilai > 0:
        await tolak_bila_usaha_sudah_berjalan(conn, tenant_id)
    return nilai


async def catat_saldo_awal_stok(
    conn, *, tenant_id: str, user_id, product_id, product_code, product_name: str, tanggal: date,
    qty, rate, warehouse_id, inventory_account_id=None, movement_type: str = SUMBER,
    catatan: str = "Saldo awal inventaris",
) -> dict:
    """Jurnal saldo awal + baris kartu stok, di transaksi PEMANGGIL. -> {journal_id, journal_number, nilai}."""
    if not conn.is_in_transaction():
        raise RuntimeError("catat_saldo_awal_stok wajib di dalam transaksi pemanggil (Law 23)")
    nilai = periksa_nilai(qty, rate)
    if nilai <= 0:
        raise ValueError("qty saldo awal harus > 0")
    await tolak_bila_usaha_sudah_berjalan(conn, tenant_id)
    qty_d, rate_d = Decimal(str(qty)), Decimal(str(rate))
    product_id = UUID(str(product_id))

    periode = await conn.fetchrow(
        "SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2",
        tenant_id, tanggal)
    if periode and periode["status"] != "OPEN":
        raise HTTPException(status_code=400, detail=tg.periode_tertutup(None, periode["status"]))

    # Law 13: satu kunci tenant -- menyerialkan penomoran jurnal DAN pemeriksaan saldo awal ganda.
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"OB_STOK:{tenant_id}")
    ganda = await conn.fetchval(
        "SELECT journal_number FROM journal_entries WHERE tenant_id = $1 AND source_type = $2 AND source_id = $3 "
        "AND status = 'POSTED' AND reversed_by_id IS NULL LIMIT 1",
        tenant_id, SUMBER, product_id)
    if ganda:
        raise HTTPException(status_code=409, detail=f"Barang ini sudah punya jurnal saldo awal {ganda}.")

    akun_persediaan = await _akun(conn, tenant_id, inventory_account_id, AccountRole.INVENTORY_MERCHANDISE, "Persediaan")
    akun_ekuitas = await _akun(conn, tenant_id, None, AccountRole.EQUITY_OPENING_BALANCE, "Modal Saldo Awal")

    awalan = f"OB-S-{tanggal.strftime('%y%m%d')}"
    urut = await conn.fetchval(
        "SELECT COUNT(*) FROM journal_entries WHERE tenant_id = $1 AND journal_number LIKE $2",
        tenant_id, f"{awalan}%")
    nomor = f"{awalan}-{str(int(urut) + 1).zfill(3)}"

    jurnal_id = await conn.fetchval(
        """INSERT INTO journal_entries (tenant_id, journal_number, journal_date, description, source_type, source_id,
                                        total_debit, total_credit, status, is_opening_balance, created_by)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $7, 'DRAFT', true, $8) RETURNING id""",
        tenant_id, nomor, tanggal, f"Saldo awal persediaan - {product_name}", SUMBER, product_id, nilai, user_id)
    await conn.execute(
        "INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo) VALUES ($1, 1, $2, $3, 0, $4)",
        jurnal_id, akun_persediaan, nilai, f"Saldo awal persediaan - {product_name}")
    await conn.execute(
        "INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo) VALUES ($1, 2, $2, 0, $3, $4)",
        jurnal_id, akun_ekuitas, nilai, f"Modal saldo awal - {product_name}")
    await conn.execute("UPDATE journal_entries SET status = 'POSTED' WHERE id = $1 AND tenant_id = $2", jurnal_id, tenant_id)

    await conn.execute(
        """INSERT INTO inventory_ledger (
               id, tenant_id, product_id, product_code, product_name, movement_type, movement_date,
               source_type, source_id, source_number, quantity_in, quantity_out, quantity_balance,
               unit_cost, total_cost, average_cost, warehouse_id, notes, created_at
           ) VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 0, $10, $11, $12, $11, $13, $14, NOW())""",
        tenant_id, product_id, product_code, product_name, movement_type, tanggal, SUMBER, jurnal_id,
        f"OB-{product_code}" if product_code else nomor, qty_d, rate_d, nilai, warehouse_id, catatan)
    return {"journal_id": jurnal_id, "journal_number": nomor, "nilai": nilai}
