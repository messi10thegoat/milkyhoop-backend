"""Buat penawaran ATOMIK dengan master baru (paket v2 03 §3, 1 Nov 2026): pelanggan baru + barang baru + penawaran dalam SATU
transaksi. Gagal satu = semua batal. Kunci idempotensi WAJIB, satu per draf (Law 14): kirim ulang draf yang sama = penawaran yang
sama; isi beda dengan kunci sama = 409.

Bentuk badan mengikuti draf FE (03 §1 newMasters): master_baru[{kind, temp_id, data}] + penawaran (badan POST /quotes) yang
merujuk master baru lewat temp_id di customer_id / items[].item_id. Aturan tiap master = jalur tulis yang SAMA dengan POST
/customers, POST /items, POST /quotes (tulis_pelanggan_baru, tulis_barang_baru, buat_penawaran_inti) -- tidak ada aturan kedua."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

PREFIX_IDEM = "MB2_QUOTE_CREATE"  # idempotency_keys.key VARCHAR(255): 16 + 1 + uuid 36 + 1 + kunci klien maks 200 = 254
JALUR_IZIN = {"customer": "/api/customers", "item": "/api/items"}


class MasterBaru(BaseModel):
    kind: Literal["customer", "item"]
    temp_id: str = Field(..., min_length=1, max_length=64)
    data: dict


class BuatPenawaranV2(BaseModel):
    master_baru: list[MasterBaru] = Field(default_factory=list, max_length=50)
    penawaran: dict


def _galat_422(code: str, message: str, **lain) -> HTTPException:
    return HTTPException(status_code=422, detail={"code": code, "message": message, **lain})


def _validasi(model, data: dict, letak: str):
    """pydantic -> 422 DATA_TIDAK_SAH {letak, errors} (bukan 500); dipanggil SEBELUM tulis apa pun."""
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise _galat_422("DATA_TIDAK_SAH", "Data tidak sah", letak=letak,
                         errors=[{"field": ".".join(str(p) for p in x.get("loc", ())), "message": x.get("msg")}
                                 for x in e.errors()])


def rencanakan(body: BuatPenawaranV2) -> tuple:
    """Periksa badan TANPA DB. -> (pelanggan [(temp_id, CreateCustomerRequest)], barang [(temp_id, CreateItemRequest)]).
    temp_id unik; maks 1 pelanggan baru; tiap master baru WAJIB dirujuk penawaran (tak ada master yatim);
    penawaran sah dengan id pengganti (galat skema = 422 sebelum transaksi)."""
    from ...routers.items import periksa_badan_item, tanpa_stok_bila_bukan_barang
    from ...schemas.customers import CreateCustomerRequest
    from ...schemas.items import CreateItemRequest
    from ...schemas.quotes import CreateQuoteRequest

    ids = [m.temp_id for m in body.master_baru]
    if len(set(ids)) != len(ids):
        raise _galat_422("TEMP_ID_GANDA", "temp_id master baru harus unik")
    pelanggan = [(m.temp_id, _validasi(CreateCustomerRequest, m.data, f"master_baru.{m.temp_id}"))
                 for m in body.master_baru if m.kind == "customer"]
    if len(pelanggan) > 1:
        raise _galat_422("PELANGGAN_BARU_LEBIH_DARI_SATU", "Satu penawaran hanya untuk satu pelanggan")
    barang = []
    for m in body.master_baru:
        if m.kind == "item":
            b = _validasi(CreateItemRequest, m.data, f"master_baru.{m.temp_id}")
            tanpa_stok_bila_bukan_barang(b)
            periksa_badan_item(b, "POST /milkybook/v2/penawaran")
            barang.append((m.temp_id, b))

    p = body.penawaran
    baris = [i for i in (p.get("items") or []) if isinstance(i, dict)]
    yatim = [t for t, _ in pelanggan if p.get("customer_id") != t]
    yatim += [t for t, _ in barang if not any(i.get("item_id") == t for i in baris)]
    if yatim:
        raise _galat_422("MASTER_BARU_TAK_DIRUJUK", "Master baru tidak dipakai di penawaran", temp_id=yatim)
    salah = [t for t, _ in barang if p.get("customer_id") == t]
    salah += [i.get("item_id") for i in baris if i.get("item_id") in {t for t, _ in pelanggan}]
    if salah:
        raise _galat_422("TEMP_ID_SALAH_TEMPAT", "temp_id pelanggan hanya di customer_id, temp_id barang hanya di baris",
                         temp_id=salah)
    _validasi(CreateQuoteRequest, ganti_rujukan(p, {t: str(uuid.uuid4()) for t in ids}, pelanggan), "penawaran")
    return pelanggan, barang


def ganti_rujukan(penawaran: dict, peta: dict, pelanggan: list) -> dict:
    """Salinan badan penawaran dengan temp_id -> id nyata (tempat sudah diperiksa rencanakan); nama pelanggan baru mengisi
    customer_name bila kosong."""
    p = dict(penawaran)
    if p.get("customer_id") in peta:
        p["customer_id"] = peta[p["customer_id"]]
        if not p.get("customer_name") and pelanggan:
            p["customer_name"] = pelanggan[0][1].name
    p["items"] = [dict(i, item_id=peta[i["item_id"]]) if isinstance(i, dict) and i.get("item_id") in peta else i
                  for i in (p.get("items") or [])]
    return p


async def periksa_izin(izin_fn, user: dict, pelanggan: list, barang: list) -> None:
    """Master baru = izin BUAT modulnya juga (rute ini dipetakan quote C; pelanggan/barang dicek di sini, peta rute yang sama)."""
    perlu = ([JALUR_IZIN["customer"]] if pelanggan else []) + ([JALUR_IZIN["item"]] if barang else [])
    if not perlu:
        return
    hasil = await izin_fn(user, perlu, "POST")
    ditolak = [j for j in perlu if not hasil.get(j)]
    if ditolak:
        raise HTTPException(status_code=403, detail={"code": "IZIN_MASTER_BARU", "message": "Tidak punya izin membuat data ini",
                                                     "jalur": ditolak})


async def buat(conn, user: dict, body: BuatPenawaranV2, kunci: str, *, izin_fn, response=None) -> dict:
    """Satu transaksi: idem (lock + replay) -> pelanggan baru -> barang baru -> penawaran -> catat respons."""
    from ...routers.customers import tulis_pelanggan_baru
    from ...routers.items import galat_bentrok_unik, tulis_barang_baru
    from ...routers.quotes import buat_penawaran_inti
    from ...schemas.quotes import CreateQuoteRequest
    from ...services import idem_buat

    if not kunci:
        raise _galat_422("IDEMPOTENCY_KEY_WAJIB", "Kunci idempotensi per draf wajib (header X-Idempotency-Key)")
    pelanggan, barang = rencanakan(body)
    await periksa_izin(izin_fn, user, pelanggan, barang)
    ctx = {"tenant_id": user["tenant_id"], "user_id": user["user_id"]}
    pelaku = (user.get("user_id"), user.get("username") or user.get("email"))

    async with conn.transaction():
        kp, sd, lama = await idem_buat.mulai(conn, ctx, kunci, PREFIX_IDEM, body, "quotes", "quote", response)
        if lama is not None:
            return lama
        peta, dibuat = {}, []
        for temp_id, b in pelanggan:
            peta[temp_id] = str(await tulis_pelanggan_baru(conn, ctx, b, *pelaku))
            dibuat.append({"kind": "customer", "temp_id": temp_id, "id": peta[temp_id]})
        for temp_id, b in barang:
            try:
                peta[temp_id] = str(await tulis_barang_baru(conn, ctx, b, *pelaku))
            except HTTPException:
                raise
            except Exception as e:  # unik DB (nama/kode balapan) -> 409 yang sama dengan POST /items
                galat = galat_bentrok_unik(e, b)
                if galat:
                    raise galat from e
                raise
            dibuat.append({"kind": "item", "temp_id": temp_id, "id": peta[temp_id]})
        q = CreateQuoteRequest.model_validate(ganti_rujukan(body.penawaran, peta, pelanggan))
        quote_id, data = await buat_penawaran_inti(conn, ctx, q)
        return await idem_buat.simpan(conn, ctx, kp, sd, PREFIX_IDEM, {
            "success": True, "message": "Penawaran dibuat", "data": {**data, "master_baru": dibuat}}, quote_id)
