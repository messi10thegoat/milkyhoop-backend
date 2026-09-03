"""
Database utilities for session management.

Lightweight asyncpg pool management for 4-layer memory tables.
Separate from Prisma since chat_* tables are managed via SQL directly.
"""
import asyncpg
import os
import uuid as _uuid
from typing import Optional


class IdSesiTidakSah(ValueError):
    """Id sesi yang dikirim klien bukan uuid dan bukan string kosong."""


def _berbentuk_uuid(teks: str) -> bool:
    try:
        _uuid.UUID(teks)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def validasi_id_sesi(nilai, nama_field: str = "session_id"):
    """Terima id sesi dari LUAR, atau tolak dengan sebab yang bisa dibaca.

    KENAPA DI TEPI, BUKAN MENERJEMAHKAN GALAT DRIVER
    Sebelum ini, id cacat menempuh seluruh jalur sampai
    `session_manager.get_or_create_session` menyisipkannya ke `chat_sessions`
    (yang `id`-nya bertipe `uuid`). asyncpg melempar DataError dan permintaan
    berakhir **500** -- klien diberi tahu "server kami rusak" padahal
    PERMINTAANNYA yang salah, dan siapa pun yang mendiagnosis nanti harus
    menebak sebabnya dari galat driver. Menangkap DataError lalu
    menerjemahkannya akan MENGAWETKAN tebakan itu; validasi di tepi
    menghapusnya.

    Aturan (sengaja mempertahankan perilaku yang SUDAH ADA):
      None          -> None
      "" (kosong)   -> "" ; server membangkitkan id baru. Itu perilaku hari ini
                       (HTTP 200) dan BUKAN kesalahan klien, jadi tidak boleh
                       berubah jadi 422 -- itu akan memutus klien yang sedang
                       memulai percakapan baru.
      uuid apa pun  -> bentuk kanonik huruf kecil (kapital maupun tanpa tanda
                       hubung ikut dibakukan; lihat bakukan_session_id)
      selain itu    -> IdSesiTidakSah -> 422 di tepi
    """
    if nilai is None:
        return None
    teks = str(nilai)
    if teks == "":
        return teks
    if not _berbentuk_uuid(teks):
        raise IdSesiTidakSah(
            f"{nama_field} harus berupa UUID (atau dikosongkan untuk memulai "
            f"percakapan baru); diterima: {teks[:40]!r}"
        )
    return bakukan_session_id(teks)


def bakukan_session_id(nilai):
    """Bakukan id sesi chat yang disimpan sebagai TEKS ke bentuk kanonik.

    KENAPA FUNGSI INI ADA
    `chat_sessions.id` bertipe `uuid`, jadi Postgres SELALU menyimpannya huruf
    kecil. Tapi `pending_actions.conversation_id` dan
    `chat_workflow_state.chat_session_id` bertipe TEXT, jadi mereka menyimpan
    apa adanya yang dikirim klien. Klien yang membangkitkan UUID huruf KAPITAL
    (mis. `UUID().uuidString` di Swift) membuat `WHERE conversation_id = $1`
    MELESET tanpa galat: 111 baris pada 29-30 Agt 2026 terbaca sebagai yatim
    padahal induknya hidup.

    ⚠️ WAJIB DIPAKAI DI DUA UJUNG — TULIS **DAN** BACA.
    Kalau hanya sisi TULIS yang dibakukan, klien yang mengirim kapital akan
    menulis huruf kecil lalu MENCARI dengan huruf kapital dan tidak menemukan
    apa pun. Hari ini klien itu konsisten-salah (kapital di kedua ujung)
    sehingga tetap berfungsi; membakukan satu sisi saja justru MEMATIKAN aksi
    tertunda baginya. Setengah tambalan lebih buruk daripada tak menambal.

    Nilai yang BUKAN uuid dikembalikan apa adanya (mis. sentinel "unknown" di
    tool_executor). Terukur 3 Sep 2026: sentinel itu nol baris di seluruh
    riwayat, tapi jalurnya masih ada, jadi fungsi ini tidak boleh menolaknya.
    """
    if nilai is None:
        return nilai
    teks = str(nilai)
    try:
        return str(_uuid.UUID(teks))
    except (ValueError, AttributeError, TypeError):
        return teks


_session_db_pool: Optional[asyncpg.Pool] = None


async def get_session_db_pool() -> asyncpg.Pool:
    """Get or create database pool for session tables.

    Singleton pattern - creates pool once, reuses across requests.
    """
    global _session_db_pool

    if _session_db_pool is None:
        # Read from environment
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            # Build from components
            db_host = os.getenv("DB_HOST", "postgres")
            db_port = os.getenv("DB_PORT", "5432")
            db_user = os.getenv("DB_USER", "postgres")
            db_password = os.getenv("DB_PASSWORD", "")
            db_name = os.getenv("DB_NAME", "milkydb")
            database_url = (
                f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
            )

        _session_db_pool = await asyncpg.create_pool(
            database_url,
            min_size=2,
            max_size=5,
            command_timeout=15,
            max_inactive_connection_lifetime=300,
        )

    return _session_db_pool


async def close_session_db_pool():
    """Close database pool on shutdown."""
    global _session_db_pool
    if _session_db_pool:
        await _session_db_pool.close()
        _session_db_pool = None
