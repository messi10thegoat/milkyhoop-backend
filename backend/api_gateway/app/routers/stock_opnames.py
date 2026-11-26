"""
Stok Opname bernomor (V509; putusan pemilik LANGSUNG 24 Nov 2026; flag gelap `stok_opname_v1`).

Alur (ikut Accurate): DRAFT -> HITUNG (gudang TERKUNCI) -> POSTED -> VOID; HITUNG -> BATAL; DRAFT -> hapus.
Opname TIDAK menulis jurnal/ledger sendiri: POST membuat SATU Penyesuaian Stok `recount` lewat penulis SA yang sama
(`stock_adjustments._buat_draf` + `_tulis_posting`), VOID = `stock_adjustments._tulis_void` (kaskade lengkap, Law 2/26/31).
Rancangan: rantai/RANCANGAN-stok-opname-20261124.md.

Endpoints: GET "" | GET /{id} | POST /preview | POST "" | DELETE /{id} | PUT /{id}/baris | POST /{id}/mulai(/preview)
           POST /{id}/post(/preview) | POST /{id}/batal(/preview) | POST /{id}/void(/preview)
"""
import logging
import uuid as uuid_module
from datetime import date
from decimal import Decimal
from typing import List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from . import stock_adjustments as SA
from ..schemas.stock_adjustments import CreateStockAdjustmentRequest, StockAdjustmentItemCreate
from ..services import idem_buat
from ..services import stok_opname_tampil as TP
from ..services import stok_sesuai as SS
from ..services.bayar_keluar_rencana import Blok
from ..services.galat_berkode import GalatBerkode
from ..services.tenant_features import fitur_aktif

logger = logging.getLogger(__name__)
router = APIRouter()

FLAG = "stok_opname_v1"
NOL = Decimal(0)
STATUS_TEKS = {"draft": "Draf", "hitung": "Sedang dihitung", "posted": "Selesai", "void": "Dibatalkan (void)", "batal": "Dibatalkan"}


# ------------------------------------------------------------------ model
class BuatOpname(BaseModel):
    warehouse_id: str
    opname_date: date
    reference_no: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None
    cakupan: Literal["semua", "daftar"] = "semua"
    product_ids: Optional[List[str]] = None

    def model_dump_idem(self):
        return self.model_dump(mode="json")


class BarisHitung(BaseModel):
    product_id: str
    physical_quantity: Optional[Decimal] = None
    note: Optional[str] = Field(None, max_length=500)
    hapus: bool = False


class IsiBaris(BaseModel):
    baris: List[BarisHitung] = Field(..., min_length=1)


