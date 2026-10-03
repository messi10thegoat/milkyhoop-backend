"""Kirim dokumen penawaran (Q5, 3 Okt 2026; MASTER via WORKSPACE). SATU penentu untuk POST /documents/quotation/{id}/share
dan pratinjaunya; SATU penanda 'terkirim' untuk share DAN POST /quotes/{id}/send.

  draft                                  -> boleh; tautan + DITANDAI terkirim (status sent, sent_at, audit QUOTE_SENT)
  sent / viewed / accepted / converted   -> boleh; tautan saja, status tetap
  void / declined / expired              -> 409 QUOTE_NOT_SHAREABLE, nol tulis
  kedaluwarsa turunan (penawaran_kedaluwarsa: sent/viewed lewat tanggal) -> 409 yang sama
  draft yang tanggal berlakunya SUDAH lewat -> 409 yang sama (mengirim penawaran yang sudah mati = menyesatkan)
"""
from datetime import date
from typing import Optional

from .penawaran_kedaluwarsa import kedaluwarsa

TAK_BOLEH = {"void": "sudah dibatalkan", "declined": "sudah ditolak pelanggan"}
KODE = "QUOTE_NOT_SHAREABLE"


def _tgl(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def penentu(status: str, nomor: str, expiry_date: Optional[date], hari_ini: date) -> dict:
    """-> {boleh, akan_ditandai_terkirim, code, message}. Murni (tanpa DB)."""
    tolak = None
    if status in TAK_BOLEH:
        tolak = f"Penawaran {nomor} {TAK_BOLEH[status]}; tidak bisa dikirim."
    elif status == "expired" or kedaluwarsa(status, expiry_date, hari_ini):
        tolak = (f"Masa berlaku penawaran {nomor} sudah lewat"
                 + (f" ({_tgl(expiry_date)})" if expiry_date else "") + ". Perpanjang dulu.")
    elif status == "draft" and expiry_date is not None and expiry_date < hari_ini:
        tolak = f"Tanggal berlaku penawaran {nomor} sudah lewat ({_tgl(expiry_date)}). Ubah tanggal berlaku dulu."
    if tolak:
        return {"boleh": False, "akan_ditandai_terkirim": False, "code": KODE, "message": tolak}
    return {"boleh": True, "akan_ditandai_terkirim": status == "draft", "code": None, "message": None}


async def tandai_terkirim(conn, tenant_id: str, quote_id, nomor: str, user_id, source: str) -> None:
    """status='sent', sent_at=NOW() + audit QUOTE_SENT. Pemanggil: kunci QUOTE + FOR UPDATE + transaksi, status sah."""
    from .so_riwayat import catat_riwayat
    await conn.execute("UPDATE quotes SET status = 'sent', sent_at = NOW() WHERE id = $1 AND tenant_id = $2",
                       quote_id, tenant_id)
    await catat_riwayat(conn, tenant_id, "quotes", quote_id, nomor, "QUOTE_SENT", user_id,
                        f"Penawaran {nomor} ditandai terkirim", source=source)
