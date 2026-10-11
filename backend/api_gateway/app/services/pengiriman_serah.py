"""Serah-terima Surat Jalan: cara serah, alamat kirim, resi BANYAK (11 Okt 2026, kontrak
backend/docs/KONTRAK-pengiriman-serah-20261011.md, disetujui MASTER). Pola NetSuite Item Fulfillment / SAP Delivery.

NOL BUKU (Law 1/16/4/20 tak disentuh): modul ini hanya membaca/menulis `metode_serah`, `pengiriman_serah`,
`pengiriman_resi`, `pengiriman_preferensi` + audit_logs. Tidak ada jurnal, inventory_ledger, warehouse_stock, HPP;
`invoice_fulfillments` TIDAK pernah di-UPDATE (trg_so_shipped_recompute AFTER UPDATE per baris akan terpancing).

Flag `pengiriman_serah` (tenant_features): tanpa flag, field `serah` di badan -> 409 SERAH_NONAKTIF (bukan diabaikan
diam -- Law 34) dan respons GET tak membawa kunci serah (tenant tanpa flag byte-identik).

Snapshot (pola default_dokumen): Surat Jalan MENYALIN nama/jenis cara serah dan alamat saat dibuat; master diubah
kemudian tak menyentuh dokumen lama. Law 24: tenant_id eksplisit di tiap SQL (gateway BYPASSRLS, bukan pagar).
Law 23: ditulis di transaksi PEMANGGIL (satu gagal = nol tulisan)."""
from datetime import date
from typing import Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field

FLAG = "pengiriman_serah"
JENIS = ("kurir", "antar_sendiri", "ambil_sendiri")
MAKS_RESI = 20
KODE_NONAKTIF = "SERAH_NONAKTIF"
KODE_TIDAK_SAH = "SERAH_TIDAK_SAH"
SUMBER_ALAMAT = ("pesanan", "pelanggan", "manual")


class ResiIn(BaseModel):
    nomor: str = Field(..., max_length=100)
    kurir: Optional[str] = Field(None, max_length=100)


class SerahIn(BaseModel):
    metode_serah_id: Optional[str] = None      # kosong -> bawaan tenant (pengiriman_preferensi)
    kirim_ke: Optional[str] = Field(None, max_length=500)  # kosong -> pesanan -> pelanggan; ambil_sendiri: tak dipakai
    resi: List[ResiIn] = Field(default_factory=list, max_length=MAKS_RESI)
    diterima_pada: Optional[date] = None       # stempel saja (K3): tanpa mesin status, tanpa jurnal


def galat_nonaktif() -> HTTPException:
    return HTTPException(status_code=409, detail={
        "code": KODE_NONAKTIF, "message": "Serah-terima pengiriman belum diaktifkan untuk usaha ini."})


def _tidak_sah(pesan: str) -> HTTPException:
    return HTTPException(status_code=422, detail={"code": KODE_TIDAK_SAH, "message": pesan})


def pesan_galat(e: HTTPException) -> str:
    d = e.detail
    return d.get("message") if isinstance(d, dict) else str(d)


async def aktif(conn, tenant_id: str) -> bool:
    return bool(await conn.fetchval(
        "SELECT 1 FROM tenant_features WHERE tenant_id = $1 AND feature = $2 AND enabled", tenant_id, FLAG))


async def pastikan_aktif(conn, tenant_id: str) -> None:
    if not await aktif(conn, tenant_id):
        raise galat_nonaktif()


async def preferensi(conn, tenant_id: str) -> dict:
    r = await conn.fetchrow(
        "SELECT default_lokasi_kirim_id, default_metode_serah_id FROM pengiriman_preferensi WHERE tenant_id = $1",
        tenant_id)
    return {"lokasi_id": r["default_lokasi_kirim_id"] if r else None,
            "metode_id": r["default_metode_serah_id"] if r else None}


async def ambil_metode(conn, tenant_id: str, metode_id) -> Optional[dict]:
    """Baris metode_serah milik tenant (None bila tak ada). id bukan UUID -> None (bukan 500)."""
    try:
        u = UUID(str(metode_id))
    except (ValueError, TypeError):
        return None
    r = await conn.fetchrow(
        "SELECT id, nama, jenis, aktif, urutan FROM metode_serah WHERE id = $1 AND tenant_id = $2", u, tenant_id)
    return dict(r) if r else None


