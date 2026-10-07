"""Pagar akun peran CUSTOMER_DEPOSIT_LIABILITY (Uang Muka Pelanggan) dari jalur MANUAL — 5 Okt 2026 (MASTER).

Saldo uang muka pelanggan = journal-derived per dokumen uang muka (customer_deposits; Law 1/16). Baris manual di akun
itu menggeser saldo GL tanpa dokumen -> sisa uang muka per pelanggan, plafon proforma, dan Check rekonsiliasi berbeda
pendapat. Pola SAMA dengan pagar AR/AP (Law 29 / Law 31 Gate 4): tolak dengan pesan Indonesia + arahkan ke modul.

Hanya jalur yang AKUNNYA DIPILIH PENGGUNA memanggil ini (jurnal manual buat/posting/balik, Uang Masuk/Keluar bank).
Jurnal sistem (uang muka terima/terapkan/refund/lepas, NK, unapply penerimaan) tidak melewatinya.
Diukur 5 Okt: 0 baris MANUAL / bank manual / draf manual di akun ini di semua tenant.

7 Okt 2026 (putusan pemilik LANGSUNG "ikut rekomendasimu" = TOLAK): SALDO AWAL juga dipagar -- modul Saldo Awal
(routers/opening_balance: validasi/buat/ubah) dan rekening bank yang ditautkan ke akun itu (kartu kredit = LIABILITY
bebas dipilih -> saldo awal Cr Uang Muka, lalu tiap transaksi bank menulisnya). Saldo awal uang muka per pelanggan =
modul Uang Muka (antre, saat onboarding tenant baru). Diukur 7 Okt: 0 baris saldo awal / 0 snapshot / 0 rekening
bank di akun ini di semua tenant.
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


KODE_SALDO_AWAL = "SALDO_AWAL_UANG_MUKA_DITOLAK"
KODE_REKENING_BANK = "REKENING_BANK_UANG_MUKA_DITOLAK"


def _pesan_saldo_awal(rows) -> str:
    return (f"Saldo awal tidak bisa dicatat ke akun Uang Muka Pelanggan ({_nama(rows)}) karena tidak terhubung ke "
            "pelanggan mana pun. Kosongkan baris akun ini; uang muka awal dicatat per pelanggan di modul Uang Muka Pelanggan.")


async def akun_uang_muka_dari_kode(conn, tenant_id: str, account_codes) -> list:
    """Sama dengan akun_uang_muka_tersentuh, tetapi masukannya KODE akun (modul Saldo Awal memakai account_code)."""
    kode = sorted({k for k in account_codes if k})
    if not kode:
        return []
    return list(await conn.fetch(
        """SELECT coa.account_code, coa.name FROM account_roles ar
           JOIN chart_of_accounts coa ON coa.id = ar.account_id AND coa.tenant_id = ar.tenant_id
           WHERE ar.tenant_id = $1 AND ar.role_key = $2 AND coa.account_code = ANY($3::text[])""",
        tenant_id, PERAN, kode))


def kode_bernilai(lines) -> list:
    """Kode akun baris saldo awal yang BENAR-BENAR diposting (debit/kredit > 0); baris nol tak menulis jurnal."""
    return [l.account_code for l in lines if (l.debit or 0) > 0 or (l.credit or 0) > 0]


async def galat_saldo_awal(conn, tenant_id: str, lines):
    """Pesan galat (str) bila baris saldo awal bernilai menyentuh akun Uang Muka; None bila aman. Untuk /validate."""
    rows = await akun_uang_muka_dari_kode(conn, tenant_id, kode_bernilai(lines))
    return _pesan_saldo_awal(rows) if rows else None


async def tolak_saldo_awal(conn, tenant_id: str, lines) -> None:
    pesan = await galat_saldo_awal(conn, tenant_id, lines)
    if pesan:
        raise HTTPException(status_code=400, detail={"code": KODE_SALDO_AWAL, "message": pesan})


async def tolak_rekening_bank(conn, tenant_id: str, coa_id) -> None:
    rows = await akun_uang_muka_tersentuh(conn, tenant_id, [coa_id])
    if rows:
        raise HTTPException(status_code=400, detail={"code": KODE_REKENING_BANK, "message": (
            f"Akun Uang Muka Pelanggan ({_nama(rows)}) tidak bisa dijadikan rekening bank atau kartu kredit. "
            "Pilih akun kas, bank, atau kartu kredit; uang muka dicatat lewat modul Uang Muka Pelanggan.")})
