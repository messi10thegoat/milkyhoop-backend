"""Rekening BAYAR bawaan biaya -- SATU penentu SERVER (BUG-055, putusan MASTER 7 Nov 2026; pola Accurate/QBO/Xero: rekening yang
TERAKHIR dipakai, lalu rekening utama). Dipakai GET /api/expenses/summary (`paid_through_bawaan`, form Tambah Biaya) dan nanti Biaya CW.

Urutan: 'terakhir' = rekening biaya POSTED terbaru tenant yang rekeningnya masih AKTIF (biaya terbaru ber-rekening nonaktif dilewati
ke biaya posted sebelumnya) -> 'utama' = rekening aktif is_default -> 'kas' = rekening aktif bertipe kas (cash/petty_cash) tertua
-> None (pengguna memilih sendiri). Dulu FE memakai rekening PERTAMA daftar abjad -> "BCA Giro Utama" menang atas "Kas Kecil" dan
biaya tunai tercatat keluar dari bank. Baca saja, tanpa angka uang.
"""
from typing import Optional

TIPE_KAS = ("cash", "petty_cash")

SQL_TERAKHIR = """SELECT ba.id, ba.account_name FROM expenses e
    JOIN bank_accounts ba ON ba.id = e.paid_through_id AND ba.tenant_id = e.tenant_id
    WHERE e.tenant_id = $1 AND e.status = 'posted' AND ba.is_active
    ORDER BY e.created_at DESC, e.id DESC LIMIT 1"""
SQL_UTAMA = """SELECT id, account_name FROM bank_accounts WHERE tenant_id = $1 AND is_active AND is_default
    ORDER BY created_at, account_name LIMIT 1"""
SQL_KAS = """SELECT id, account_name FROM bank_accounts WHERE tenant_id = $1 AND is_active AND account_type = ANY($2::text[])
    ORDER BY created_at, account_name LIMIT 1"""


def pilih(terakhir, utama, kas) -> Optional[dict]:
    """Baris pertama yang ada menurut urutan sumber -> {id, nama, sumber}; semuanya kosong -> None."""
    for sumber, r in (("terakhir", terakhir), ("utama", utama), ("kas", kas)):
        if r:
            return {"id": str(r["id"]), "nama": r["account_name"], "sumber": sumber}
    return None


async def paid_through_bawaan(conn, tid: str) -> Optional[dict]:
    terakhir = await conn.fetchrow(SQL_TERAKHIR, tid)
    utama = None if terakhir else await conn.fetchrow(SQL_UTAMA, tid)
    kas = None if terakhir or utama else await conn.fetchrow(SQL_KAS, tid, list(TIPE_KAS))
    return pilih(terakhir, utama, kas)
