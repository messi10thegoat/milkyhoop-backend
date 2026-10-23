"""Dashboard v2 (D3) — GET /api/dashboard/v2/summary, GET /api/dashboard/tasks, POST/DELETE dismiss.

Hitungan di services/dashboard_v2.py (satu sumber dengan laporan; lihat docstring di sana).

Rute ringkasan = /v2/summary, BUKAN /summary: /api/dashboard/summary sudah dipakai dashboard LAMA
(period=7d|30d|month, respons berbeda) — nilai 'month' bertabrakan, jadi rute lama dibiarkan utuh
(flag OFF = identik) dan kontrak baru hidup di jalur sendiri.

Pagar: /api/dashboard dilewati PermissionMiddleware (SKIP) -> require_active_membership + izin per
bagian lewat services/dashboard_izin.boleh_semua (OWNER lolos; galat = tidak boleh). Bagian tanpa izin
= null + dicatat di `omitted` (pola /summary lama). Tugas tanpa izin modulnya tak dikirim.

Cache 60 dtk per (tenant, periode, hari bisnis) lewat services.cache (Redis, kunci `dashboard:*:{tenant}:*`
-> ikut dibatalkan invalidate_dashboard_cache oleh DashboardCacheInvalidationMiddleware pada setiap
tulis finansial). Penyaringan izin & dismiss terjadi SESUDAH cache (per pengguna, tak ikut di-cache).
"""
import logging
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..services import dashboard_v2 as dv2
from ..services.cache import cached_fetch
from ..services.dashboard_izin import boleh_semua
from ..services.db_pool import get_db_pool
from ..services.role_resolution import require_active_membership
from ..utils.tanggal_tenant import tanggal_dokumen, zona_tenant

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_active_membership)])

CACHE_DETIK = 60


def _pengguna(request: Request) -> tuple:
    user = getattr(request.state, "user", None) or {}
    tenant_id, user_id = user.get("tenant_id"), user.get("user_id")
    if not tenant_id or not user_id:
        raise HTTPException(status_code=401, detail="Sesi tidak valid, silakan masuk lagi.")
    return str(tenant_id), str(user_id)


async def _waktu_tenant(conn, tenant_id: str):
    zona = await zona_tenant(conn, tenant_id)
    hari_ini = await tanggal_dokumen(conn, tenant_id)
    as_of = datetime.now(timezone.utc).astimezone(zona).isoformat(timespec="seconds")
    return zona, hari_ini, as_of


@router.get("/v2/summary")
async def ringkasan_v2(request: Request,
                       period: str = Query("month", pattern="^(day|week|month|year|bulan_ini|bulan_lalu|kuartal_ini|tahun_ini|kustom)$"),
                       from_: Optional[date] = Query(None, alias="from"), to: Optional[date] = Query(None)):
    # T2 (KONTRAK-dashboard-T2 §2): kunci lama day|week|month|year tetap (makna lama); from/to hanya untuk kustom
    tenant_id, _ = _pengguna(request)
    # panggilan langsung (tes/harness) tanpa argumen -> default = objek Query, bukan nilai (pelajaran gerbang 23 Okt)
    from_ = from_ if isinstance(from_, date) else None
    to = to if isinstance(to, date) else None
    if period != "kustom":
        from_, to = None, None
    elif from_ is None or to is None or from_ > to or dv2.hari_periode_inklusif(from_, to) > dv2.MAKS_HARI_KUSTOM:
        raise HTTPException(status_code=422, detail={"code": "PERIOD_INVALID",
                                                     "message": "Periode kustom butuh tanggal dari <= sampai, paling lama 366 hari."})
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        zona, hari_ini, as_of = await _waktu_tenant(conn, tenant_id)

    async def hitung():
        # koneksi sendiri: cached_fetch bisa memanggilnya di latar (refresh) sesudah permintaan selesai
        async with pool.acquire() as c:
            return await dv2.ringkasan(c, tenant_id, period, hari_ini, zona.key, as_of, from_, to)

    rentang = f":{from_.isoformat()}:{to.isoformat()}" if period == "kustom" else ""
    data = dict(await cached_fetch(f"dashboard:v2summary:{tenant_id}:{period}{rentang}:{hari_ini.isoformat()}",
                                   hitung, ttl=CACHE_DETIK, stale_ttl=CACHE_DETIK))
    omitted = []
    for bagian, modul in dv2.BAGIAN_MODUL.items():
        if not await boleh_semua(request, modul):
            data[bagian] = None
            omitted.append({"widget": bagian, "modules": list(modul)})
    if data.get("aging") is not None:
        data["aging"] = dict(data["aging"])
        for sisi, modul in dv2.BAGIAN_AGING.items():
            if not await boleh_semua(request, modul):
                data["aging"][sisi] = None
                omitted.append({"widget": f"aging.{sisi}", "modules": list(modul)})
    data["omitted"] = omitted
    return data


