"""Pagar akun peran CUSTOMER_DEPOSIT_LIABILITY (Uang Muka Pelanggan) dari jalur MANUAL — 5 Okt 2026 (MASTER).

Saldo uang muka pelanggan = journal-derived per dokumen uang muka (customer_deposits; Law 1/16). Baris manual di akun
itu menggeser saldo GL tanpa dokumen -> sisa uang muka per pelanggan, plafon proforma, dan Check rekonsiliasi berbeda
pendapat. Pola SAMA dengan pagar AR/AP (Law 29 / Law 31 Gate 4): tolak dengan pesan Indonesia + arahkan ke modul.

Hanya jalur yang AKUNNYA DIPILIH PENGGUNA memanggil ini (jurnal manual buat/posting/balik, Uang Masuk/Keluar bank).
Jurnal sistem (uang muka terima/terapkan/refund/lepas, NK, unapply penerimaan) tidak melewatinya.
Diukur 5 Okt: 0 baris MANUAL / bank manual / draf manual di akun ini di semua tenant.
"""
from fastapi import HTTPException

PERAN = "CUSTOMER_DEPOSIT_LIABILITY"


async def akun_uang_muka_tersentuh(conn, tenant_id: str, account_ids) -> list:
    """Akun (kode, nama) di antara account_ids yang dipetakan ke peran Uang Muka Pelanggan tenant ini. Peran tak
    dipetakan -> [] (tak ada yang bisa disentuh). Filter tenant eksplisit di kedua tabel."""
    ids = [a for a in account_ids if a is not None]
    if not ids:
        return []
    return list(await conn.fetch(
        """SELECT coa.account_code, coa.name FROM account_roles ar
           JOIN chart_of_accounts coa ON coa.id = ar.account_id AND coa.tenant_id = ar.tenant_id
           WHERE ar.tenant_id = $1 AND ar.role_key = $2 AND ar.account_id = ANY($3::uuid[])""",
        tenant_id, PERAN, ids))


def _nama(rows) -> str:
    return ", ".join(f"{r['account_code']} {r['name']}" for r in rows)


async def tolak_jurnal_manual(conn, tenant_id: str, account_ids) -> None:
    rows = await akun_uang_muka_tersentuh(conn, tenant_id, account_ids)
    if rows:
        raise HTTPException(status_code=400, detail=(
            f"Jurnal manual tidak boleh menyentuh akun Uang Muka Pelanggan ({_nama(rows)}). "
            "Catat lewat modul Uang Muka Pelanggan: terima, terapkan ke faktur, atau refund."))


async def tolak_akun_lawan_bank(conn, tenant_id: str, account_id) -> None:
    rows = await akun_uang_muka_tersentuh(conn, tenant_id, [account_id])
    if rows:
        raise HTTPException(status_code=400, detail=(
            f"Akun lawan Uang Muka Pelanggan ({_nama(rows)}) tidak bisa dipakai di Uang Masuk/Keluar. "
            "Terima uang muka atau refund lewat modul Uang Muka Pelanggan."))
