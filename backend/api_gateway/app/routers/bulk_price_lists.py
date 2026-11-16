"""Ubah HARGA massal per daftar harga (D3, 13 Nov 2026, putusan pemilik): persen ATAU nominal atas dasar harga lama | HPP, pembulatan, tanggal berlaku, pratinjau per barang.
POST /api/price-lists/{id}/bulk/price/preview  {item_ids?, mode, nilai, berlaku_mulai?} -> per barang {ok, blocks, preview{rencana, peringatan[]}} READ ONLY.
POST /api/price-lists/{id}/bulk/price          sama + X-Idempotency-Key (WAJIB) -> tiap barang transaksi SENDIRI, idempotensi per barang, audit BULK_HARGA.
item_ids kosong = SEMUA barang di daftar itu. Penentu SAMA dgn pratinjau (services/harga_massal). Tanpa jurnal."""
import logging
from datetime import date
from decimal import Decimal
from typing import List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, model_validator

from ..services import bulk
from ..services import harga_massal as HM
from ..services import harga_riwayat as HR
from ..services import harga_salin as HS
from ..services import idem_buat

logger = logging.getLogger(__name__)
router = APIRouter()
AKSI = "harga"
assert AKSI in bulk.AKSI_DIIZINKAN


async def get_pool():
    from ..services.db_pool import get_db_pool
    return await get_db_pool()