async def alamat_bawaan(conn, tenant_id: str, so_id) -> tuple:
    """(alamat, sumber): alamat kirim pesanan -> alamat pelanggan -> (None, None)."""
    if so_id is None:
        return None, None
    r = await conn.fetchrow(
        "SELECT shipping_address, customer_id FROM sales_orders WHERE id = $1 AND tenant_id = $2", so_id, tenant_id)
    if not r:
        return None, None
    a = (r["shipping_address"] or "").strip()
    if a:
        return a, "pesanan"
    if r["customer_id"]:
        c = await conn.fetchval(
            "SELECT alamat FROM customers WHERE id = $1 AND tenant_id = $2", r["customer_id"], tenant_id)
        c = (c or "").strip()
        if c:
            return c, "pelanggan"
    return None, None


async def selesaikan(conn, tenant_id: str, so_id, body: Optional[SerahIn], pakai_bawaan: bool = True) -> dict:
    """Validasi + bawaan -> bentuk ternormalisasi. Galat = HTTPException 422 SERAH_TIDAK_SAH. Nol tulisan.
    Aturan: ambil_sendiri -> tanpa kirim_ke & resi; antar_sendiri -> tanpa resi; resi hanya bila jenis kurir."""
    body = body or SerahIn()
    metode = None
    if body.metode_serah_id:
        metode = await ambil_metode(conn, tenant_id, body.metode_serah_id)
        if not metode:
            raise _tidak_sah("Cara serah tidak dikenal.")
        if not metode["aktif"]:
            raise _tidak_sah(f"Cara serah {metode['nama']} tidak aktif.")
    elif pakai_bawaan:  # PUT ganti-seluruhnya memakai False: kosong = tanpa cara serah, bukan bawaan lagi
        bawaan = (await preferensi(conn, tenant_id))["metode_id"]
        if bawaan:
            m = await ambil_metode(conn, tenant_id, bawaan)
            if m and m["aktif"]:  # bawaan nonaktif/hilang -> dilewati (FE menampilkan "belum diatur"), bukan galat
                metode = m
    jenis = metode["jenis"] if metode else None

    resi, lihat = [], set()
    for r in body.resi:
        nomor = (r.nomor or "").strip()
        if not nomor:
            raise _tidak_sah("Nomor resi tidak boleh kosong.")
        if nomor.lower() in lihat:
            raise _tidak_sah(f"Nomor resi {nomor} dobel.")
        lihat.add(nomor.lower())
        resi.append({"nomor": nomor, "kurir": (r.kurir or "").strip() or None})
    if len(resi) > MAKS_RESI:
        raise _tidak_sah(f"Resi maksimal {MAKS_RESI} per Surat Jalan.")
    if resi and jenis != "kurir":
        raise _tidak_sah("Nomor resi hanya untuk cara serah kurir." if jenis else
                         "Pilih cara serah kurir untuk mengisi nomor resi.")

    diminta = (body.kirim_ke or "").strip()
    if jenis == "ambil_sendiri":
        if diminta:
            raise _tidak_sah("Cara serah ambil sendiri tidak memakai alamat kirim.")
        kirim_ke = sumber = None
    elif diminta:
        kirim_ke, sumber = diminta, "manual"
    else:
        kirim_ke, sumber = await alamat_bawaan(conn, tenant_id, so_id)
    return {
        "metode": {"id": str(metode["id"]), "nama": metode["nama"], "jenis": metode["jenis"]} if metode else None,
        "kirim_ke": kirim_ke, "kirim_ke_sumber": sumber, "resi": resi, "diterima_pada": body.diterima_pada,
    }


