"""Aksi massal daftar (U1b, 5 Okt 2026, MASTER GO F1) -- infra bersama.

Prinsip (putusan MASTER 5 Okt): satu permintaan per aksi per modul (rate limiter prod 100 tulis/menit/bucket); DAFTAR PUTIH aksi
(aksi uang TIDAK punya rute massal -- dikunci tes AST); tiap item = transaksi SENDIRI (satu gagal tak membatalkan yang lain);
idempotensi PER ITEM lewat idem_buat.mulai_aksi (kunci efektif BULK_{AKSI}:{user}:{id}:{kunci_batch}); hasil per item;
batas jumlah; audit BULK_{AKSI}; tenant selalu dari JWT dan eksplisit di setiap SQL; id tenant lain = tak ditemukan.

F1 hanya memakai: validasi_ids, buat_csv, catat_audit. jalankan_per_item dipakai F2+ (aksi tulis) dan dikunci tes sejak sekarang.
"""
import csv
import io
import json
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Awaitable, Callable, Iterable, List, Optional
from uuid import UUID

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Batas jumlah per permintaan (putusan MASTER): tulis 50, CSV 500, PDF 25 (F5).
BATAS = {"tulis": 50, "csv": 500, "pdf": 25}

# DAFTAR PUTIH aksi massal. Tambah aksi = sengaja, lewat putusan; aksi uang (terbit faktur, penerimaan, uang muka, refund,
# void/apply NK, terbit proforma) TIDAK BOLEH ada di sini -- test_bulk_f1 memindai rute + impor.
AKSI_DIIZINKAN = frozenset({"export", "confirm", "delete", "send"})  # F2: SO confirm/delete; F3: Penawaran delete + send (tandai terkirim)

_RAWAN_RUMUS = ("=", "+", "-", "@", "\t", "\r")


def validasi_ids(ids: Iterable, jenis_batas: str) -> List[UUID]:
    """Daftar id -> UUID unik berurutan (urutan pilihan dipertahankan). 400 bila kosong / tak valid / melebihi batas."""
    batas = BATAS[jenis_batas]
    hasil: List[UUID] = []
    lihat = set()
    for x in ids or []:
        try:
            u = x if isinstance(x, UUID) else UUID(str(x))
        except (ValueError, AttributeError, TypeError):
            raise HTTPException(status_code=400, detail={"code": "BULK_ID_TAK_VALID",
                                                         "message": f"ID dokumen tidak valid: {str(x)[:40]}"})
        if u not in lihat:
            lihat.add(u)
            hasil.append(u)
    if not hasil:
        raise HTTPException(status_code=400, detail={"code": "BULK_KOSONG", "message": "Pilih minimal satu dokumen."})
    if len(hasil) > batas:
        raise HTTPException(status_code=400, detail={
            "code": "BULK_TERLALU_BANYAK", "batas": batas,
            "message": f"Maksimal {batas} dokumen per permintaan; Anda memilih {len(hasil)}. Bagi menjadi beberapa kali."})
    return hasil


def _sel(v) -> str:
    """Satu sel CSV. Angka dan tanggal apa adanya (tanpa format ribuan, tanpa notasi ilmiah); TEKS yang diawali = + - @ diberi
    awalan ' supaya tak dieksekusi sebagai rumus oleh spreadsheet (CSV injection). Angka negatif TIDAK diberi awalan."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "ya" if v else "tidak"
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat(sep=" ", timespec="minutes")
    if isinstance(v, date):
        return v.isoformat()
    s = str(v)
    if s.startswith(_RAWAN_RUMUS):
        return "'" + s
    return s


def buat_csv(kolom: List[str], baris: List[list]) -> bytes:
    """CSV RFC 4180 (CRLF, kutip otomatis), UTF-8 dengan BOM supaya Excel membaca huruf Indonesia dengan benar."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow([_sel(k) for k in kolom])
    for b in baris:
        w.writerow([_sel(x) for x in b])
    return out.getvalue().encode("utf-8-sig")


async def catat_audit(conn, ctx: dict, aksi: str, modul: str, ids: List[UUID], ringkas: Optional[dict] = None) -> None:
    """Satu baris audit_logs BULK_{AKSI} (siapa, modul, berapa, id pertama-pertama). Append-only (Law 12). Di transaksi pemanggil."""
    meta = {"modul": modul, "jumlah": len(ids), "ids": [str(i) for i in ids[:50]], **(ringkas or {})}
    await conn.execute(
        """INSERT INTO audit_logs (id, "eventType", entity_type, tenant_id, source, metadata, success, "createdAt", "userId")
           VALUES (gen_random_uuid()::text, $1, 'bulk', $2, 'api:bulk', $3::jsonb, true, now(), $4)""",
        f"BULK_{aksi.upper()}", ctx["tenant_id"], json.dumps(meta), str(ctx.get("user_id") or "") or None)