def _ctx(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user or not user.get("tenant_id"):
        raise HTTPException(status_code=401, detail="Silakan masuk terlebih dahulu.")
    return {"tenant_id": user["tenant_id"], "user_id": user.get("user_id") or user.get("id"), "username": user.get("username")}


class BulkHargaRequest(BaseModel):
    item_ids: Optional[List[str]] = Field(None, max_length=2000, description="barang terpilih; kosong/null = semua barang di daftar")
    mode: Literal["persen", "nominal"]
    nilai: Decimal = Field(..., description="persen (mis. 10 = naik 10%, -5 = turun 5%) atau nominal Rupiah (+/-)")
    berlaku_mulai: Optional[date] = Field(None, description="start_date baris yang diubah; kosong = harga langsung berlaku")
    dasar: Literal["harga_lama", "hpp"] = Field("harga_lama", description="dasar hitung: harga baris sekarang atau HPP (WAC satuan dasar x faktor konversi)")
    pembulatan_kelipatan: int = Field(0, description="0 = tanpa pembulatan (bawaan); selain itu 1/10/100/500/1000")
    pembulatan_arah: Literal["terdekat", "atas", "bawah"] = "terdekat"

    @model_validator(mode="after")
    def _periksa(self):
        if self.nilai == 0:
            raise ValueError("Nilai perubahan tidak boleh 0.")
        if self.pembulatan_kelipatan not in HM.KELIPATAN:
            raise ValueError("Kelipatan pembulatan harus 0, 1, 10, 100, 500, atau 1000.")
        if self.mode == "persen" and self.nilai < Decimal("-100"):
            raise ValueError("Penurunan persen tidak boleh lebih dari 100%.")
        return self


async def _siapkan(pool, ctx: dict, price_list_id: UUID, body: BulkHargaRequest):
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM price_lists WHERE id = $1 AND tenant_id = $2", price_list_id, ctx["tenant_id"]):
            raise HTTPException(status_code=404, detail="Daftar harga tidak ditemukan.")
        if body.item_ids:
            ids = bulk.validasi_ids(body.item_ids, "tulis")
        else:
            ids = bulk.validasi_ids([str(r["item_id"]) for r in await conn.fetch(
                """SELECT DISTINCT pli.item_id FROM price_list_items pli JOIN price_lists pl ON pl.id = pli.price_list_id
                   WHERE pl.tenant_id = $1 AND pli.price_list_id = $2""", ctx["tenant_id"], price_list_id)] or [], "tulis")
        nama = {str(r["id"]): r["nama_produk"] for r in await conn.fetch(
            "SELECT id, nama_produk FROM products WHERE tenant_id = $1 AND id = ANY($2::uuid[])", ctx["tenant_id"], ids)}
    return ids, nama


def _fungsi(price_list_id: UUID, body: BulkHargaRequest):
    async def tulis(conn, ctx, i):
        r = await HM.ukur_rencana(conn, ctx["tenant_id"], price_list_id, i, body.mode, body.nilai, body.berlaku_mulai, kunci=True,
                                dasar=body.dasar, kelipatan=body.pembulatan_kelipatan, arah=body.pembulatan_arah)
        if r["blocks"]:
            raise HTTPException(status_code=409 if r["blocks"][0]["code"] != "DH_BARANG_TAK_DI_DAFTAR" else 404,
                                detail={"code": r["blocks"][0]["code"], "message": r["blocks"][0]["message"]})
        await HM.terapkan(conn, ctx["tenant_id"], price_list_id, r["rencana"])
        return {"id": str(i), "jumlah_baris": r["rencana"]["jumlah_baris"], "baris": r["rencana"]["baris"], "peringatan": r["peringatan"]}

    async def pratinjau(conn, ctx, i):
        r = await HM.ukur_rencana(conn, ctx["tenant_id"], price_list_id, i, body.mode, body.nilai, body.berlaku_mulai,
                                dasar=body.dasar, kelipatan=body.pembulatan_kelipatan, arah=body.pembulatan_arah)
        return r["blocks"], ({**r["rencana"], "peringatan": r["peringatan"]} if not r["blocks"] else None)
    return tulis, pratinjau


def baris_riwayat_massal(data: dict) -> list:
    """Hasil jalankan_per_item (item ok, bukan replay) -> baris riwayat [{dari, ke}] (harga lama/baru per baris; nama barang dari `number`)."""
    out = []
    for it in data["items"]:
        if it["status"] != "ok" or it["replay"] or not it.get("result"):
            continue
        for b in it["result"].get("baris") or []:
            dasar = {"line_id": str(b["id"]), "item_id": it["id"], "kode": None, "nama": it.get("number"), "satuan": b.get("unit_text"), "min_quantity": float(b["min_quantity"]),
                     "min_price": float(b["min_price"]) if b.get("min_price") is not None else None, "diskon_persen": None, "mulai": None, "berakhir": None}
            out.append({"dari": {**dasar, "harga": float(b["dari"])}, "ke": {**dasar, "harga": float(b["ke"])}})
    return out


async def _catat_riwayat_massal(pool, ctx: dict, price_list_id: UUID, body: "BulkHargaRequest", data: dict) -> None:
    baris = baris_riwayat_massal(data)
    if not baris:
        return
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                kode = await conn.fetchval("SELECT code FROM price_lists WHERE id = $1 AND tenant_id = $2", price_list_id, ctx["tenant_id"])
                arah = f"{'+' if body.nilai > 0 else ''}{body.nilai:f}{'%' if body.mode == 'persen' else ''}"
                await HR.catat(conn, ctx, price_list_id, kode, "UBAH_MASSAL",
                               f"Ubah harga massal {arah}: {sum(1 for i in data['items'] if i['status'] == 'ok' and not i['replay'])} barang, {len(baris)} baris"
                               + (f", mulai {body.berlaku_mulai.isoformat()}" if body.berlaku_mulai else ""), baris)
    except Exception:  # noqa: BLE001
        logger.error("BUG-252 riwayat ubah massal gagal dicatat (harga sudah berubah)", exc_info=True)


@router.post("/{price_list_id}/bulk/price/preview")
async def bulk_price_preview(request: Request, price_list_id: UUID, body: BulkHargaRequest):
    ctx = _ctx(request)
    pool = await get_pool()
    ids, nama = await _siapkan(pool, ctx, price_list_id, body)
    _, pratinjau = _fungsi(price_list_id, body)
    return {"success": True, "data": await bulk.pratinjau_per_item(pool, ctx, AKSI, ids, pratinjau, nama, baca_saja=True)}


@router.post("/{price_list_id}/bulk/price")
async def bulk_price(request: Request, price_list_id: UUID, body: BulkHargaRequest):
    ctx = _ctx(request)
    kunci = idem_buat.kunci_dari(request)
    if not kunci:
        raise HTTPException(status_code=400, detail={"code": "BULK_KUNCI_WAJIB", "message": "Aksi massal wajib membawa header X-Idempotency-Key."})
    pool = await get_pool()
    ids, nama = await _siapkan(pool, ctx, price_list_id, body)
    tulis, _ = _fungsi(price_list_id, body)
    data = await bulk.jalankan_per_item(
        pool, ctx, AKSI, "price_lists", ids, tulis, kunci_batch=kunci,
        payload={"price_list_id": str(price_list_id), "mode": body.mode, "nilai": str(body.nilai),
                 "berlaku_mulai": body.berlaku_mulai.isoformat() if body.berlaku_mulai else None,
                 "dasar": body.dasar, "kelipatan": body.pembulatan_kelipatan, "arah": body.pembulatan_arah},
        nomor=nama)
    await _catat_riwayat_massal(pool, ctx, price_list_id, body, data)   # BUG-252: SATU event berbaris (replay/ditolak tak dicatat ulang)
    return {"success": True, "data": data}


@router.get("/{price_list_id}/riwayat")
async def riwayat_daftar(request: Request, price_list_id: UUID, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), aksi: Optional[str] = Query(None),
                         dari: Optional[date] = Query(None), sampai: Optional[date] = Query(None)):
    """BUG-252 riwayat perubahan daftar harga (audit_logs immutable; bersaring tenant + daftar): terbaru dulu, aksi_text + ringkas dari server. Izin R price_list."""
    ctx = _ctx(request)
    if aksi is not None and aksi not in HR.AKSI_TEKS:
        raise HTTPException(status_code=400, detail={"code": "RIWAYAT_AKSI_TIDAK_DIKENAL", "message": f"Aksi riwayat: {', '.join(HR.AKSI_TEKS)}."})
    pool = await get_pool()
    async with pool.acquire() as conn:
        ada = await conn.fetchrow("SELECT code FROM price_lists WHERE id = $1 AND tenant_id = $2", price_list_id, ctx["tenant_id"])
        hasil = await HR.daftar(conn, ctx["tenant_id"], price_list_id, limit, offset, aksi, dari, sampai) if ada else None
    if hasil is None:                                   # daftar sudah dihapus: riwayatnya tetap ada (immutable) -> baca tanpa cek daftar
        async with pool.acquire() as conn:
            hasil = await HR.daftar(conn, ctx["tenant_id"], price_list_id, limit, offset, aksi, dari, sampai)
        if hasil["total"] == 0:
            raise HTTPException(status_code=404, detail="Daftar harga tidak ditemukan.")
    return {"success": True, "data": {**hasil, "limit": limit, "offset": offset, "aksi_pilihan": [{"aksi": k, "teks": v} for k, v in HR.AKSI_TEKS.items()]}}


