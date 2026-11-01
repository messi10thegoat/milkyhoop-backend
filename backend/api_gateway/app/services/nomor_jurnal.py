"""Nomor jurnal "satu ruang nama" (ND-JURNAL-NOMOR, 1 Nov 2026; preseden UMJ C2 + LPS L2-C1 23 Sep).

Jurnal sebuah dokumen bernomor SAMA dengan dokumennya (pola NetSuite: dampak GL tampil di transaksi
sumber dengan nomornya sendiri). Dulu ND/NK/PK memakai urutan jurnal ber-awalan dokumen ('VC'/'CN'/'BP')
dengan penghitung TERPISAH dari penghitung dokumen -> jurnal bernomor = nomor dokumen LAIN (kaos: ND 3,
NK 16). Bila nomor dokumen itu SUDAH dipakai jurnal lain (baris lama dari urutan lama), pakai seri
cadangan (NDJ/NKJ/PKJ) -- dicek dulu, bukan ditangkap (pelanggaran uq_je_tenant_number membatalkan
transaksi). Jurnal lama tidak diubah (Law 2).

PREMIS RAS (pra-cek lalu INSERT tanpa kunci tenant-lebar): tak ada penulis LAIN yang menerbitkan nomor
jurnal ber-awalan dokumen (nomor dokumen unik per tenant dari penghitungnya sendiri). Diuji
tests/unit/test_nomor_jurnal_satu_ruang.py pada tiap merge: awalan dokumen terlarang di
get_next_journal_number kecuali daftar izin eksplisit; pemanggil pembungkus ini wajib awalan cadangan literal.
"""


async def nomor_jurnal_dokumen(conn, tenant_id: str, nomor_dokumen: str, cadangan: str) -> str:
    """-> nomor_dokumen bila belum dipakai jurnal mana pun di tenant ini; selain itu nomor seri `cadangan`."""
    if nomor_dokumen and not await conn.fetchval(
        "SELECT 1 FROM journal_entries WHERE tenant_id = $1 AND journal_number = $2",
        tenant_id, nomor_dokumen,
    ):
        return nomor_dokumen
    return (
        await conn.fetchval("SELECT get_next_journal_number($1, $2)", tenant_id, cadangan)
        or f"{cadangan}-{nomor_dokumen}"
    )
