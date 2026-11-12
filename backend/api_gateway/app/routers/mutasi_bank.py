"""Bank Feed BF-1a (KONTRAK-bankfeed §3, §8): impor mutasi (pratinjau + tulis) dan daftar per rekening. Izin = modul kas_bank (pola generik
/api/bank-accounts: GET baca, POST buat). Penentu di services/bankfeed_impor + bankfeed_daftar; tenant eksplisit di tiap SQL."""
import logging
from datetime import date
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response

from ..schemas.bankfeed import (AksiMutasiResponse, BatalkanCatatanRequest, CalonMutasiResponse, CatatRequest, CocokkanRequest, DaftarMutasiResponse, ImporMutasiRequest, ImporMutasiResponse, KecualikanRequest,
                                LepaskanRequest, PratinjauAksiResponse, PratinjauImporResponse, RiwayatMutasiResponse, TakDiBankResponse)
from ..services import bankfeed_daftar as BD
from ..services import bankfeed_aksi as BA
from ..services import bankfeed_impor as BI
from ..services import bankfeed_tak_di_bank as TD
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
        return {"success": True, **await BD.daftar(conn, ctx["tenant_id"], bank_account_id, status, q, dari, sampai, limit, offset, request.state.user)}


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


# ---------------------------------------------------------------- BF-1c1: aksi (NOL jurnal), riwayat, laporan tak-di-bank
@router.get("/{bank_account_id}/mutasi-bank/tak-di-bank", response_model=TakDiBankResponse)
async def tak_di_bank(request: Request, bank_account_id: UUID, dari: Optional[date] = Query(None), sampai: Optional[date] = Query(None)):
    """Laporan pengecualian: tercatat di MilkyHoop, tak ada di rekening koran (READ ONLY). Hanya tanggal tercakup berkas impor (-3 hari ujung) yang dinilai."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        return {"success": True, **await TD.daftar(conn, ctx["tenant_id"], bank_account_id, dari, sampai)}


@router.get("/{bank_account_id}/mutasi-bank/{mutasi_id}/riwayat", response_model=RiwayatMutasiResponse)
async def riwayat_mutasi_bank(request: Request, bank_account_id: UUID, mutasi_id: UUID):
    """Jejak keputusan satu baris (append-only): diimpor, dicocokkan, dikecualikan, dilepas."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        return {"success": True, "data": await BA.riwayat(conn, ctx["tenant_id"], bank_account_id, mutasi_id)}


async def _pratinjau_aksi(request: Request, bank_account_id: UUID, mutasi_id: UUID, aksi: str, body):
    ctx = {**get_user_context(request), "user": request.state.user}       # BF-1c3: izin modul tujuan (catat/batalkan) diperiksa di BA
    return await BA.pratinjau(await get_pool(), ctx["tenant_id"], bank_account_id, mutasi_id, aksi, body.model_dump(mode="json", exclude_none=True), ctx)


async def _tulis_aksi(request: Request, response: Response, bank_account_id: UUID, mutasi_id: UUID, aksi: str, body):
    ctx = {**get_user_context(request), "user": request.state.user}
    isi = body.model_dump(mode="json", exclude_none=True)
    prefix = f"MUTASI_{aksi.upper()}"
    try:
        pool = await get_pool()
        await BA.siapkan(pool, ctx["tenant_id"], aksi, isi)       # prasyarat peran akun modul tujuan (butuh pool, di luar transaksi)
        async with pool.acquire() as conn:
            async with conn.transaction():
                kp, sd, lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), prefix, mutasi_id, lambda: isi, response)
                if lama is not None:
                    return lama
                data = await BA.tulis(conn, ctx, bank_account_id, mutasi_id, aksi, isi)
                return await idem_buat.simpan(conn, ctx, kp, sd, prefix, {"success": True, "message": data["kalimat"], "data": data}, None)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error aksi mutasi bank {aksi}: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal menyimpan keputusan mutasi bank.") from e


@router.post("/{bank_account_id}/mutasi-bank/{mutasi_id}/cocokkan/preview", response_model=PratinjauAksiResponse)
async def pratinjau_cocokkan(request: Request, bank_account_id: UUID, mutasi_id: UUID, body: CocokkanRequest):
    """Pratinjau cocokkan (READ ONLY): dokumen, alokasi, blok berkode; jurnal selalu kosong."""
    return await _pratinjau_aksi(request, bank_account_id, mutasi_id, "cocokkan", body)


