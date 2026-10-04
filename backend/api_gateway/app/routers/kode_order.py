"""Kode order: pengaturan per tenant + ganti manual (V359; 2 Okt 2026, pemilik + MASTER).

GET  /api/settings/order-codes          baca (tenant_settings R; ADMIN boleh baca)
PUT  /api/settings/order-codes          ubah (tenant_settings U + PEMILIK saja, dicek di sini dari DB)
POST /api/settings/order-codes/preview  contoh kode + peringatan, NOL tulis
PATCH /api/sales-orders/{id}/order-code ganti manual (allow_override + OWNER/ADMIN, dicek di sini dari DB)
                                        {order_code} = ketik; {mode: "next"} = nomor berikutnya dari penghitung
Penerbitan otomatis TIDAK lewat sini: services/kode_order.terbitkan dipanggil dari inti uang masuk.
"""
import logging
import uuid as uuid_module
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from ..services import kode_order as KO
from ..services.db_pool import get_db_pool

logger = logging.getLogger(__name__)
router = APIRouter()


def _ctx(request: Request) -> dict:
    u = getattr(request.state, "user", None) or {}
    if not u.get("tenant_id"):
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"tenant_id": u["tenant_id"], "user_id": u.get("user_id") or u.get("id")}


def _keluaran(s: dict) -> dict:
    return {k: s[k] for k in ("enabled", "template", "min_digits", "reset", "trigger", "allow_override", "label")}


def _badan(b: dict, lama: dict) -> dict:
    s = dict(lama)
    for k in ("enabled", "template", "min_digits", "reset", "trigger", "allow_override", "label"):
        if k in b:
            s[k] = b[k]
    s["label"] = KO.normal_label(s.get("label") or KO.BAWAAN["label"])
    for k in ("enabled", "allow_override"):
        if not isinstance(s[k], bool):
            raise KO.KodeOrderGalat(f"{k} wajib boolean.")
    KO.validasi_setelan(s["template"], s["min_digits"], s["reset"], s["trigger"], s["allow_override"])
    return s


def _peringatan(lama: dict, baru: dict, tgl: date) -> list:
    w = []
    if baru["enabled"] and not lama["enabled"]:
        w.append("Pesanan yang sudah dikonfirmasi sebelum fitur ini dinyalakan tidak mendapat kode otomatis; "
                 "isi lewat impor atau ganti manual.")
    if lama["enabled"] and (lama["template"], lama["min_digits"]) != (baru["template"], baru["min_digits"]):
        a = KO.render(lama["template"], 1, lama["min_digits"], tgl)
        b = KO.render(baru["template"], 1, baru["min_digits"], tgl)
        if a != b:
            w.append(f"Bentuk kode berikutnya berubah (contoh {a} → {b}); kode yang sudah terbit tidak berubah.")
    if lama["enabled"] and lama["reset"] != baru["reset"]:
        w.append("Aturan reset berubah: penomoran berikutnya mengikuti periode baru.")
    return w


@router.get("/settings/order-codes")
async def baca_setelan(request: Request):
    ctx = _ctx(request)
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return {"success": True, "data": _keluaran(await KO.muat_setelan(conn, ctx["tenant_id"]))}


@router.post("/settings/order-codes/preview")
async def pratinjau_setelan(request: Request):
    """Contoh kode dari setelan yang SEDANG diisi (belum disimpan) + peringatan perubahan. Nol tulis."""
    ctx = _ctx(request)
    b = await request.json() if await request.body() else {}
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        lama = await KO.muat_setelan(conn, ctx["tenant_id"])
        from ..utils.tanggal_tenant import tanggal_dokumen
        hari = await tanggal_dokumen(conn, ctx["tenant_id"])
    try:
        baru = _badan(b or {}, lama)
        tgl = date.fromisoformat(b["date"]) if b.get("date") else hari
    except (KO.KodeOrderGalat, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"success": True, "data": {"example": KO.render(baru["template"], 1, baru["min_digits"], tgl),
                                      "warnings": _peringatan(lama, baru, tgl)}}


