"""Uang MASUK/KELUAR manual -- daftar sendiri standar SO (Kas&Bank gelombang 1, kontrak backend/docs/KONTRAK-kasbank-standar-so-20261109.md,
MASTER 9 Nov 2026). Baris = mutasi manual SAH (KT.ASAL_MANUAL, predikat SAMA dengan void); penentu di services/kasbank_daftar +
kasbank_riwayat. Void tetap POST /api/bank-transactions/{id}/void (kasbank_v2, dipasang di main.py); pratinjaunya di sini.

Putusan MASTER 9 Nov: TANPA pill (kartu masuk/keluar bulan ini + neto); status bawaan `tercatat` (seragam tanpa_batal / BUG-141)."""
from datetime import date
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from ..schemas.kasbank_v2 import VoidTransactionRequest
from ..services import kasbank_daftar as KD
from ..services import kasbank_riwayat as KRW
from ..services import kasbank_tulis as KT
from ..services.bayar_keluar_rencana import Blok
from ..utils.tanggal_tenant import tanggal_dokumen
from .kasbank_v2 import get_pool, get_user_context

router = APIRouter()

TIDAK_ADA = "Mutasi manual tidak ditemukan."


def _syarat(search: Optional[str], i: int) -> tuple:
    """Predikat cari (nomor/keterangan/pihak/akun lawan) atas baris SQL_MANUAL alias d -- SAMA untuk daftar dan /summary."""
    if not search or not search.strip():
        return "", [], i
    syarat, par = [], []
    for kata in search.strip().split():
        syarat.append(f"(d.nomor ILIKE ${i} OR d.description ILIKE ${i} OR d.pihak ILIKE ${i} OR d.lawan_nama ILIKE ${i})")
        par.append(f"%{kata}%")
        i += 1
    return " AND " + " AND ".join(syarat), par, i


