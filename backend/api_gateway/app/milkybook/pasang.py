"""Pemasangan Milkybook ke app gateway (paket aktivasi). main.py: `app.include_router(milkybook_router(), prefix="/api/milkybook")`.

Semua impor berat (DB, Redis, middleware izin, SDK) MALAS: modul ini aman diimpor suite unit.
Urutan aktivasi + mundur: FE docs/asisten-rancangan §11.12 (runbook).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
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

    async def biaya_bulan_ini(self, tenant_id: str):
        """#11: galat baca -> None = pagar TIDAK memblok (fail-open, dicatat); batas keras tetap batas belanja Console."""
        try:
            if self._inti is None:
                from ..services.db_pool import get_db_pool
                from .inti.audit import AuditPostgres
                self._inti = AuditPostgres(await get_db_pool())
            return await self._inti.biaya_bulan_ini(tenant_id)
        except Exception as e:  # pragma: no cover - jalur produksi
            logger.error("[MILKYBOOK] baca biaya gagal: %s", type(e).__name__)
            return None


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


async def _flag_v2(tenant_id: str) -> bool:
    from ..services.db_pool import get_db_pool
    from ..services.tenant_features import fitur_aktif
    from .v2.router import FLAG
    async with (await get_db_pool()).acquire() as conn:
        return FLAG in await fitur_aktif(conn, tenant_id)


async def _izin_v2(user, jalur, metode):
    from ..middleware.permission_middleware import izin_rute_pengguna
    return await izin_rute_pengguna(user, jalur, metode)


@asynccontextmanager
async def _koneksi():
    from ..services.db_pool import get_db_pool
    async with (await get_db_pool()).acquire() as conn:
        yield conn


@lru_cache(maxsize=1)
def _pemanggil_v2():
    """Lapisan LLM v2 (P5): HANYA MILKYBOOK_ANTHROPIC_API_KEY (kunci terpisah ber-batas belanja). Tanpa kunci = None
    (tafsir menjawab tanya balik), TIDAK pernah jatuh ke ANTHROPIC_API_KEY lingkungan (aturan pemilik)."""
    import os

    from .inti.model import MODEL_BAWAAN, PemanggilAnthropic
    kunci = os.environ.get("MILKYBOOK_ANTHROPIC_API_KEY") or None
    if not kunci:
        return None
    return PemanggilAnthropic(model=os.environ.get("MILKYBOOK_MODEL", MODEL_BAWAAN), api_key=kunci, max_tokens=4000,
                              ttl_prefiks=None)


def _tafsir_v2():
    """Dibaca SETIAP panggilan (batas dan effort dari .env tanpa rilis kode)."""
    import os

    from .inti.konfigurasi import batas_bulan_dari_env
    from .v2.tafsir import EFFORT_BAWAAN
    return {"pemanggil": _pemanggil_v2(), "audit": _AuditMalas(), "batas_fn": batas_bulan_dari_env(),
            "effort": os.environ.get("MILKYBOOK_V2_EFFORT", EFFORT_BAWAAN)}


def milkybook_router():
    from .inti.router import buat_router
    from .v2.router import buat_router_v2
    r = buat_router(_putaran, _PenyimpananMalas(), app_fn=_app, izin_fn_untuk=_izin_untuk,
                    flag_fn=_flag, hari_ini_fn=_hari_ini)
    r.include_router(buat_router_v2(flag_fn=_flag_v2, conn_fn=_koneksi, izin_fn=_izin_v2, tafsir_fn=_tafsir_v2), prefix="/v2")  # flag milkybook_v2 (31 Okt)
    return r
