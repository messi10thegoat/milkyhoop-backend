"""Pemasangan Milkybook ke app gateway (paket aktivasi). main.py: `app.include_router(milkybook_router(), prefix="/api/milkybook")`.

Semua impor berat (DB, Redis, middleware izin, SDK) MALAS: modul ini aman diimpor suite unit.
Urutan aktivasi + mundur: FE docs/asisten-rancangan §11.12 (runbook).
"""
from __future__ import annotations

import logging
from datetime import date
from functools import lru_cache

logger = logging.getLogger(__name__)


class _AuditMalas:
    """AuditPostgres dengan pool diambil saat tulis pertama. Galat audit dicatat, TIDAK memutus percakapan
    (tulis buku tetap dijaga kartu + idempotency BE; audit = jejak, bukan pagar)."""

    def __init__(self):
        self._inti = None

    async def tulis(self, baris) -> None:
        try:
            if self._inti is None:
                from ..services.db_pool import get_db_pool
                from .inti.audit import AuditPostgres
                self._inti = AuditPostgres(await get_db_pool())
            await self._inti.tulis(baris)
        except Exception as e:  # pragma: no cover - jalur produksi
            logger.error("[MILKYBOOK] audit gagal (%s): %s", baris.peristiwa, type(e).__name__)


@lru_cache(maxsize=1)
def _putaran():
    from .inti.konfigurasi import buat_putaran_dari_env
    from .inti.registri import Registri
    from .paket import semua
    return buat_putaran_dari_env(Registri(semua()), _AuditMalas())


@lru_cache(maxsize=1)
def _penyimpanan():
    import redis.asyncio as redis

    from ..config import settings
    from .inti.penyimpanan import PenyimpananSesiRedis
    return PenyimpananSesiRedis(redis.from_url(settings.REDIS_URL, decode_responses=True))


def _app():
    from ..main import app
    return app


def _izin_untuk(user):
    from ..middleware.permission_middleware import izin_rute_pengguna

    async def izin(jalur, metode):
        return await izin_rute_pengguna(user, jalur, metode)
    return izin


async def _flag(tenant_id: str) -> bool:
    from ..services.db_pool import get_db_pool
    from ..services.tenant_features import fitur_aktif
    from .inti.router import FLAG
    async with (await get_db_pool()).acquire() as conn:
        return FLAG in await fitur_aktif(conn, tenant_id)


async def _hari_ini(tenant_id: str) -> date:
    from ..services.db_pool import get_db_pool
    from ..utils.tanggal_tenant import tanggal_dokumen
    async with (await get_db_pool()).acquire() as conn:
        return await tanggal_dokumen(conn, tenant_id)


class _PenyimpananMalas:
    def __getattr__(self, nama):
        return getattr(_penyimpanan(), nama)


def milkybook_router():
    from .inti.router import buat_router
    return buat_router(_putaran, _PenyimpananMalas(), app_fn=_app, izin_fn_untuk=_izin_untuk,
                       flag_fn=_flag, hari_ini_fn=_hari_ini)