class Alasan(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class AlasanOpsional(BaseModel):
    reason: Optional[str] = Field(None, max_length=500)


# ------------------------------------------------------------------ pembantu
async def _ctx(request: Request) -> dict:
    ctx = SA.get_user_context(request)
    if not ctx["user_id"]:
        raise HTTPException(status_code=401, detail="ID pengguna wajib ada.")
    return ctx


async def _flag(conn, tid: str) -> None:
    if FLAG not in await fitur_aktif(conn, tid):
        raise GalatBerkode(404, "FITUR_BELUM_AKTIF", "Stok Opname belum diaktifkan untuk usaha ini.")


async def _muat(conn, tid: str, oid, kunci: bool = False):
    return await conn.fetchrow("SELECT * FROM stock_opnames WHERE id = $1 AND tenant_id = $2" + (" FOR UPDATE" if kunci else ""), oid, tid)


async def _baris(conn, oid):
    return await conn.fetch("SELECT * FROM stock_opname_lines WHERE opname_id = $1 ORDER BY line_number, created_at", oid)


async def _stok_banyak(conn, tid: str, gid, pids: list) -> dict:
    """Σ masuk − Σ keluar ledger per barang di SATU gudang (Law 16; BUKAN cache warehouse_stock yang terpangkas 0)."""
    if not pids:
        return {}
    rows = await conn.fetch(
        "SELECT product_id, COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) AS q FROM inventory_ledger "
        "WHERE tenant_id = $1 AND warehouse_id = $2 AND product_id = ANY($3::uuid[]) GROUP BY product_id", tid, gid, pids)
    d = {r["product_id"]: Decimal(str(r["q"])) for r in rows}
    return {p: d.get(p, NOL) for p in pids}


_FILTER_BARANG = ("p.tenant_id = $1 AND p.deleted_at IS NULL AND p.item_type = 'goods' AND COALESCE(p.track_inventory, true) "
                  "AND NOT COALESCE(p.track_batches, false) AND NOT COALESCE(p.track_serial, false)")


async def _kandidat(conn, tid: str, gid, cakupan: str, product_ids, bl: Blok) -> list:
    """Barang yang dihitung. semua = barang ber-stok yang pernah bergerak di gudang itu; daftar = pilihan pengguna.
    Barang ber-batch/serial TIDAK ikut v1 (SA belum menerima baris ber-batch; putusan pemilik 24 Nov: serial di luar v1)."""
    if cakupan == "daftar":
        try:
            ids = [UUID(str(x)) for x in (product_ids or [])]
        except (ValueError, TypeError):
            bl.galat("OPNAME_BARANG_TIDAK_VALID", 400, "Daftar barang memuat ID yang tidak valid.")
            return []
        if not ids:
            bl.galat("OPNAME_DAFTAR_KOSONG", 422, "Pilih minimal satu barang untuk dihitung.")
            return []
        rows = await conn.fetch(
            f"SELECT p.id, p.item_code, p.nama_produk, COALESCE(NULLIF(p.base_unit, ''), p.satuan) AS unit FROM products p "
            f"WHERE {_FILTER_BARANG} AND p.id = ANY($2::uuid[]) ORDER BY lower(p.nama_produk), p.id", tid, ids)
        if len(rows) != len(set(ids)):
            bl.galat("OPNAME_BARANG_TIDAK_DIDUKUNG", 409,
                     "Ada barang yang tidak ditemukan, tidak melacak stok, atau memakai batch/serial (belum didukung di Stok Opname).")
            return []
        return list(rows)
    return list(await conn.fetch(
        f"SELECT p.id, p.item_code, p.nama_produk, COALESCE(NULLIF(p.base_unit, ''), p.satuan) AS unit FROM products p "
        f"WHERE {_FILTER_BARANG} AND EXISTS (SELECT 1 FROM inventory_ledger l WHERE l.tenant_id = p.tenant_id AND l.product_id = p.id AND l.warehouse_id = $2) "
        f"ORDER BY lower(p.nama_produk), p.id", tid, gid))


async def _tulis_baris(conn, tid: str, oid, produk: list, mulai_no: int = 1) -> None:
    for i, p in enumerate(produk, mulai_no):
        await conn.execute(
            "INSERT INTO stock_opname_lines (opname_id, tenant_id, product_id, product_code, product_name, unit, line_number) VALUES ($1,$2,$3,$4,$5,$6,$7)",
            oid, tid, p["id"], p["item_code"], p["nama_produk"], p["unit"], i)


def _keluaran_pratinjau(bl: Blok, data: dict) -> dict:
    return jsonable_encoder({"ok": not bl.daftar, "blocks": bl.untuk_respons(), **data})


async def _pratinjau(request: Request, rencana) -> dict:
    ctx = await _ctx(request)
    pool = await SA.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            await _flag(conn, ctx["tenant_id"])
            bl = Blok(True)
            data = await rencana(conn, ctx["tenant_id"], bl)
            return _keluaran_pratinjau(bl, data)


# ------------------------------------------------------------------ penentu (SATU untuk pratinjau DAN tulis)
async def rencana_buat(conn, tid: str, body: BuatOpname, bl: Blok) -> dict:
    g = await SS.gudang(conn, tid, body.warehouse_id, bl)
    if bl.daftar or not g:
        return {"gudang": None, "produk": []}
    produk = await _kandidat(conn, tid, g["id"], body.cakupan, body.product_ids, bl)
    if not bl.daftar and not produk:
        bl.galat("OPNAME_TANPA_BARANG", 422, f"Tidak ada barang ber-stok untuk dihitung di gudang {g['name']}. Pilih barang secara manual.")
    return {"gudang": g, "produk": produk, "jumlah_baris": len(produk)}


async def rencana_mulai(conn, tid: str, oid, bl: Blok, kunci: bool = False) -> dict:
    o = await _muat(conn, tid, oid, kunci)
    if not o:
        bl.galat("OPNAME_TIDAK_DITEMUKAN", 404, "Stok opname tidak ditemukan.")
        return {"opname": None}
    if o["status"] != "draft":
        bl.galat("OPNAME_BUKAN_DRAF", 409, f"Opname {o['opname_number']} berstatus {STATUS_TEKS[o['status']]}; hanya draf yang bisa mulai dihitung.")
        return {"opname": o}
    g = await SS.gudang(conn, tid, o["warehouse_id"], bl)
    if bl.daftar:
        return {"opname": o}
    lain = await conn.fetchval("SELECT opname_number FROM stock_opnames WHERE tenant_id = $1 AND warehouse_id = $2 AND status = 'hitung' AND id <> $3",
                               tid, o["warehouse_id"], oid)
    if lain:
        bl.galat("GUDANG_SUDAH_DIHITUNG", 409, f"Gudang {g['name']} sedang dihitung di opname {lain}; selesaikan atau batalkan dulu.")
        return {"opname": o}
    n = await conn.fetchval("SELECT count(*) FROM stock_opname_lines WHERE opname_id = $1", oid)
    if not n:
        bl.galat("OPNAME_TANPA_BARIS", 422, "Opname belum punya barang untuk dihitung.")
    return {"opname": o, "gudang": g, "jumlah_baris": n,
            "catatan": f"Gudang {g['name']} akan DIKUNCI: tidak ada penjualan, penerimaan, pemindahan, atau penyesuaian lain sampai opname ini selesai atau dibatalkan."}


async def rencana_posting(conn, tid: str, oid, bl: Blok, kunci: bool = False) -> dict:
    o = await _muat(conn, tid, oid, kunci)
    if not o:
        bl.galat("OPNAME_TIDAK_DITEMUKAN", 404, "Stok opname tidak ditemukan.")
        return {"opname": None, "baris": [], "items": []}
    if o["status"] != "hitung":
        bl.galat("OPNAME_BUKAN_HITUNG", 409, f"Opname {o['opname_number']} berstatus {STATUS_TEKS[o['status']]}; hanya yang sedang dihitung yang bisa diposting.")
        return {"opname": o, "baris": [], "items": []}
    await SS.periode(conn, tid, o["opname_date"], bl)
    garis = [b for b in await _baris(conn, oid)]
    pids = [b["product_id"] for b in garis]
    if kunci:  # Law 13 granularitas stok: kunci per (barang, gudang) berurutan -> tak ada pesanan lain yang menyelip di antara ukur dan tulis
        for p in sorted(pids, key=str):
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"STOCK:{p}:{o['warehouse_id']}")
    kini = await _stok_banyak(conn, tid, o["warehouse_id"], pids)
    baris, items, total = [], [], NOL
    for b in garis:
        sekarang = kini.get(b["product_id"], NOL)
        item = {"product_id": str(b["product_id"]), "product_name": b["product_name"], "unit": b["unit"],
                "system_quantity": b["system_quantity"], "stok_sekarang": sekarang, "physical_quantity": b["physical_quantity"],
                "dihitung": b["physical_quantity"] is not None, "selisih": None, "nilai": None, "bergerak_sejak_snapshot": False}
        if b["physical_quantity"] is not None:
            selisih = Decimal(str(b["physical_quantity"])) - sekarang   # selisih dari ledger SAAT POST (putusan pemilik 24 Nov)
            item["selisih"] = selisih
            item["bergerak_sejak_snapshot"] = b["system_quantity"] is not None and Decimal(str(b["system_quantity"])) != sekarang
            if selisih != 0:
                nilai = abs(selisih) * await SS.harga_pokok(conn, tid, b["product_id"])
                item["nilai"] = nilai
                total += nilai if selisih > 0 else -nilai
                items.append((b, selisih))
        baris.append(item)
    tdk = sum(1 for x in baris if not x["dihitung"])
    return {"opname": o, "baris": baris, "items": items, "total_nilai_bertanda": total,
            "belum_dihitung": tdk, "baris_selisih": len(items),
            "catatan": ("Barang yang belum dihitung TIDAK disesuaikan." if tdk else None),
            "tanpa_selisih": not items}