def _pesan(e: HTTPException) -> tuple:
    d = e.detail
    if isinstance(d, dict):
        return d.get("code"), d.get("message") or str(d)
    return None, str(d)


async def pratinjau_per_item(pool, ctx: dict, aksi: str, ids: List[UUID],
                             fn: Callable[[object, dict, UUID], Awaitable[tuple]], nomor: Optional[dict] = None) -> dict:
    """Pratinjau massal: fn(conn, ctx, id) -> (blok: list[{code,message,...}], ringkasan|None) memakai penentu + inti tunggal YANG
    SAMA. SEMUA item berurutan di SATU transaksi lalu di-ROLLBACK (efek item sebelumnya terlihat oleh yang sesudahnya, mis. kode order
    berurutan, persis tulis nyata); tiap item di savepoint sendiri (HTTPException = blok, tak merusak item lain). Nol tulis."""
    items = []
    async with pool.acquire() as conn:
        luar = conn.transaction()
        await luar.start()
        try:
            for i in ids:
                blok, ringkas = [], None
                try:
                    async with conn.transaction():
                        blok, ringkas = await fn(conn, ctx, i)
                except HTTPException as e:
                    kode, pesan = _pesan(e)
                    blok, ringkas = [{"code": kode or f"HTTP_{e.status_code}", "message": pesan}], None
                except Exception:
                    logger.error("bulk preview %s item %s gagal", aksi, i, exc_info=True)
                    blok, ringkas = [{"code": "BULK_ITEM_GALAT", "message": "Item ini gagal dianalisis."}], None
                blok = [{"code": b["code"], "message": b["message"]} for b in blok]
                items.append({"id": str(i), "number": (nomor or {}).get(str(i)), "ok": not blok, "blocks": blok,
                              "preview": ringkas if not blok else None})
        finally:
            await luar.rollback()
    ok = sum(1 for x in items if x["ok"])
    return {"action": aksi, "total": len(items), "can_run_any": ok > 0, "ok_count": ok, "rejected_count": len(items) - ok,
            "items": items, "preview": True}


async def jalankan_per_item(pool, ctx: dict, aksi: str, modul: str, ids: List[UUID],
                            fn: Callable[[object, dict, UUID], Awaitable[dict]],
                            kunci_batch: Optional[str] = None, payload: Optional[dict] = None,
                            nomor: Optional[dict] = None) -> dict:
    """Tulis massal: tiap item di transaksi SENDIRI (+ idempotensi per item). fn(conn, ctx, id) -> respons item (dict) atau
    HTTPException (4xx = ditolak, 409 kunci terpakai = ditolak, selain itu = galat). Hasil: urutan = urutan ids.
    Audit BULK_{AKSI} ditulis SESUDAH semua item (satu baris ringkas). Dipakai F2+."""
    from . import idem_buat as _ib
    kode_aksi = f"BULK_{aksi.upper()}"
    hasil = []
    for i in ids:
        item = {"id": str(i), "number": (nomor or {}).get(str(i)), "status": "ok", "http": 200, "code": None, "message": None, "replay": False, "result": None}
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    kp, sd, lama = await _ib.mulai_aksi(conn, ctx, kunci_batch, kode_aksi, i, {"aksi": aksi, "payload": payload or {}})
                    if lama is not None:
                        item["replay"], item["result"] = True, lama
                    else:
                        resp = await fn(conn, ctx, i)
                        item["result"] = await _ib.simpan(conn, ctx, kp, sd, kode_aksi, resp, i)
        except HTTPException as e:
            kode, pesan = _pesan(e)
            item.update(status="rejected" if 400 <= e.status_code < 500 else "error", http=e.status_code, code=kode,
                        message=pesan, result=None)
        except Exception:
            logger.error("bulk %s item %s gagal", kode_aksi, i, exc_info=True)
            item.update(status="error", http=500, code="BULK_ITEM_GALAT", message="Item ini gagal diproses.", result=None)
        hasil.append(item)
    ok = sum(1 for h in hasil if h["status"] == "ok")
    rejected = sum(1 for h in hasil if h["status"] == "rejected")
    async with pool.acquire() as conn:
        await catat_audit(conn, ctx, aksi, modul, ids, {"ok": ok, "rejected": rejected, "error": len(hasil) - ok - rejected})
    return {"action": aksi, "total": len(hasil), "ok_count": ok, "rejected_count": rejected,
            "error_count": len(hasil) - ok - rejected, "all_ok": ok == len(hasil), "items": hasil}