async def _dismissed(conn, tenant_id: str, user_id: str, zona_nama: str, hari_ini) -> tuple:
    rows = await conn.fetch(
        """SELECT task_key, (created_at AT TIME ZONE $3)::date = $4 AS hari_ini
           FROM dashboard_task_state
           WHERE tenant_id = $1 AND user_id = $2 AND state = 'dismissed'""",
        tenant_id, user_id, zona_nama, hari_ini,
    )
    return {r["task_key"] for r in rows}, sum(1 for r in rows if r["hari_ini"])


@router.get("/tasks")
async def tugas(request: Request):
    tenant_id, user_id = _pengguna(request)
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        zona, hari_ini, as_of = await _waktu_tenant(conn, tenant_id)
        dismissed, done_today = await _dismissed(conn, tenant_id, user_id, zona.key, hari_ini)

    async def hitung():
        async with pool.acquire() as c:
            return await dv2.tugas_tenant(c, tenant_id, hari_ini)

    async def hitung_pengingat():
        from ..services import pengingat as P
        async with pool.acquire() as c:
            return await P.hitung(c, tenant_id, hari_ini)

    semua = await cached_fetch(f"dashboard:v2tasks:{tenant_id}:{hari_ini.isoformat()}",
                               hitung, ttl=CACHE_DETIK, stale_ttl=CACHE_DETIK)
    pengingat = await cached_fetch(f"dashboard:v2pengingat:{tenant_id}:{hari_ini.isoformat()}",
                                   hitung_pengingat, ttl=CACHE_DETIK, stale_ttl=CACHE_DETIK)
    boleh = [t for t in semua if await boleh_semua(request, dv2.TUGAS_MODUL[t["type"]])]
    boleh_p = [p for p in pengingat if await boleh_semua(request, (p["izin"],))]  # tanpa izin modul = tak dikirim (pola tasks)
    return dv2.rangkum_tugas(boleh, dismissed, done_today, as_of, boleh_p)


def _kunci_sah(key: str) -> str:
    if not dv2.POLA_KUNCI.match(key or ""):
        raise HTTPException(status_code=422, detail={"code": "TASK_KEY_INVALID", "message": "Kunci tugas tidak dikenal."})
    return key


@router.post("/tasks/{key:path}/dismiss")
async def tandai_selesai(request: Request, key: str):
    tenant_id, user_id = _pengguna(request)
    _kunci_sah(key)
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO dashboard_task_state (tenant_id, user_id, task_key, state)
               VALUES ($1, $2, $3, 'dismissed')
               ON CONFLICT (tenant_id, user_id, task_key) DO NOTHING""",
            tenant_id, user_id, key,
        )
    return {"key": key, "state": "dismissed"}


@router.delete("/tasks/{key:path}/dismiss")
async def urungkan_selesai(request: Request, key: str):
    tenant_id, user_id = _pengguna(request)
    _kunci_sah(key)
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM dashboard_task_state WHERE tenant_id = $1 AND user_id = $2 AND task_key = $3",
            tenant_id, user_id, key,
        )
    return {"key": key, "state": None}