@router.put("/settings/order-codes")
async def simpan_setelan(request: Request):
    ctx = _ctx(request)
    b = await request.json()
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        from ..services.role_resolution import try_resolve_business_role
        if await try_resolve_business_role(conn, str(ctx["user_id"]), ctx["tenant_id"]) != "OWNER":
            _lbl = KO.label_kalimat((await KO.muat_setelan(conn, ctx["tenant_id"]))["label"])
            raise HTTPException(status_code=403, detail=f"Hanya pemilik usaha yang dapat mengubah pengaturan {_lbl}.")
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"ORDER_CODE_SETTINGS:{ctx['tenant_id']}")
            lama = await KO.muat_setelan(conn, ctx["tenant_id"])
            try:
                baru = _badan(b or {}, lama)
            except KO.KodeOrderGalat as e:
                raise HTTPException(status_code=422, detail=str(e))
            await conn.execute(
                """INSERT INTO order_code_settings (tenant_id, enabled, template, min_digits, reset, trigger,
                                                    allow_override, label, updated_at, updated_by)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now(), $9)
                   ON CONFLICT (tenant_id) DO UPDATE SET enabled = EXCLUDED.enabled, template = EXCLUDED.template,
                     min_digits = EXCLUDED.min_digits, reset = EXCLUDED.reset, trigger = EXCLUDED.trigger,
                     allow_override = EXCLUDED.allow_override, label = EXCLUDED.label, updated_at = now(),
                     updated_by = EXCLUDED.updated_by""",
                ctx["tenant_id"], baru["enabled"], baru["template"], baru["min_digits"], baru["reset"],
                baru["trigger"], baru["allow_override"], baru["label"], str(ctx["user_id"]))
    return {"success": True, "data": _keluaran(baru)}


@router.patch("/sales-orders/{order_id}/order-code")
async def ganti_kode_order(request: Request, order_id: str):
    ctx = _ctx(request)
    try:
        so_id = uuid_module.UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Sales order not found")
    b = await request.json()
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        if not await KO.boleh_ganti_kode(conn, ctx["tenant_id"], ctx["user_id"]):
            _lbl = (await KO.muat_setelan(conn, ctx["tenant_id"]))["label"]
            raise HTTPException(status_code=403, detail=f"{_lbl} tidak dapat diganti manual untuk peran/pengaturan ini.")
        mode = (b or {}).get("mode") or "manual"
        if mode not in ("manual", "next"):
            raise HTTPException(status_code=422, detail="mode harus 'manual' atau 'next'.")
        async with conn.transaction():
            try:
                if mode == "next":
                    from ..utils.tanggal_tenant import tanggal_dokumen
                    h = await KO.terbitkan_berikutnya(conn, ctx["tenant_id"], so_id,
                                                      await tanggal_dokumen(conn, ctx["tenant_id"]), ctx["user_id"])
                else:
                    h = await KO.ganti_kode(conn, ctx["tenant_id"], so_id, (b or {}).get("order_code"),
                                            ctx["user_id"])
            except KO.KodeOrderGalat as e:
                raise HTTPException(status_code=422, detail=str(e))
    if h["status"] == 404:
        raise HTTPException(status_code=404, detail="Sales order not found")
    if h["status"] == 409:
        async with pool.acquire() as _c:
            _lbl = (await KO.muat_setelan(_c, ctx["tenant_id"]))["label"]
        raise HTTPException(status_code=409, detail=(f"Pesanan sudah memakai {KO.label_kalimat(_lbl)} {h['order_code']}."
                                                     if mode == "next" else f"{_lbl} sudah dipakai pesanan lain."))
    return {"success": True, "data": {k: v for k, v in h.items() if k != "status"}}


@router.post("/settings/order-codes/import")
async def impor_kode_order(request: Request):
    """Impor kode yang sudah dipakai di luar MilkyHoop (tenant mana pun). PEMILIK saja. {rows:[{order_number |
    customer_name+order_date, order_code, order_title?}], dry_run}. dry_run WAJIB dikirim eksplisit (bawaan true):
    pratinjau dulu, terapkan dengan dry_run=false. Terapkan = semua-atau-tidak (ada galat -> 422 + laporan)."""
    ctx = _ctx(request)
    b = await request.json()
    dry_run = (b or {}).get("dry_run", True) is not False
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        from ..services.role_resolution import try_resolve_business_role
        if await try_resolve_business_role(conn, str(ctx["user_id"]), ctx["tenant_id"]) != "OWNER":
            _lbl = KO.label_kalimat((await KO.muat_setelan(conn, ctx["tenant_id"]))["label"])
            raise HTTPException(status_code=403, detail=f"Hanya pemilik usaha yang dapat mengimpor {_lbl}.")
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"ORDER_CODE_IMPORT:{ctx['tenant_id']}")
            try:
                h = await KO.impor(conn, ctx["tenant_id"], (b or {}).get("rows"), dry_run, ctx["user_id"])
            except KO.KodeOrderGalat as e:
                raise HTTPException(status_code=422, detail=str(e))
    if not dry_run and not h["applied"]:
        raise HTTPException(status_code=422, detail={"code": "IMPORT_HAS_ERRORS",
                                                     "message": "Impor dibatalkan: ada baris bermasalah.", "report": h})
    return {"success": True, "data": h}