async def rencana_batal(conn, tid: str, oid, bl: Blok, kunci: bool = False) -> dict:
    o = await _muat(conn, tid, oid, kunci)
    if not o:
        bl.galat("OPNAME_TIDAK_DITEMUKAN", 404, "Stok opname tidak ditemukan.")
    elif o["status"] != "hitung":
        bl.galat("OPNAME_BUKAN_HITUNG", 409, f"Opname {o['opname_number']} berstatus {STATUS_TEKS[o['status']]}; hanya yang sedang dihitung yang bisa dibatalkan (draf: hapus).")
    return {"opname": o, "catatan": "Kunci gudang dilepas; tidak ada jurnal atau stok yang berubah."}


async def rencana_void(conn, tid: str, oid, alasan, bl: Blok, kunci: bool = False) -> dict:
    o = await _muat(conn, tid, oid, kunci)
    if not o:
        bl.galat("OPNAME_TIDAK_DITEMUKAN", 404, "Stok opname tidak ditemukan.")
        return {"opname": None, "sa_rencana": None}
    if o["status"] != "posted":
        bl.galat("OPNAME_BUKAN_POSTED", 409, f"Opname {o['opname_number']} berstatus {STATUS_TEKS[o['status']]}; hanya opname selesai yang bisa di-void.")
        return {"opname": o, "sa_rencana": None}
    if not o["adjustment_id"]:
        return {"opname": o, "sa_rencana": None, "catatan": "Opname ini tanpa selisih (tidak ada jurnal); hanya statusnya yang berubah."}
    r = await SS.rencana_batal(conn, tid, o["adjustment_id"], alasan or "Void stok opname", bl, kunci=kunci)
    return {"opname": o, "sa_rencana": r, "catatan": "Penyesuaian Stok tertaut di-void: jurnal pembalik dibuat dan stok dikembalikan ke sebelum opname."}


