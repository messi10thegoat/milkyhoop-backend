"""Bentuk dokumen Bank Feed (BF-1e, tambahan FE U1): SATU bentuk untuk tebakan.dokumen, tak-di-bank.dokumen, dan daftar.dokumen_tertaut.
{jenis: kunci stabil yang dipetakan FE ke halaman modul (daftar di KONTRAK-bf1e), jenis_text: label server, sumber: je.source_type mentah, id: ID DOKUMEN modul (je.source_id), nomor, bt_id: baris bank_transactions}.
BE tidak mengirim URL (pola modul lain tidak begitu); FE memetakan `jenis` -> rute dengan tabelnya sendiri. Tanpa jurnal."""
from . import kasbank_ringkas as KR

KUNCI_SUMBER = {"BILL_PAYMENT": "pembayaran_tagihan", "PAYMENT_BILL": "pembayaran_tagihan", "PAYMENT_MADE": "pembayaran_tagihan",
                "RECEIVE_PAYMENT": "penerimaan_pembayaran", "PAYMENT_RECEIVED": "penerimaan_pembayaran",
                "CUSTOMER_DEPOSIT": "uang_muka_pelanggan", "VENDOR_DEPOSIT": "uang_muka_vendor", "EXPENSE": "biaya",
                "BANK_TRANSFER": "transfer", "BANK_TRANSACTION": "mutasi_manual"}
KUNCI_LAIN = "lainnya"


def bentuk(sumber, dokumen_id, nomor, bt_id) -> dict:
    return {"jenis": KUNCI_SUMBER.get(sumber, KUNCI_LAIN), "jenis_text": KR.LABEL_SUMBER.get(sumber, KR.LABEL_LAIN), "sumber": sumber,
            "id": str(dokumen_id) if dokumen_id else None, "nomor": nomor, "bt_id": str(bt_id) if bt_id else None}