async def tulis(conn, tenant_id: str, user_id, fulfillment_id, s: dict) -> None:
    """Ganti-seluruhnya serah + resi satu Surat Jalan. Di transaksi pemanggil. TIDAK menyentuh invoice_fulfillments."""
    fid = UUID(str(fulfillment_id))
    m = s["metode"]
    uid = UUID(str(user_id)) if user_id else None
    await conn.execute(
        """INSERT INTO pengiriman_serah (fulfillment_id, tenant_id, metode_serah_id, metode_nama, jenis_serah,
                                         kirim_ke, kirim_ke_sumber, diterima_pada, diubah_oleh)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
           ON CONFLICT (fulfillment_id) DO UPDATE SET
               metode_serah_id = EXCLUDED.metode_serah_id, metode_nama = EXCLUDED.metode_nama,
               jenis_serah = EXCLUDED.jenis_serah, kirim_ke = EXCLUDED.kirim_ke,
               kirim_ke_sumber = EXCLUDED.kirim_ke_sumber, diterima_pada = EXCLUDED.diterima_pada,
               diubah_at = now(), diubah_oleh = EXCLUDED.diubah_oleh
           WHERE pengiriman_serah.tenant_id = EXCLUDED.tenant_id""",
        fid, tenant_id, UUID(m["id"]) if m else None, m["nama"] if m else None, m["jenis"] if m else None,
        s["kirim_ke"], s["kirim_ke_sumber"], s["diterima_pada"], uid)
    await conn.execute("DELETE FROM pengiriman_resi WHERE fulfillment_id = $1 AND tenant_id = $2", fid, tenant_id)
    for i, r in enumerate(s["resi"]):
        await conn.execute(
            """INSERT INTO pengiriman_resi (tenant_id, fulfillment_id, nomor_resi, kurir_nama, urutan)
               VALUES ($1, $2, $3, $4, $5)""", tenant_id, fid, r["nomor"], r["kurir"], i)


def bentuk(s: dict) -> dict:
    """Bentuk respons dari hasil `selesaikan` (belum/baru ditulis)."""
    return {"metode": s["metode"], "kirim_ke": s["kirim_ke"], "kirim_ke_sumber": s["kirim_ke_sumber"],
            "diterima_pada": s["diterima_pada"].isoformat() if s["diterima_pada"] else None,
            "resi": [{"nomor": r["nomor"], "kurir": r["kurir"]} for r in s["resi"]]}


async def muat(conn, tenant_id: str, fulfillment_ids: list) -> Dict[str, dict]:
    """id Surat Jalan -> {metode, kirim_ke, kirim_ke_sumber, diterima_pada, resi[], lampiran_jumlah}. Selalu ada
    entri untuk tiap id (kosong bila belum ada serah) supaya FE tak menebak."""
    ids = [UUID(str(i)) for i in fulfillment_ids]
    if not ids:
        return {}
    serah = {r["fulfillment_id"]: r for r in await conn.fetch(
        """SELECT fulfillment_id, metode_serah_id, metode_nama, jenis_serah, kirim_ke, kirim_ke_sumber, diterima_pada
           FROM pengiriman_serah WHERE tenant_id = $1 AND fulfillment_id = ANY($2::uuid[])""", tenant_id, ids)}
    resi: Dict[UUID, list] = {}
    for r in await conn.fetch(
            """SELECT fulfillment_id, nomor_resi, kurir_nama FROM pengiriman_resi
               WHERE tenant_id = $1 AND fulfillment_id = ANY($2::uuid[]) ORDER BY urutan, created_at""",
            tenant_id, ids):
        resi.setdefault(r["fulfillment_id"], []).append({"nomor": r["nomor_resi"], "kurir": r["kurir_nama"]})
    lamp = {r["entity_id"]: r["n"] for r in await conn.fetch(
        """SELECT da.entity_id, count(*) AS n FROM document_attachments da
           JOIN documents d ON d.id = da.document_id AND d.tenant_id = da.tenant_id
           WHERE da.tenant_id = $1 AND da.entity_type = 'delivery' AND da.entity_id = ANY($2::uuid[])
             AND d.deleted_at IS NULL GROUP BY da.entity_id""", tenant_id, ids)}
    out = {}
    for i in ids:
        r = serah.get(i)
        out[str(i)] = {
            "metode": ({"id": str(r["metode_serah_id"]) if r["metode_serah_id"] else None,
                        "nama": r["metode_nama"], "jenis": r["jenis_serah"]}
                       if r and (r["metode_nama"] or r["jenis_serah"]) else None),
            "kirim_ke": r["kirim_ke"] if r else None,
            "kirim_ke_sumber": r["kirim_ke_sumber"] if r else None,
            "diterima_pada": r["diterima_pada"].isoformat() if r and r["diterima_pada"] else None,
            "resi": resi.get(i, []),
            "lampiran_jumlah": int(lamp.get(i, 0)),
        }
    return out
