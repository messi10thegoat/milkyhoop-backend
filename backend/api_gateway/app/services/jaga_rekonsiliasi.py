"""Penjaga void transaksi bank yang SUDAH DIREKONSILIASI (27 Sep 2026; pola Xero/QBO).

Keadaan rekonsiliasi hidup di bank_transactions (routers/bank_reconciliation.py):
  - matched_statement_line_id NOT NULL  = sudah DICOCOKKAN ke baris rekening koran (sesi berjalan; bisa dilepas)
  - is_reconciled = true                = sesi rekonsiliasi SELESAI (tak ada jalur batal-rekonsiliasi)
Baris bank tergantung pada jurnal dokumen (bank_sync Rule 1: journal_id), jurnal dokumen = je.source_id = id dokumen
(terukur 27 Sep: RCV/BILL_PAYMENT(v2)/EXPENSE/BANK_TRANSFER/CUSTOMER_DEPOSIT/BANK_TRANSACTION 100%%).
Void = membalik jurnal itu -> mutasi bank yang sudah cocok dengan rekening koran jadi hantu di buku.

Lapis DB (V320 trg_cegah_void_bank_terekonsiliasi) menjaga SEMUA jalur yang menandai reversed_by_id; fungsi ini
memberi 409 yang bisa dibaca di endpoint void utama SEBELUM tulisan apa pun."""
from fastapi import HTTPException

SQL_KEADAAN = """
    SELECT count(*) FILTER (WHERE COALESCE(bt.is_reconciled, false)) AS rekon,
           count(*) FILTER (WHERE NOT COALESCE(bt.is_reconciled, false)
                             AND bt.matched_statement_line_id IS NOT NULL) AS cocok
    FROM bank_transactions bt
    JOIN journal_entries je ON je.id = bt.journal_id AND je.tenant_id = bt.tenant_id
    WHERE bt.tenant_id = $1 AND je.source_id = $2::uuid AND je.reversed_by_id IS NULL AND je.reversal_of_id IS NULL
"""


async def tolak_void_bila_terekonsiliasi(conn, tenant_id: str, source_id) -> None:
    r = await conn.fetchrow(SQL_KEADAAN, tenant_id, str(source_id))
    if r and r["rekon"]:
        raise HTTPException(status_code=409, detail={
            "code": "BANK_TX_RECONCILED",
            "message": "Transaksi ini sudah direkonsiliasi dengan rekening koran, jadi tidak bisa dibatalkan. "
                       "Catat koreksinya sebagai transaksi baru.",
        })
    if r and r["cocok"]:
        raise HTTPException(status_code=409, detail={
            "code": "BANK_TX_MATCHED",
            "message": "Transaksi ini sudah dicocokkan dengan baris rekening koran di sesi rekonsiliasi yang sedang "
                       "berjalan. Lepaskan pencocokannya dulu di Rekonsiliasi Bank, lalu batalkan.",
        })
