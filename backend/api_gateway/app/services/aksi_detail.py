"""aksi_tersedia DETAIL modul jual/beli (gelombang 3, kontrak backend/docs/KONTRAK-jualbeli-aksi-standar-so-20261109.md §2/§5/§6).

Satu helper bersama: setiap kunci aksi dinilai oleh PENENTU tulis aksi itu sendiri (penentu baca yang SUDAH dipakai pratinjau, atau
penjaga masuk yang diekstrak dari penulis dan dipanggil penulis juga) -- tanpa salinan aturan. Semua kunci satu permintaan dinilai di
SATU transaksi READ ONLY (putusan MASTER 9 Nov): penentu yang ternyata menulis gagal keras (galat Postgres read-only), tidak diam.
Penentu penulis+rollback (savepoint yang menulis lalu dibatalkan) DILARANG di sini.

Penilai = `async (conn) -> alasan | None`: None = tersedia; teks = alasan server (kalimat penentu itu, apa adanya).
Isian wajib (alasan batal/tutup, nominal, rekening, gudang, tanggal) BUKAN "tak tersedia": penilai hanya memetik blok KEADAAN
(daftar kode eksplisit per aksi), blok isian diabaikan -- isian divalidasi saat pratinjau sungguhan."""
import inspect
from typing import Awaitable, Callable, Iterable, List, Optional, Tuple

from fastapi import HTTPException

from ..schemas.aksi_tersedia import aksi

Penilai = Callable[..., Awaitable[Optional[str]]]


def pesan_galat(e: HTTPException) -> str:
    """Kalimat galat penulis: detail teks apa adanya, atau detail["message"] (bentuk {code, message})."""
    d = e.detail
    if isinstance(d, dict):
        return str(d.get("message") or d.get("detail") or d.get("code") or "")
    return str(d)


def alasan_blok(blocks: Iterable[dict], kode_keadaan: Optional[Iterable[str]]) -> Optional[str]:
    """Blok KEADAAN pertama (urutan penentu) -> kalimatnya; blok isian (kode di luar daftar) diabaikan. kode_keadaan None = semua blok
    keadaan (penentu tanpa isian, mis. hapus draf)."""
    kode = None if kode_keadaan is None else set(kode_keadaan)
    for b in blocks:
        if kode is None or b.get("code") in kode:
            return b.get("message") or pesan_galat(HTTPException(400, b.get("detail") or b.get("code")))
    return None


async def alasan_galat(fn: Callable[[], object]) -> Optional[str]:
    """Penjaga yang me-raise HTTPException (penjaga masuk penulis, sinkron atau async) -> kalimat galatnya; lolos -> None."""
    try:
        r = fn()
        if inspect.isawaitable(r):
            await r
    except HTTPException as e:
        return pesan_galat(e)
    return None


async def nilai(conn, penilai: List[Tuple[str, Penilai]]) -> dict:
    """{kunci: {tersedia, alasan}} urut = urutan tampil; SATU transaksi READ ONLY untuk semua kunci (pemanggil TIDAK di dalam
    transaksi -- savepoint tak bisa READ ONLY)."""
    hasil = {}
    async with conn.transaction(readonly=True):
        for kunci, fn in penilai:
            alasan = await fn(conn)
            hasil[kunci] = aksi(alasan is None, alasan)
    return hasil
