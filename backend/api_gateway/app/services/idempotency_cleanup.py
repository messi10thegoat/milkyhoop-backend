"""
Pembersih baris kedaluwarsa di `idempotency_keys` (Law 14).

KENAPA ADA: tabel ini bertambah 1 baris per transaksi uang, masing-masing
membawa `result jsonb` (salinan response, ±300-500 byte). `expires_at` hanya
dipakai sebagai FILTER BACA (`WHERE expires_at > NOW()`), jadi baris kedaluwarsa
berhenti melindungi tetapi TETAP MENUMPUK. Tanpa pembersih: ±2 MB/hari pada 50
tenant × 100 transaksi/hari, ~700 MB/tahun sebelum index.

GRACE 7 HARI DI ATAS TTL 24 JAM — DISENGAJA. Kalau ada bug yang membuat baris
dianggap kedaluwarsa terlalu cepat, kita punya jendela seminggu untuk melihatnya
sebelum datanya hilang. Jangan perkecil grace demi menghemat ruang.

BATCHED, BUKAN SATU DELETE BESAR. Setiap transaksi uang menulis ke tabel ini;
DELETE besar mengunci dan bisa memblokir jalur uang yang sedang berjalan. Itu
bukan risiko teoretis untuk tabel dengan pola tulis seperti ini.

TAHAN RESTART: nol state di memori. Seluruh keadaan ada di kolom `expires_at`.
Poller mati/restart kapan pun -> siklus berikutnya melanjutkan dari keadaan DB
apa adanya. DELETE-nya idempoten by nature (baris yang sudah hilang tak dihapus
dua kali).
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

GRACE_DAYS = 7          # di atas TTL 24 jam — lihat catatan modul
BATCH_SIZE = 10_000     # maksimum baris per statement
MAX_BATCHES_PER_TICK = 20  # ceiling: 200k baris/siklus, lalu beri napas ke DB
INTERVAL_SECONDS = 3600


async def cleanup_tick(pool, grace_days: int = GRACE_DAYS,
                       batch_size: int = BATCH_SIZE,
                       max_batches: int = MAX_BATCHES_PER_TICK) -> int:
    """Hapus baris kedaluwarsa secara BERBATCH. Mengembalikan total terhapus."""
    total = 0
    for _ in range(max_batches):
        async with pool.acquire() as conn:
            # ctid subquery + LIMIT = batch kecil, kunci pendek.
            deleted = await conn.execute(
                """
                DELETE FROM idempotency_keys
                 WHERE ctid IN (
                       SELECT ctid FROM idempotency_keys
                        WHERE expires_at < NOW() - make_interval(days => $1)
                        LIMIT $2
                 )
                """,
                grace_days, batch_size,
            )
        # asyncpg mengembalikan "DELETE <n>"
        n = int(deleted.split()[-1]) if deleted and deleted.split()[-1].isdigit() else 0
        total += n
        if n < batch_size:
            break   # habis
    return total


async def idempotency_cleanup_loop(pool, interval: int = INTERVAL_SECONDS):
    """
    Loop pembersih. SELALU mencetak hasil tiap siklus — TERMASUK nol.

    Log nol itu penting: kalau poller mati diam-diam, satu-satunya gejala adalah
    tabel yang membesar tanpa ada yang menyadarinya. Baris log "deleted=0" yang
    berhenti muncul jauh lebih mudah terlihat daripada ketiadaan sinyal.
    """
    # print(), BUKAN logger.info(): konfigurasi logging container MEMBUANG
    # INFO level dari modul (terverifikasi 2026-08-06 — logger.info nol muncul
    # di docker logs, print muncul). Memakai logger di sini berarti syarat
    # "log tiap siklus" gagal DIAM-DIAM — persis silent-fallback yang syarat
    # itu dirancang untuk mencegah.
    print(
        f"[IDEM_CLEANUP] poller started (grace={GRACE_DAYS}d, "
        f"batch={BATCH_SIZE}, interval={interval}s)"
    )
    while True:
        try:
            n = await cleanup_tick(pool)
            print(f"[IDEM_CLEANUP] tick done: deleted={n}")
        except asyncio.CancelledError:
            print("[IDEM_CLEANUP] poller cancelled — berhenti bersih")
            raise
        except Exception as e:
            # JANGAN biarkan satu galat mematikan poller selamanya.
            # (summary_poller_loop tidak punya penjagaan ini — kalau tick-nya
            #  melempar, loop-nya mati diam-diam. Jangan tiru bagian itu.)
            print(f"[IDEM_CLEANUP] tick GAGAL (non-fatal): {e}")
        await asyncio.sleep(interval)
