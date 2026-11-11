"""Rekening awal tenant BARU (BUG-157, putusan MASTER 12 Nov 2026; pola Accurate: kas tunai bawaan, kas/bank siap sejak awal).

Onboarding menyemai "Kas" (cash, akun CoA 1-10100, DEFAULT) dan "Bank" (bank, 1-10200, bukan default), saldo awal 0 TANPA jurnal
(saldo tetap turunan jurnal, Hukum Besi 1/16). Idempoten (ON CONFLICT DO NOTHING pada unik tenant+akun / tenant+nama / satu default).
Hanya dipanggil dari onboarding: tenant yang sudah ada TIDAK disentuh (tanpa backfill; menulis data tenant nyata = putusan pemilik).
Akun CoA tak ada -> rekening itu dilewati (seperti seed_default_account_roles), bukan menggagalkan pendaftaran. Bukan rekening_bawaan.py
(itu penentu rekening BAYAR bawaan biaya)."""

# (nama, tipe, kode akun CoA, default) -- urutan = urutan sisip
REKENING_AWAL = (("Kas", "cash", "1-10100", True), ("Bank", "bank", "1-10200", False))

SQL_AKUN = "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = $2"
SQL_SISIP = """INSERT INTO bank_accounts (tenant_id, account_name, coa_id, account_type, is_default, opening_balance, current_balance, created_by)
    VALUES ($1, $2, $3, $4, $5, 0, 0, $6::uuid) ON CONFLICT DO NOTHING"""


async def seed_rekening_awal(conn, tenant_id: str, user_id: str) -> int:
    """-> jumlah rekening yang benar-benar dibuat (0..2)."""
    dibuat = 0
    for nama, tipe, kode, utama in REKENING_AWAL:
        coa_id = await conn.fetchval(SQL_AKUN, tenant_id, kode)
        if coa_id is None:
            continue
        status = await conn.execute(SQL_SISIP, tenant_id, nama, coa_id, tipe, utama, user_id)
        dibuat += 1 if str(status).endswith(" 1") else 0
    return dibuat