# ------------------------------------------------------------------ baca
@router.get("")
async def daftar_opname(request: Request, status: Optional[str] = Query(None), warehouse_id: Optional[str] = Query(None),
                        limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    ctx = await _ctx(request)
    pool = await SA.get_pool()
    async with pool.acquire() as conn:
        await _flag(conn, ctx["tenant_id"])
        kond, par = ["o.tenant_id = $1"], [ctx["tenant_id"]]
        if status and status != "all":
            par.append(status); kond.append(f"o.status = ${len(par)}")
        if warehouse_id:
            try:
                par.append(UUID(warehouse_id)); kond.append(f"o.warehouse_id = ${len(par)}")
            except ValueError:
                raise GalatBerkode(400, "GUDANG_TIDAK_VALID", "ID gudang tidak valid.")
        par += [limit, offset]
        rows = await conn.fetch(
            f"SELECT o.id, o.opname_number, o.opname_date, o.status, o.warehouse_id, w.name AS gudang, o.adjustment_id, o.started_at, o.created_at, "
            f"(SELECT count(*) FROM stock_opname_lines l WHERE l.opname_id = o.id) AS jumlah_baris, "
            f"(SELECT count(*) FROM stock_opname_lines l WHERE l.opname_id = o.id AND l.physical_quantity IS NOT NULL) AS sudah_dihitung, "
            f"CASE WHEN o.status = 'hitung' THEN (CURRENT_DATE - o.started_at::date) END AS umur_hari "
            f"FROM stock_opnames o JOIN warehouses w ON w.id = o.warehouse_id WHERE {' AND '.join(kond)} "
            f"ORDER BY o.created_at DESC LIMIT ${len(par) - 1} OFFSET ${len(par)}", *par)
        return jsonable_encoder({"success": True, "data": [
            {**dict(r), "status_teks": STATUS_TEKS[r["status"]], "aksi_tersedia": TP.aksi_tersedia(r["status"], r["jumlah_baris"]),
             "position_text": TP.position_text(r["status"], r["jumlah_baris"], r["sudah_dihitung"], r["umur_hari"])} for r in rows]})


@router.get("/{opname_id}")
async def detail_opname(request: Request, opname_id: UUID):
    ctx = await _ctx(request)
    pool = await SA.get_pool()
    async with pool.acquire() as conn:
        await _flag(conn, ctx["tenant_id"])
        o = await _muat(conn, ctx["tenant_id"], opname_id)
        if not o:
            raise GalatBerkode(404, "OPNAME_TIDAK_DITEMUKAN", "Stok opname tidak ditemukan.")
        g = await conn.fetchval("SELECT name FROM warehouses WHERE id = $1", o["warehouse_id"])
        sa = await conn.fetchrow("SELECT adjustment_number, journal_id, total_value, status FROM stock_adjustments WHERE id = $1 AND tenant_id = $2",
                                 o["adjustment_id"], ctx["tenant_id"]) if o["adjustment_id"] else None
        garis = [dict(b) for b in await _baris(conn, opname_id)]
        selisih_baris, total = await _selisih_tampil(conn, ctx["tenant_id"], o, garis)
        for b in garis:
            b.update(TP.medan_baris(*selisih_baris.get(b["product_id"], (None, None)), b["unit"]))
        sudah = sum(1 for b in garis if b["physical_quantity"] is not None)
        umur = await conn.fetchval("SELECT (CURRENT_DATE - $1::timestamptz::date)", o["started_at"]) if o["status"] == "hitung" and o["started_at"] else None
        return jsonable_encoder({"success": True, "data": {
            **dict(o), "status_teks": STATUS_TEKS[o["status"]], "gudang": g, "terkunci": o["status"] == "hitung",
            "aksi_tersedia": TP.aksi_tersedia(o["status"], len(garis)), "position_text": TP.position_text(o["status"], len(garis), sudah, umur),
            **TP.total_bertanda(total), "penyesuaian": dict(sa) if sa else None, "baris": garis}})


async def _selisih_tampil(conn, tid: str, o, garis: list) -> tuple:
    """-> ({product_id: (selisih bertanda|None, |nilai|)}, total bertanda). hitung = penentu posting (== /post/preview, ledger kini);
    posted/void = angka dokumen Penyesuaian Stok tertaut (qty bertanda + total_value baris); draf/batal = belum ada."""
    if o["status"] == "hitung":
        h = await rencana_posting(conn, tid, o["id"], Blok(True))
        return ({UUID(b["product_id"]): (b["selisih"], b["nilai"]) for b in h["baris"] if b["dihitung"]}, h.get("total_nilai_bertanda", NOL))
    if o["status"] in ("posted", "void") and o["adjustment_id"]:
        rows = await conn.fetch("SELECT product_id, quantity_adjustment, total_value FROM stock_adjustment_items WHERE stock_adjustment_id = $1", o["adjustment_id"])
        d = {r["product_id"]: (Decimal(str(r["quantity_adjustment"])), Decimal(str(r["total_value"]))) for r in rows}
        for b in garis:   # dihitung tapi selisih 0 = tak punya baris SA -> selisih 0
            if b["physical_quantity"] is not None and b["product_id"] not in d:
                d[b["product_id"]] = (NOL, NOL)
        return d, sum((SS.nilai_bertanda(q, v) for q, v in d.values()), NOL)
    return {}, NOL


# ------------------------------------------------------------------ buat / hapus / isi hitung
@router.post("/preview")
async def pratinjau_buat(request: Request, body: BuatOpname):
    async def r(conn, tid, bl):
        h = await rencana_buat(conn, tid, body, bl)
        return {"gudang": {"id": str(h["gudang"]["id"]), "name": h["gudang"]["name"]} if h.get("gudang") else None,
                "jumlah_baris": h.get("jumlah_baris", 0), "barang": [{"product_id": str(p["id"]), "product_name": p["nama_produk"]} for p in h["produk"][:200]]}
    return await _pratinjau(request, r)


@router.post("", status_code=201)
async def buat_opname(request: Request, body: BuatOpname, response: Response = None):
    ctx = await _ctx(request)
    pool = await SA.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _flag(conn, ctx["tenant_id"])
            _kp, _sd, _lama = await idem_buat.mulai(conn, ctx, idem_buat.kunci_dari(request), "OPNAME_CREATE", body, "stock_opnames", "opname", response)
            if _lama is not None:
                return _lama
            h = await rencana_buat(conn, ctx["tenant_id"], body, Blok(False))
            oid = uuid_module.uuid4()
            nomor = await conn.fetchval("SELECT generate_stock_opname_number($1, $2)", ctx["tenant_id"], body.opname_date)
            await conn.execute(
                "INSERT INTO stock_opnames (id, tenant_id, opname_number, warehouse_id, opname_date, reference_no, notes, status, created_by) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,'draft',$8)",
                oid, ctx["tenant_id"], nomor, h["gudang"]["id"], body.opname_date, body.reference_no, body.notes, ctx["user_id"])
            await _tulis_baris(conn, ctx["tenant_id"], oid, h["produk"])
            return await idem_buat.simpan(conn, ctx, _kp, _sd, "OPNAME_CREATE", {
                "success": True, "message": "Stok opname dibuat (draf)",
                "data": {"id": str(oid), "opname_number": nomor, "status": "draft", "jumlah_baris": len(h["produk"])}}, oid)


@router.delete("/{opname_id}")
async def hapus_opname(request: Request, opname_id: UUID):
    ctx = await _ctx(request)
    pool = await SA.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _flag(conn, ctx["tenant_id"])
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"OPNAME:{opname_id}")
            o = await _muat(conn, ctx["tenant_id"], opname_id, True)
            if not o:
                raise GalatBerkode(404, "OPNAME_TIDAK_DITEMUKAN", "Stok opname tidak ditemukan.")
            if o["status"] != "draft":
                raise GalatBerkode(409, "OPNAME_BUKAN_DRAF", "Hanya opname draf yang bisa dihapus; yang sedang dihitung dibatalkan, yang selesai di-void.")
            await conn.execute("DELETE FROM stock_opnames WHERE id = $1 AND tenant_id = $2", opname_id, ctx["tenant_id"])
            return {"success": True, "message": "Draf stok opname dihapus", "data": {"id": str(opname_id), "opname_number": o["opname_number"]}}


