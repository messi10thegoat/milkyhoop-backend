"""#30 (25 Sep 2026): berkas lampiran MILIK dokumen ikut dibersihkan saat draf dihapus.

Dua jenis lampiran:
- Tautan hub (document_attachments) -> dilepas trigger DB V308 di SETIAP jalur hapus-keras.
  Baris `documents` + objeknya tetap (dokumen hub bisa tertaut ke entitas lain).
- Berkas MILIK satu dokumen (tabel lama, objek MinIO hanya milik baris itu):
  sales_invoice_attachments (TANPA FK -> barisnya dulu tertinggal: 3 yatim kaos)
  dan bill_attachments (FK CASCADE -> baris hilang, OBJEK tertinggal).

Urutan kompensasi: kumpulkan path + hapus BARIS di dalam transaksi hapus draf; hapus OBJEK
SESUDAH commit. Kebalikannya (objek dulu) bisa menghapus berkas dokumen yang transaksinya
lalu gagal/rollback -> dokumen hidup dengan lampiran rusak. Objek yang gagal dihapus
dicatat `[LAMPIRAN_YATIM]` (path + konteks) -- yatim objek yang BERSUARA, bukan senyap.
storage.delete_file mengembalikan False saat gagal (tidak melempar) -> False = gagal.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# tabel induk -> (tabel lampiran milik, kolom FK ke induk)
BERKAS_MILIK = {
    "sales_invoices": ("sales_invoice_attachments", "invoice_id"),
    "bills": ("bill_attachments", "bill_id"),
}


async def lepas_berkas_milik(conn, tabel_induk: str, induk_id) -> list[str]:
    """Di DALAM transaksi hapus: hapus baris lampiran milik, kembalikan path objeknya."""
    tabel, kolom = BERKAS_MILIK[tabel_induk]
    rows = await conn.fetch(
        f"DELETE FROM {tabel} WHERE {kolom} = $1 RETURNING file_path",  # nosec B608 - peta tetap
        induk_id,
    )
    return [r["file_path"] for r in rows if r["file_path"]]


async def hapus_objek_sesudah_commit(paths: list[str], konteks: str) -> dict:
    """SESUDAH commit: hapus objek; kegagalan dicatat, tak menggagalkan respons."""
    if not paths:
        return {"dihapus": 0, "gagal": []}
    from .storage_service import get_storage_service

    gagal = []
    try:
        storage = get_storage_service()
    except Exception as e:  # penyimpanan tak tersedia -> semua tercatat yatim
        logger.warning("[LAMPIRAN_YATIM] %s: storage tak tersedia (%s): %s", konteks, e, paths)
        return {"dihapus": 0, "gagal": list(paths)}
    for p in paths:
        try:
            ok = await storage.delete_file(p)
        except Exception as e:
            ok = False
            logger.warning("[LAMPIRAN_YATIM] %s: gagal hapus objek %s (%s)", konteks, p, e)
        if ok is not True:
            gagal.append(p)
            logger.warning("[LAMPIRAN_YATIM] %s: objek tertinggal %s", konteks, p)
    return {"dihapus": len(paths) - len(gagal), "gagal": gagal}
