"""Bank Feed BF-1a (KONTRAK-bankfeed §3, §8): impor mutasi (pratinjau + tulis) dan daftar per rekening. Izin = modul kas_bank (pola generik
/api/bank-accounts: GET baca, POST buat). Penentu di services/bankfeed_impor + bankfeed_daftar; tenant eksplisit di tiap SQL."""
import logging
from datetime import date
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response

from ..schemas.bankfeed import CalonMutasiResponse, DaftarMutasiResponse, ImporMutasiRequest, ImporMutasiResponse, PratinjauImporResponse
from ..services import bankfeed_daftar as BD
from ..services import bankfeed_impor as BI
from ..services import bankfeed_tebak as TB
from ..services import idem_buat

logger = logging.getLogger(__name__)
router = APIRouter()


async def get_pool():
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Silakan masuk terlebih dahulu.")
    u = request.state.user
    if not u.get("tenant_id") or not u.get("user_id"):
        raise HTTPException(status_code=401, detail="Sesi tidak valid, silakan masuk lagi.")
    return {"tenant_id": u["tenant_id"], "user_id": u["user_id"]}


@router.get("/{bank_account_id}/mutasi-bank", response_model=DaftarMutasiResponse)
async def daftar_mutasi_bank(request: Request, bank_account_id: UUID,
                             status: Optional[Literal["untuk_ditinjau", "mungkin_duplikat", "dicatat", "dikecualikan"]] = Query(None),
                             q: Optional[str] = Query(None, max_length=100), dari: Optional[date] = Query(None), sampai: Optional[date] = Query(None),
                             limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    """Mutasi bank satu rekening: ?status= tab, ?q= keterangan/nominal, ?dari/?sampai=; cacah per status + total Σ server."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        return {"success": True, **await BD.daftar(conn, ctx["tenant_id"], bank_account_id, status, q, dari, sampai, limit, offset)}


@router.get("/{bank_account_id}/mutasi-bank/{mutasi_id}/calon", response_model=CalonMutasiResponse)
async def calon_mutasi_bank(request: Request, bank_account_id: UUID, mutasi_id: UUID, q: Optional[str] = Query(None, max_length=100),
                            limit: int = Query(20, ge=1, le=50)):
    """Calon dokumen untuk satu baris (READ ONLY; G1): nominal persis atau selisih Rp2.500 (periksa); ?q= nomor/nama/nominal melebar ke rentang -90/+20."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        return {"success": True, **await TB.calon(conn, ctx["tenant_id"], bank_account_id, mutasi_id, q, limit)}


@router.post("/{bank_account_id}/mutasi-bank/impor/preview", response_model=PratinjauImporResponse)
async def pratinjau_impor(request: Request, bank_account_id: UUID, body: ImporMutasiRequest):
    """Pratinjau impor (READ ONLY): format, rentang, baru/duplikat/mungkin_duplikat, baris dilewati, saldo berkas vs buku, payload untuk simpan."""
    ctx = get_user_context(request)
    return await BI.pratinjau(await get_pool(), ctx["tenant_id"], ctx["user_id"], bank_account_id, body.model_dump(mode="json"))


@router.post("/{bank_account_id}/mutasi-bank/impor", response_model=ImporMutasiResponse, status_code=201)
async def impor_mutasi_bank(request: Request, bank_account_id: UUID, body: ImporMutasiRequest, response: Response = None):
    """Simpan impor (payload pratinjau). Penentu SAMA dengan pratinjau; NOL jurnal; X-Idempotency-Key opsional (replay = respons asli)."""
    ctx = get_user_context(request)
    isi = body.model_dump(mode="json")
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                kp, sd, lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "MUTASI_IMPOR", bank_account_id, lambda: isi, response)
                if lama is not None:
                    return lama
                data = await BI.tulis(conn, ctx, bank_account_id, isi)
                resp = {"success": True, "message": f"{data['baru']} mutasi diimpor, {data['duplikat']} sudah ada.", "data": data}
                return await idem_buat.simpan(conn, ctx, kp, sd, "MUTASI_IMPOR", resp, None)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error impor mutasi bank: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal mengimpor mutasi bank.") from e