@router.put("/{opname_id}/baris")
async def isi_baris(request: Request, opname_id: UUID, body: IsiBaris):
    """Isi hasil hitung (boleh sebagian, berkali-kali) saat HITUNG; saat DRAFT hanya atur daftar barang (tanpa jumlah fisik). Baris baru = barang yang
    ditemukan fisik di gudang tetapi tak ada di daftar. physical_quantity null = batalkan hitungan baris itu."""
    ctx = await _ctx(request)
    tid = ctx["tenant_id"]
    pool = await SA.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _flag(conn, tid)
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"OPNAME:{opname_id}")
            o = await _muat(conn, tid, opname_id, True)
            if not o:
                raise GalatBerkode(404, "OPNAME_TIDAK_DITEMUKAN", "Stok opname tidak ditemukan.")
            if o["status"] not in ("draft", "hitung"):
                raise GalatBerkode(409, "OPNAME_TIDAK_BISA_DIUBAH", f"Opname {o['opname_number']} berstatus {STATUS_TEKS[o['status']]}; hasil hitung tidak bisa diubah.")
            urut = await conn.fetchval("SELECT COALESCE(MAX(line_number), 0) FROM stock_opname_lines WHERE opname_id = $1", opname_id)
            for b in body.baris:
                try:
                    pid = UUID(str(b.product_id))
                except (ValueError, TypeError):
                    raise GalatBerkode(400, "OPNAME_BARANG_TIDAK_VALID", "ID barang tidak valid.")
                if b.physical_quantity is not None and o["status"] == "draft":
                    raise GalatBerkode(409, "OPNAME_BELUM_DIMULAI", "Jumlah fisik diisi setelah opname dimulai (gudang dikunci).")
                if b.physical_quantity is not None and b.physical_quantity < 0:
                    raise GalatBerkode(422, "OPNAME_FISIK_NEGATIF", "Jumlah fisik tidak boleh negatif.")
                ada = await conn.fetchval("SELECT id FROM stock_opname_lines WHERE opname_id = $1 AND product_id = $2", opname_id, pid)
                if b.hapus:
                    if ada:
                        await conn.execute("DELETE FROM stock_opname_lines WHERE id = $1", ada)
                    continue
                if not ada:
                    bl = Blok(False)
                    p = await _kandidat(conn, tid, o["warehouse_id"], "daftar", [str(pid)], bl)
                    urut += 1
                    await _tulis_baris(conn, tid, opname_id, p, urut)
                    if o["status"] == "hitung":   # barang tambahan saat hitung: snapshot sistem = ledger SAAT INI
                        kini = (await _stok_banyak(conn, tid, o["warehouse_id"], [pid])).get(pid, NOL)
                        await conn.execute("UPDATE stock_opname_lines SET system_quantity = $3 WHERE opname_id = $1 AND product_id = $2", opname_id, pid, kini)
                if o["status"] == "hitung":
                    await conn.execute(
                        "UPDATE stock_opname_lines SET physical_quantity = $3, note = COALESCE($4, note), "
                        "counted_by = CASE WHEN $3::numeric IS NULL THEN NULL ELSE $5::uuid END, counted_at = CASE WHEN $3::numeric IS NULL THEN NULL ELSE now() END "
                        "WHERE opname_id = $1 AND product_id = $2", opname_id, pid, b.physical_quantity, b.note, ctx["user_id"])
            n = await conn.fetchrow("SELECT count(*) AS j, count(physical_quantity) AS h FROM stock_opname_lines WHERE opname_id = $1", opname_id)
            return {"success": True, "data": {"id": str(opname_id), "jumlah_baris": n["j"], "sudah_dihitung": n["h"]}}


