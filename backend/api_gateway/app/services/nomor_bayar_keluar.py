"""Nomor Pembayaran Keluar -- SATU seri BP-YYMM-NNNN untuk kedua rute (putusan MASTER 4 Nov 2026, syarat U-A).

Dulu dua generator: POST /bills/{id}/payments = get_next_journal_number(tenant, 'BP') -> BP-2611-0001 (seri yang dipakai PDF/daftar
grapgrap), POST /bill-payments = bill_payment_sequences -> PAY-202611-0001. Memindah pembayaran ke /bill-payments akan mengganti seri
dokumen di tengah jalan. Kini keduanya + perkiraan pratinjau memakai penghitung YANG SAMA (fungsi DB penomoran jurnal, awalan BP,
self-heal dari journal_entries) dan MELOMPATI nomor yang sudah dipakai pembayaran mana pun di tenant (pola V475). Nomor PAY- lama
dibiarkan (data lama tak diubah); bill_payment_sequences tak ditulis lagi.
"""
from fastapi import HTTPException

AWALAN = "BP"


async def nomor_pk(conn, tid: str) -> str:
    """Terbitkan nomor berikutnya (penulis; di transaksi pemanggil). Lompati nomor yang sudah dipakai bill_payments_v2 tenant ini."""
    for _ in range(1000):
        n = await conn.fetchval("SELECT get_next_journal_number($1, $2)", tid, AWALAN)
        if not n:
            break
        if not await conn.fetchval("SELECT 1 FROM bill_payments_v2 WHERE tenant_id = $1 AND payment_number = $2", tid, n):
            return n
    raise HTTPException(status_code=500, detail="Nomor pembayaran tidak bisa dibuat. Coba lagi.")


async def perkiraan_nomor_pk(conn, tid: str) -> str:
    """Pratinjau (baca saja): nomor yang AKAN diterbitkan penghitung yang sama (perkiraan; penulis paralel bisa mendahului)."""
    from .nomor_jurnal_perkiraan import perkiraan_nomor_jurnal
    return await perkiraan_nomor_jurnal(conn, tid, AWALAN)