@router.post("/{bank_account_id}/mutasi-bank/{mutasi_id}/cocokkan", response_model=AksiMutasiResponse)
async def cocokkan(request: Request, bank_account_id: UUID, mutasi_id: UUID, body: CocokkanRequest, response: Response = None):
    """Cocokkan baris bank dengan dokumen yang sudah tercatat (NOL jurnal; X-Idempotency-Key opsional)."""
    return await _tulis_aksi(request, response, bank_account_id, mutasi_id, "cocokkan", body)


@router.post("/{bank_account_id}/mutasi-bank/{mutasi_id}/kecualikan/preview", response_model=PratinjauAksiResponse)
async def pratinjau_kecualikan(request: Request, bank_account_id: UUID, mutasi_id: UUID, body: KecualikanRequest):
    """Pratinjau kecualikan (READ ONLY)."""
    return await _pratinjau_aksi(request, bank_account_id, mutasi_id, "kecualikan", body)


@router.post("/{bank_account_id}/mutasi-bank/{mutasi_id}/kecualikan", response_model=AksiMutasiResponse)
async def kecualikan(request: Request, bank_account_id: UUID, mutasi_id: UUID, body: KecualikanRequest, response: Response = None):
    """Kecualikan baris (duplikat / bukan transaksi / lain); NOL jurnal."""
    return await _tulis_aksi(request, response, bank_account_id, mutasi_id, "kecualikan", body)


@router.post("/{bank_account_id}/mutasi-bank/{mutasi_id}/lepaskan/preview", response_model=PratinjauAksiResponse)
async def pratinjau_lepaskan(request: Request, bank_account_id: UUID, mutasi_id: UUID, body: LepaskanRequest):
    """Pratinjau lepaskan (READ ONLY): dokumen yang tetap tercatat."""
    return await _pratinjau_aksi(request, bank_account_id, mutasi_id, "lepaskan", body)


@router.post("/{bank_account_id}/mutasi-bank/{mutasi_id}/lepaskan", response_model=AksiMutasiResponse)
async def lepaskan(request: Request, bank_account_id: UUID, mutasi_id: UUID, body: LepaskanRequest, response: Response = None):
    """Lepaskan keputusan Bank Feed (cocok/kecualikan): tautan dilepas (tak dihapus), dokumen dan jurnal tak disentuh."""
    return await _tulis_aksi(request, response, bank_account_id, mutasi_id, "lepaskan", body)


# ---------------------------------------------------------------- BF-1c2: catat (uang_masuk/uang_keluar/pribadi) + batalkan-catatan (jurnal lewat penulis Uang Masuk/Keluar yang ada)
@router.post("/{bank_account_id}/mutasi-bank/{mutasi_id}/catat/preview", response_model=PratinjauAksiResponse)
async def pratinjau_catat(request: Request, bank_account_id: UUID, mutasi_id: UUID, body: CatatRequest):
    """Pratinjau catat (READ ONLY): jurnal dan saldo dari penentu Uang Masuk/Keluar yang SAMA dengan tulis."""
    return await _pratinjau_aksi(request, bank_account_id, mutasi_id, "catat", body)


@router.post("/{bank_account_id}/mutasi-bank/{mutasi_id}/catat", response_model=AksiMutasiResponse)
async def catat(request: Request, bank_account_id: UUID, mutasi_id: UUID, body: CatatRequest, response: Response = None):
    """Catat baris bank sebagai Uang Masuk/Keluar/Pribadi (jurnal lewat penulis yang ada; baris bertaut ke bank_transaction hasil)."""
    return await _tulis_aksi(request, response, bank_account_id, mutasi_id, "catat", body)


@router.post("/{bank_account_id}/mutasi-bank/{mutasi_id}/batalkan-catatan/preview", response_model=PratinjauAksiResponse)
async def pratinjau_batalkan_catatan(request: Request, bank_account_id: UUID, mutasi_id: UUID, body: BatalkanCatatanRequest):
    """Pratinjau batalkan catatan (READ ONLY): dokumen dibatalkan, jurnal pembalik, baris kembali."""
    return await _pratinjau_aksi(request, bank_account_id, mutasi_id, "batalkan", body)


@router.post("/{bank_account_id}/mutasi-bank/{mutasi_id}/batalkan-catatan", response_model=AksiMutasiResponse)
async def batalkan_catatan(request: Request, bank_account_id: UUID, mutasi_id: UUID, body: BatalkanCatatanRequest, response: Response = None):
    """Batalkan catatan (cara catat/pribadi): void lewat penulis yang ada = jurnal PEMBALIK (Law 2); baris kembali ke Untuk ditinjau."""
    return await _tulis_aksi(request, response, bank_account_id, mutasi_id, "batalkan", body)