@router.get("")
async def list_bank_transactions(
    request: Request,
    arah: Optional[Literal["masuk", "keluar"]] = Query(None),
    bank_account_id: Optional[UUID] = Query(None),
    status: Literal["tercatat", "dibatalkan", "semua"] = Query("tercatat"),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    search: Optional[str] = Query(None),
    aksi: Optional[Literal["masuk_bulan_ini", "keluar_bulan_ini"]] = Query(None),
    with_totals: bool = Query(False, description="sum_masuk/sum_keluar/sum_neto baris terfilter tanpa yang dibatalkan"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    sort_by: Literal["tanggal", "nominal"] = Query("tanggal"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
):
    ctx = get_user_context(request)
    tid = ctx["tenant_id"]
    pool = await get_pool()
    async with pool.acquire() as conn:
        syarat, par, i = [], [tid], 2
        st = KD.sql_status_manual(status if isinstance(status, str) else "tercatat")
        if st:
            syarat.append(st)
        if arah in ("masuk", "keluar"):
            syarat.append("d.net > 0" if arah == "masuk" else "d.net < 0")
        for nilai, sql in ((bank_account_id, "d.ba_id = ${}"), (date_from, "d.tgl >= ${}"), (date_to, "d.tgl <= ${}")):
            if nilai is not None and not hasattr(nilai, "default"):
                syarat.append(sql.format(i))
                par.append(nilai)
                i += 1
        if aksi in KD.KARTU_MANUAL:
            syarat.append(KD.sql_aksi_manual(aksi, f"${i}"))
            par.append(await tanggal_dokumen(conn, tid))
            i += 1
        cari, par_cari, i = _syarat(search if isinstance(search, str) else None, i)
        par += par_cari
        where = (" AND ".join(syarat) or "TRUE") + cari
        dasar = f"FROM ({KD.SQL_MANUAL}) d WHERE {where}"
        total = await conn.fetchval(f"SELECT COUNT(*) {dasar}", *par)
        jumlah = {}
        if with_totals is True:  # Query() default = objek truthy saat dipanggil langsung -> `is True`
            r = await conn.fetchrow(f"SELECT COALESCE(SUM(d.net) FILTER (WHERE d.net > 0 AND NOT d.batal), 0) AS masuk, "
                                    f"COALESCE(-SUM(d.net) FILTER (WHERE d.net < 0 AND NOT d.batal), 0) AS keluar {dasar}", *par)
            jumlah = {"sum_masuk": KD.rp(r["masuk"]), "sum_keluar": KD.rp(r["keluar"]), "sum_neto": KD.rp(r["masuk"] - r["keluar"])}
        urut = {"tanggal": "d.tgl", "nominal": "abs(d.net)"}[sort_by if sort_by in ("tanggal", "nominal") else "tanggal"]
        arah_urut = "ASC" if sort_order == "asc" else "DESC"
        rows = await conn.fetch(f"SELECT d.* {dasar} ORDER BY {urut} {arah_urut}, d.created_at {arah_urut}, d.id "
                                f"LIMIT ${i} OFFSET ${i + 1}", *par, limit, skip)
    return {"items": [KD.baris_manual(r) for r in rows], "total": total, "has_more": (skip + limit) < total, **jumlah}


# WAJIB di atas /{transaction_id}: rute terdaftar duluan menang.
@router.get("/summary")
async def bank_transactions_summary(request: Request, search: Optional[str] = Query(None)):
    """Kartu {k}_count/_value = predikat SAMA dengan ?aksi= daftar (KD.sql_aksi_manual); neto = masuk - keluar bulan ini."""
    ctx = get_user_context(request)
    tid = ctx["tenant_id"]
    pool = await get_pool()
    async with pool.acquire() as conn:
        hari = await tanggal_dokumen(conn, tid)
        cari, par_cari, _ = _syarat(search if isinstance(search, str) else None, 3)
        kol = ", ".join(f"COUNT(*) FILTER (WHERE {KD.sql_aksi_manual(k, '$2')}) AS {k}_count, "
                        f"COALESCE(ABS(SUM(d.net) FILTER (WHERE {KD.sql_aksi_manual(k, '$2')})), 0) AS {k}_value" for k in KD.KARTU_MANUAL)
        r = await conn.fetchrow(f"SELECT {kol}, COUNT(*) FILTER (WHERE NOT d.batal) AS total_count "
                                f"FROM ({KD.SQL_MANUAL}) d WHERE TRUE{cari}", tid, hari, *par_cari)
    data = {f"{k}_count": int(r[f"{k}_count"]) for k in KD.KARTU_MANUAL}
    data.update({f"{k}_value": KD.rp(r[f"{k}_value"]) for k in KD.KARTU_MANUAL})
    data["neto_bulan_ini_value"] = KD.rp(r["masuk_bulan_ini_value"] - r["keluar_bulan_ini_value"])
    data["total_count"] = int(r["total_count"])
    return {"success": True, "data": data}


@router.get("/{transaction_id}")
async def get_bank_transaction(request: Request, transaction_id: UUID):
    ctx = get_user_context(request)
    tid = ctx["tenant_id"]
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await conn.fetchrow(KD.SQL_MANUAL + " AND bt.id = $2", tid, transaction_id)
        if not r:
            raise HTTPException(status_code=404, detail=TIDAK_ADA)
        return {"success": True, "data": {**KD.baris_manual(r), "aksi_tersedia": await KD.aksi_tersedia_manual(conn, tid, transaction_id)}}


@router.get("/{transaction_id}/history")
async def get_bank_transaction_history(request: Request, transaction_id: UUID, limit: int = Query(200, ge=1, le=500)):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        data = await KRW.riwayat_manual(conn, ctx["tenant_id"], transaction_id, limit)
    if data is None:
        raise HTTPException(status_code=404, detail=TIDAK_ADA)
    return {"success": True, "data": data}


@router.get("/{transaction_id}/documents")
async def get_bank_transaction_documents(request: Request, transaction_id: UUID):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        data = await KRW.dokumen_manual(conn, ctx["tenant_id"], transaction_id)
    if data is None:
        raise HTTPException(status_code=404, detail=TIDAK_ADA)
    return {"success": True, "data": data}


@router.post("/{transaction_id}/void/preview")
async def preview_void_bank_transaction(request: Request, transaction_id: UUID, body: VoidTransactionRequest):
    """Pratinjau batal mutasi manual -- penentu SAMA dengan POST /{id}/void (KT.rencana_void_manual), READ ONLY.
    saldo[].minus = isyarat (K3: batal tidak dikunci saldo), bukan blok."""
    ctx = get_user_context(request)
    tid = ctx["tenant_id"]
    return await KT.pratinjau(await get_pool(), tid, lambda c: KT.rencana_void_manual(c, tid, transaction_id, body.reason, Blok(True)),
                              body.model_dump(mode="json"))
