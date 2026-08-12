"""Tanggal dokumen menurut zona waktu tenant.

K0 2026-08-12: seluruh lapisan berjalan di UTC (host, container, postgres).
Pemilik UMKM yang membereskan pembukuan selepas toko tutup — 00.00-08.00
WITA, 00.00-07.00 WIB — mendapat dokumen bertanggal MUNDUR SATU HARI.
Jam kerja, bukan kasus tepi.

Modul ini satu-satunya sumber "hari ini" untuk TANGGAL DOKUMEN.

BUKAN untuk waktu sistem. created_at, hash chain, kedaluwarsa
token/sesi/pending_actions, telemetri, nama berkas: semuanya TETAP UTC.
Menggeser itu menyentuh Law 2 dan rantai hash.

tenant_id dioper EKSPLISIT dan itu keputusan, bukan kekakuan. Corong
ambient yang ada (rls_context.set_tenant_context) tak pernah dipanggil di
produksi, jadi ContextVar-nya selalu None — helper yang bersandar padanya
akan diam-diam memakai zona cadangan untuk SETIAP tenant.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ZONA_CADANGAN = "Asia/Jakarta"

# Zona tenant jarang berubah, tapi cache tanpa kedaluwarsa berarti mengubahnya
# di profil tak berpengaruh sampai container di-restart — persis kelas
# "jaring yang lapuk diam-diam". TTL pendek menutupnya dengan harga nol.
_TTL_DETIK = 300
_cache: dict[str, tuple[float, ZoneInfo]] = {}


def _zona_dari_nama(nama: str | None, tenant_id: str) -> ZoneInfo:
    """Nama zona -> ZoneInfo, dengan cadangan yang BERSUARA."""
    if not nama or not str(nama).strip():
        logger.warning(
            "[K0_ZONA] tenant %s tak punya timezone, memakai cadangan %s",
            tenant_id,
            ZONA_CADANGAN,
        )
        return ZoneInfo(ZONA_CADANGAN)
    try:
        return ZoneInfo(str(nama).strip())
    except Exception:
        logger.warning(
            "[K0_ZONA] tenant %s bertimezone tak dikenal %r, memakai cadangan %s",
            tenant_id,
            nama,
            ZONA_CADANGAN,
        )
        return ZoneInfo(ZONA_CADANGAN)


def tanggal_pada(instant: datetime, zona: ZoneInfo) -> date:
    """Inti perhitungan, murni dan dapat diuji dengan instant yang ditentukan.

    Dipisah supaya gate bisa mensimulasikan jam rawan pada KODE YANG
    SESUNGGUHNYA dipakai produksi, bukan pada tiruan konsepnya. Assertion
    yang tak bisa dibuat gagal tidak membuktikan apa pun.
    """
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(zona).date()


async def zona_tenant(conn, tenant_id: str) -> ZoneInfo:
    import time

    _sekarang = time.monotonic()
    _hit = _cache.get(tenant_id)
    if _hit and _sekarang - _hit[0] < _TTL_DETIK:
        return _hit[1]

    nama = None
    try:
        nama = await conn.fetchval(
            'SELECT timezone FROM "Tenant" WHERE id = $1', tenant_id
        )
    except Exception as e:
        logger.warning(
            "[K0_ZONA] gagal membaca timezone tenant %s (%s), memakai cadangan %s",
            tenant_id,
            e,
            ZONA_CADANGAN,
        )

    zona = _zona_dari_nama(nama, tenant_id)
    _cache[tenant_id] = (_sekarang, zona)
    return zona


async def tanggal_dokumen(conn, tenant_id: str) -> date:
    """Hari ini menurut zona tenant. Untuk TANGGAL DOKUMEN saja."""
    return tanggal_pada(datetime.now(timezone.utc), await zona_tenant(conn, tenant_id))


async def tanggal_dokumen_iso(conn, tenant_id: str) -> str:
    return (await tanggal_dokumen(conn, tenant_id)).isoformat()