# ------------------------------------------------------------------ mulai / post / batal / void
@router.post("/{opname_id}/mulai/preview")
async def pratinjau_mulai(request: Request, opname_id: UUID):
    async def r(conn, tid, bl):
        h = await rencana_mulai(conn, tid, opname_id, bl)
        return {"jumlah_baris": h.get("jumlah_baris"), "catatan": h.get("catatan"), "gudang": h["gudang"]["name"] if h.get("gudang") else None}
    return await _pratinjau(request, r)


@router.post("/{opname_id}/mulai")
async def mulai_hitung(request: Request, opname_id: UUID, response: Response = None):
    ctx = await _ctx(request)
    tid = ctx["tenant_id"]
    pool = await SA.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _flag(conn, tid)
            o0 = await _muat(conn, tid, opname_id)
            if o0:   # kunci gudang dulu (dua opname yang memulai bersamaan pada gudang yang sama diserialkan), baru dokumen
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"OPNAME_WH:{tid}:{o0['warehouse_id']}")
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"OPNAME:{opname_id}")
            _kp, _sd, _lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "OPNAME_MULAI", opname_id, {}, response)
            if _lama is not None:
                return _lama
            h = await rencana_mulai(conn, tid, opname_id, Blok(False), kunci=True)
            o = h["opname"]
            pids = [b["product_id"] for b in await _baris(conn, opname_id)]
            kini = await _stok_banyak(conn, tid, o["warehouse_id"], pids)
            for p, q in kini.items():
                await conn.execute("UPDATE stock_opname_lines SET system_quantity = $3 WHERE opname_id = $1 AND product_id = $2", opname_id, p, q)
            await conn.execute("UPDATE stock_opnames SET status = 'hitung', started_at = now(), started_by = $2, updated_at = now() WHERE id = $1", opname_id, ctx["user_id"])
            return await idem_buat.simpan(conn, ctx, _kp, _sd, "OPNAME_MULAI", {
                "success": True, "message": "Hitung dimulai; gudang dikunci",
                "data": {"id": str(opname_id), "status": "hitung", "jumlah_baris": len(pids)}}, opname_id)


