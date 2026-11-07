"""Aksi BIAYA standar SO tahap 2 (kontrak MASTER 7 Nov 2026): aksi_tersedia SATU bentuk (schemas/aksi_tersedia) + alasan dari
penentu yang SAMA dengan jalur tulis POST /{id}/post dan /{id}/void (status · jurnal · periode Law 5 · rekonsiliasi bank).
Urutan kunci = urutan tampil: posting, ubah, hapus, batalkan, tambah_bukti. Label = resep FE."""
from typing import Optional

from fastapi import HTTPException

from ..schemas.aksi_tersedia import aksi


def _pesan(e: HTTPException) -> str:
    d = e.detail
    return (d.get("message") or str(d)) if isinstance(d, dict) else str(d)


async def alasan_posting(conn, tid: str, exp) -> Optional[str]:
    """None = boleh diterbitkan. Urutan == post_expense (sesudah kunci): status draf · belum berjurnal · periode tanggal biaya."""
    if exp["status"] != "draft":
        return "Hanya biaya draf yang bisa diterbitkan."
    if exp["journal_id"]:
        return "Biaya ini sudah berjurnal."
    from ..routers.expenses import check_period_is_open
    try:
        await check_period_is_open(conn, tid, exp["expense_date"])
    except HTTPException as e:
        return _pesan(e)
    return None


async def alasan_batal(conn, tid: str, exp) -> Optional[str]:
    """None = boleh dibatalkan. Urutan == void_expense: rekonsiliasi bank · belum dibatalkan · terbit (draf dihapus) · periode tanggal
    biaya · periode hari ini (jurnal pembalik)."""
    from ..routers.expenses import check_period_is_open
    from ..services.jaga_rekonsiliasi import tolak_void_bila_terekonsiliasi
    from ..utils.tanggal_tenant import tanggal_dokumen
    try:
        await tolak_void_bila_terekonsiliasi(conn, tid, exp["id"])
    except HTTPException as e:
        return _pesan(e)
    if exp["status"] == "void":
        return "Biaya sudah dibatalkan."
    if exp["status"] != "posted":
        return "Draf dihapus, bukan dibatalkan."
    try:
        await check_period_is_open(conn, tid, exp["expense_date"])
        await check_period_is_open(conn, tid, await tanggal_dokumen(conn, tid))
    except HTTPException as e:
        return _pesan(e)
    return None


async def aksi_tersedia_biaya(conn, tid: str, exp) -> dict:
    draf = exp["status"] == "draft"
    a_post, a_batal = await alasan_posting(conn, tid, exp), await alasan_batal(conn, tid, exp)
    return {
        "posting": aksi(a_post is None, a_post),
        "ubah": aksi(draf, "Biaya yang sudah terbit tidak bisa diubah; batalkan lalu buat baru."),
        "hapus": aksi(draf, "Biaya yang sudah terbit tidak bisa dihapus; batalkan saja."),
        "batalkan": aksi(a_batal is None, a_batal),
        "tambah_bukti": aksi(exp["status"] != "void", "Biaya yang dibatalkan tidak bisa diberi bukti."),
    }
