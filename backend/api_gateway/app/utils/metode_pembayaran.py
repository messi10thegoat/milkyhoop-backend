"""Metode pembayaran penerimaan (receive_payments.payment_method) — #29, 24 Sep 2026.

Metode = LABEL (kwitansi, PDF, filter daftar). Jurnal TIDAK membacanya: sisi
Dr memakai akun Kas/Bank. Karena itu satu-satunya sumber yang jujur untuk
metode adalah JENIS akun yang menerima uang, bukan tebakan klien.

Dulu jalur "Catat pembayaran" faktur menerima 'transfer' yang di-hardcode FE
dan memetakan semua selain 'cash' ke 'bank_transfer' -> 16/16 RCV grapgrap ke
akun kas tercatat "Transfer Bank".

Kontrak (GO MASTER 24 Sep):
- Nilai sah: cash | bank_transfer | e_wallet (CHECK V301).
- Klien kosong/null -> diturunkan dari akun. Klien mengirim nilai sah -> dihormati.
- Kosakata lama jalur faktur (transfer/check/other) = BUKAN pilihan pengguna -> diturunkan.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

METODE_SAH = ("cash", "bank_transfer", "e_wallet")

# bank_accounts.account_type (CHECK chk_bank_account_type) -> metode.
_METODE_DARI_JENIS = {
    "cash": "cash",
    "petty_cash": "cash",
    "bank": "bank_transfer",
    "e_wallet": "e_wallet",
    "credit_card": "bank_transfer",
}

LABEL_METODE = {
    "cash": "Tunai",
    "bank_transfer": "Transfer Bank",
    "e_wallet": "E-Wallet",
}


def metode_dari_jenis_akun(account_type: Optional[str]) -> str:
    """Jenis akun Kas/Bank -> metode. Tak dikenal / None (akun CoA tanpa
    baris bank_accounts) -> bank_transfer."""
    return _METODE_DARI_JENIS.get((account_type or "").strip().lower(), "bank_transfer")


def label_metode(metode: Optional[str]) -> str:
    """Label Indonesia untuk kwitansi/PDF. Nilai di luar kontrak (data lama)
    tetap diberi label transfer seperti perilaku sebelumnya."""
    return LABEL_METODE.get((metode or "").strip().lower(), "Transfer Bank")


def metode_klien_sah(metode: Optional[str]) -> Optional[str]:
    """Nilai klien yang DIHORMATI sebagai override; selain itu None (= turunkan)."""
    m = (metode or "").strip().lower()
    return m if m in METODE_SAH else None


async def jenis_akun_kas_bank(conn, tenant_id, akun_id) -> Optional[str]:
    """account_type bank_accounts untuk id yang bisa berupa bank_accounts.id
    ATAU CoA (bank_accounts.coa_id). None bila tak ada baris bank_accounts."""
    if not akun_id:
        return None
    try:
        aid = akun_id if isinstance(akun_id, UUID) else UUID(str(akun_id))
    except (ValueError, TypeError):
        return None
    return await conn.fetchval(
        """
        SELECT account_type FROM bank_accounts
        WHERE tenant_id = $1 AND (id = $2 OR coa_id = $2)
        ORDER BY (id = $2) DESC
        LIMIT 1
        """,
        tenant_id,
        aid,
    )


async def tentukan_metode(conn, tenant_id, akun_id, metode_klien: Optional[str]) -> str:
    """Override sah klien, atau turunan dari jenis akun."""
    sah = metode_klien_sah(metode_klien)
    if sah:
        return sah
    return metode_dari_jenis_akun(await jenis_akun_kas_bank(conn, tenant_id, akun_id))