async def _jurnal_teks(conn, tid: str, baris) -> list:
    """jurnal_preview (Accurate menampilkan jurnal; FRONTEND 3): [{akun: 'kode · nama', debit_text, kredit_text}] dari baris (akun_id, debit, kredit, memo). Teks Rp dari server."""
    from ..services import teks_galat as tg
    ids = sorted({str(b[0]) for b in baris if b[0]})
    nama = {str(r["id"]): f"{r['account_code']} · {r['name']}" for r in await conn.fetch(
        "SELECT id, account_code, name FROM chart_of_accounts WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tid, ids)} if ids else {}
    return [{"akun": nama.get(str(a), "(akun tak dikenal)"), "debit_text": tg.rp(d) if d else None, "kredit_text": tg.rp(k) if k else None} for a, d, k, _m in baris]


async def _jurnal_post(conn, tid: str, items) -> list:
    """Jurnal Penyesuaian Stok tertaut yang AKAN ditulis posting (penentu sama dgn SS.rencana_posting: naik = Dr persediaan / Cr penyesuaian; turun sebaliknya;
    nilai per baris dibulatkan sama dgn total_value draf). Tanpa selisih = []."""
    naik = turun = NOL
    for b, sel in items:
        nilai = (abs(sel) * await SS.harga_pokok(conn, tid, b["product_id"])).quantize(Decimal("1"))
        if sel > 0:
            naik += nilai
        else:
            turun += nilai
    persediaan, penyesuaian = await SS._akun(conn, tid, Blok(False))
    if not persediaan or not penyesuaian:
        return []
    baris = []
    if naik > 0:
        baris += [(persediaan, naik, NOL, ""), (penyesuaian, NOL, naik, "")]
    if turun > 0:
        baris += [(penyesuaian, turun, NOL, ""), (persediaan, NOL, turun, "")]
    return await _jurnal_teks(conn, tid, baris)


@router.post("/{opname_id}/post/preview")
async def pratinjau_post(request: Request, opname_id: UUID):
    async def r(conn, tid, bl):
        h = await rencana_posting(conn, tid, opname_id, bl)
        baris = [{**b, **TP.medan_baris(b["selisih"] if b["dihitung"] else None, b["nilai"], b["unit"])} for b in h["baris"]]
        return {"baris": baris, **TP.total_bertanda(h.get("total_nilai_bertanda", NOL)), "belum_dihitung": h.get("belum_dihitung"),
                "baris_selisih": h.get("baris_selisih"), "catatan": h.get("catatan"), "tanpa_jurnal": h.get("tanpa_selisih"),
                "jurnal_preview": [] if bl.daftar else await _jurnal_post(conn, tid, h["items"])}
    return await _pratinjau(request, r)


@router.post("/{opname_id}/post")
async def post_opname(request: Request, opname_id: UUID, response: Response = None):
    ctx = await _ctx(request)
    tid = ctx["tenant_id"]
    pool = await SA.get_pool()
    await SA._ensure_stock_adj_role_preconditions(pool, tid)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _flag(conn, tid)
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"OPNAME:{opname_id}")
            _kp, _sd, _lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "OPNAME_POST", opname_id, {}, response)
            if _lama is not None:
                return _lama
            h = await rencana_posting(conn, tid, opname_id, Blok(False), kunci=True)
            o = h["opname"]
            sa_id = journal_id = journal_number = None
            if h["items"]:
                body = CreateStockAdjustmentRequest(
                    adjustment_date=o["opname_date"], adjustment_type="recount", warehouse_id=str(o["warehouse_id"]), reference_no=o["opname_number"],
                    notes=f"Stok Opname {o['opname_number']}",
                    items=[StockAdjustmentItemCreate(product_id=str(b["product_id"]), quantity_adjustment=s, physical_quantity=b["physical_quantity"],
                                                     reason_detail=b["note"] or f"Hasil hitung {o['opname_number']}") for b, s in h["items"]])
                r = await SS.rencana_buat(conn, tid, body, Blok(False))
                sa_id, _nomor, _total = await SA._buat_draf(conn, ctx, body, r)
                # tautkan SEBELUM menulis gerak: pengecualian kunci gudang (helper + trigger) mengenali SA milik opname ini
                await conn.execute("UPDATE stock_opnames SET adjustment_id = $2 WHERE id = $1", opname_id, sa_id)
                rp = await SS.rencana_posting(conn, tid, sa_id, Blok(False), kunci=True)
                journal_id, journal_number = await SA._tulis_posting(conn, ctx, sa_id, rp)
            await conn.execute("UPDATE stock_opnames SET status = 'posted', posted_at = now(), posted_by = $2, updated_at = now() WHERE id = $1", opname_id, ctx["user_id"])
            return await idem_buat.simpan(conn, ctx, _kp, _sd, "OPNAME_POST", {
                "success": True, "message": "Stok opname selesai" + ("" if sa_id else " (tanpa selisih)"),
                "data": {"id": str(opname_id), "status": "posted", "adjustment_id": str(sa_id) if sa_id else None,
                         "journal_id": str(journal_id) if journal_id else None, "journal_number": journal_number, "baris_selisih": len(h["items"])}}, opname_id)