# =============================================================================
# SALIN DAFTAR (BUG-256): pratinjau == tulis, atomik, idempoten, riwayat 252 (services/harga_salin)
# =============================================================================
def _badan_salin(body: dict) -> tuple:
    body = body or {}
    kode, nama = HS.rapikan(body.get("code"), body.get("name"))
    return kode, nama


@router.post("/{price_list_id}/salin/preview")
async def pratinjau_salin(request: Request, price_list_id: UUID, body: dict):
    """Pratinjau salin (READ ONLY, penentu SAMA dengan tulis): {ok, blocks[], peringatan[], rencana{sumber, baru, jumlah_baris, jumlah_barang, jumlah_berjangka, teks}, payload{code, name}}."""
    ctx = _ctx(request)
    kode, nama = _badan_salin(body)
    pool = await get_pool()
    async with pool.acquire() as conn:
        tr = conn.transaction(readonly=True)
        await tr.start()
        try:
            r = await HS.rencana(conn, ctx["tenant_id"], price_list_id, kode, nama)
        finally:
            await tr.rollback()
    return {"success": True, "data": {**r, "payload": {"code": kode, "name": nama}}}


@router.post("/{price_list_id}/salin")
async def salin_daftar(request: Request, price_list_id: UUID, body: dict, response: Response = None):
    """Salin ATOMIK. Badan = payload pratinjau {code, name}; X-Idempotency-Key WAJIB (replay = respons asli). 201-gaya: data {id, code, name, jumlah_baris}."""
    import asyncpg
    ctx = _ctx(request)
    kunci = idem_buat.kunci_dari(request)
    if not kunci:
        raise HTTPException(status_code=400, detail={"code": "SALIN_KUNCI_WAJIB", "message": "Salin daftar wajib membawa header X-Idempotency-Key."})
    kode, nama = _badan_salin(body)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            kp, sd, lama = await idem_buat.mulai_aksi(conn, ctx, kunci, "DH_SALIN", price_list_id, lambda: {"code": kode, "name": nama}, response)
            if lama is not None:
                return lama
            try:
                data = await HS.tulis(conn, ctx, price_list_id, kode, nama)
            except asyncpg.exceptions.UniqueViolationError:        # balapan: kode dipakai di antara pemeriksaan dan INSERT
                raise HTTPException(status_code=409, detail={"code": "KODE_DAFTAR_GANDA", "message": "Kode daftar harga itu sudah dipakai.", "field": "code"})
            resp = {"success": True, "message": f"Daftar disalin: {data['jumlah_baris']} harga.", "data": data}
            return await idem_buat.simpan(conn, ctx, kp, sd, "DH_SALIN", resp, None)
