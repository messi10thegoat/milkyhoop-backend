"""Bentuk dokumen Bank Feed (BF-1e, tambahan FE U1): SATU bentuk untuk tebakan.dokumen, tak-di-bank.dokumen, dan daftar.dokumen_tertaut.
{jenis: kunci stabil yang dipetakan FE ke halaman modul (daftar di KONTRAK-bf1e), jenis_text: label server, sumber: je.source_type mentah, id: ID DOKUMEN modul (je.source_id), nomor, bt_id: baris bank_transactions}.
BE tidak mengirim URL (pola modul lain tidak begitu); FE memetakan `jenis` -> rute dengan tabelnya sendiri. Dokumen tertaut dibaca dari tautan + bank_transactions/jurnal (hanya SELECT, SQL_TERTAUT di sini agar modul daftar bebas jurnal)."""
from . import kasbank_ringkas as KR

KUNCI_SUMBER = {"BILL_PAYMENT": "pembayaran_tagihan", "PAYMENT_BILL": "pembayaran_tagihan", "PAYMENT_MADE": "pembayaran_tagihan",
                "RECEIVE_PAYMENT": "penerimaan_pembayaran", "PAYMENT_RECEIVED": "penerimaan_pembayaran",
                "CUSTOMER_DEPOSIT": "uang_muka_pelanggan", "VENDOR_DEPOSIT": "uang_muka_vendor", "EXPENSE": "biaya",
                "BANK_TRANSFER": "transfer", "BANK_TRANSACTION": "mutasi_manual"}
KUNCI_LAIN = "lainnya"

# dokumen yang tertaut AKTIF ke baris dicatat/dicocokkan (BF-1e): satu bentuk dengan tebakan.dokumen dan tak-di-bank.dokumen; nomor/pihak dari SQL_JOIN_DOKUMEN (satu definisi dengan register bank)
SQL_TERTAUT = f"""SELECT t.mutasi_bank_id, bt.id AS bt_id, je.source_type AS sumber, je.source_id AS dokumen_id, COALESCE(dok.nomor_dokumen, bt.transaction_number, bt.reference_number) AS nomor
FROM mutasi_bank_tautan t
JOIN bank_transactions bt ON bt.id = t.bank_transaction_id AND bt.tenant_id = t.tenant_id
JOIN journal_entries je ON je.id = bt.journal_id AND je.tenant_id = bt.tenant_id
{KR.SQL_JOIN_DOKUMEN}
WHERE t.tenant_id = $1 AND t.mutasi_bank_id = ANY($2::uuid[]) AND t.dilepas_pada IS NULL
ORDER BY t.dibuat_pada, t.id"""


def bentuk(sumber, dokumen_id, nomor, bt_id) -> dict:
    return {"jenis": KUNCI_SUMBER.get(sumber, KUNCI_LAIN), "jenis_text": KR.LABEL_SUMBER.get(sumber, KR.LABEL_LAIN), "sumber": sumber,
            "id": str(dokumen_id) if dokumen_id else None, "nomor": nomor, "bt_id": str(bt_id) if bt_id else None}