@router.post("/{opname_id}/batal/preview")
async def pratinjau_batal(request: Request, opname_id: UUID):
    async def r(conn, tid, bl):
        h = await rencana_batal(conn, tid, opname_id, bl)
        return {"catatan": h["catatan"], "jurnal_preview": []}   # batal = tanpa jurnal, tanpa stok
    return await _pratinjau(request, r)


@router.post("/{opname_id}/batal")
async def batal_opname(request: Request, opname_id: UUID, body: Alasan, response: Response = None):
    ctx = await _ctx(request)
    tid = ctx["tenant_id"]
    pool = await SA.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _flag(conn, tid)
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"OPNAME:{opname_id}")
            _kp, _sd, _lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "OPNAME_BATAL", opname_id, {"reason": body.reason}, response)
            if _lama is not None:
                return _lama
            await rencana_batal(conn, tid, opname_id, Blok(False), kunci=True)
            await conn.execute("UPDATE stock_opnames SET status = 'batal', cancelled_at = now(), cancelled_by = $2, cancelled_reason = $3, updated_at = now() WHERE id = $1",
                               opname_id, ctx["user_id"], body.reason)
            return await idem_buat.simpan(conn, ctx, _kp, _sd, "OPNAME_BATAL", {
                "success": True, "message": "Stok opname dibatalkan; gudang tidak terkunci lagi", "data": {"id": str(opname_id), "status": "batal"}}, opname_id)


@router.post("/{opname_id}/void/preview")
async def pratinjau_void(request: Request, opname_id: UUID, body: Optional[AlasanOpsional] = None):
    async def r(conn, tid, bl):
        h = await rencana_void(conn, tid, opname_id, body.reason if body else None, bl)
        sa = h.get("sa_rencana") or {}
        return {"catatan": h.get("catatan"), "jurnal_baris": len(sa.get("baris") or []), "tanpa_jurnal": bool(h.get("opname") and not h["opname"]["adjustment_id"]),
                "jurnal_preview": await _jurnal_teks(conn, tid, sa.get("baris") or [])}
    return await _pratinjau(request, r)


@router.post("/{opname_id}/void")
async def void_opname(request: Request, opname_id: UUID, body: Alasan, response: Response = None):
    ctx = await _ctx(request)
    tid = ctx["tenant_id"]
    pool = await SA.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _flag(conn, tid)
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"OPNAME:{opname_id}")
            o0 = await _muat(conn, tid, opname_id)
            if o0 and o0["adjustment_id"]:
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"STOCK_ADJ_VOID:{o0['adjustment_id']}")   # lock yang sama dgn void SA langsung
            _kp, _sd, _lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "OPNAME_VOID", opname_id, {"reason": body.reason}, response)
            if _lama is not None:
                return _lama
            h = await rencana_void(conn, tid, opname_id, body.reason, Blok(False), kunci=True)
            o, r = h["opname"], h["sa_rencana"]
            reversal = None
            if r is not None:
                reversal = await SA._tulis_void(conn, ctx, o["adjustment_id"], r, body.reason)
            await conn.execute("UPDATE stock_opnames SET status = 'void', voided_at = now(), voided_by = $2, voided_reason = $3, updated_at = now() WHERE id = $1",
                               opname_id, ctx["user_id"], body.reason)
            return await idem_buat.simpan(conn, ctx, _kp, _sd, "OPNAME_VOID", {
                "success": True, "message": "Stok opname di-void",
                "data": {"id": str(opname_id), "status": "void", "reversal_journal_id": str(reversal) if reversal else None}}, opname_id)
