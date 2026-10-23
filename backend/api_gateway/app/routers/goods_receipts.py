"""Penerimaan Barang (GRN) -- daftar, detail, batal (+ pratinjau). Dokumen lahir lewat POST /api/purchase-orders/{id}/receive saat flag
`penerimaan_barang_v1` hidup (services/grn_rencana). Baca & batal tak bergantung flag (dokumen yang sudah ada tetap bisa dibatalkan).
Izin: modul purchase_order (R baca, V batal) -- middleware/permission_middleware.
SERAGAM (23 Okt 2026, ANDROID/MASTER, ADITIF): baris & detail + billing_state/status_text/grni_open_amount/aksi (services/grn_daftar, SATU
sumber dgn ?aksi= & /summary); detail bills[] (tautan aktif + jenis); /documents (bundel po · grn · faktur, bentuk ND U3c); /history
(bentuk riwayat ND/tagihan); pratinjau batal + kode/nama akun + sebelum/sesudah."""
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from ..services import grn_daftar as GD
from ..services import grn_rencana as G
from ..services import idem_buat
from ..services.so_riwayat import catat_riwayat
from ..utils.tanggal_tenant import tanggal_dokumen
from .purchase_orders import get_pool, get_user_context

logger = logging.getLogger(__name__)
router = APIRouter()


class VoidGoodsReceiptRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class VoidGoodsReceiptPreviewRequest(BaseModel):
    reason: Optional[str] = None


def _uang(v) -> Optional[str]:
    return str(G.r2(G._d(v))) if v is not None else None


def _bentuk(gr, items=None) -> dict:
    out = {"id": str(gr["id"]), "receipt_number": gr["receipt_number"], "purchase_order_id": str(gr["purchase_order_id"]),
           "po_number": gr.get("po_number"), "vendor_id": str(gr["vendor_id"]) if gr.get("vendor_id") else None,
           "vendor_name": gr.get("vendor_name"), "receipt_date": gr["receipt_date"].isoformat(),
           "warehouse_id": str(gr["warehouse_id"]) if gr.get("warehouse_id") else None, "warehouse_name": gr.get("warehouse_name"),
           "status": gr["status"], "position_text": "Dibatalkan" if gr["status"] == "void" else "Diterima",
           "journal_id": str(gr["journal_id"]) if gr.get("journal_id") else None,
           "reversal_journal_id": str(gr["reversal_journal_id"]) if gr.get("reversal_journal_id") else None,
           "amount": _uang(gr.get("amount")), "notes": gr.get("notes"),
           "voided_at": gr["voided_at"].isoformat() if gr.get("voided_at") else None, "voided_reason": gr.get("voided_reason")}
    if "qty_dasar" in gr.keys():  # SERAGAM: medan turunan (SQL_HEADER membawa kolom GD.sql_kolom)
        out.update(GD.medan(gr))
    if items is not None:
        out["items"] = [{"id": str(i["id"]), "po_item_id": str(i["purchase_order_item_id"]), "description": i.get("description"),
                         "unit": i.get("unit"), "quantity": str(i["quantity"]), "quantity_returned": str(i["quantity_returned"]),
                         "quantity_billed": str(i.get("quantity_billed") or 0), "unit_cost": _uang(i["unit_cost"]),
                         "amount": _uang(i["amount"]), "stocked": i["ber_stok"]} for i in items]
    return out


SQL_HEADER = """
    SELECT g.*, po.po_number, COALESCE(v.name, po.vendor_name) AS vendor_name, w.name AS warehouse_name,
           (SELECT COALESCE(SUM(i.amount), 0) FROM goods_receipt_items i WHERE i.goods_receipt_id = g.id) AS amount,
           """ + GD.sql_kolom("g") + """
    FROM goods_receipts g
    JOIN purchase_orders po ON po.id = g.purchase_order_id AND po.tenant_id = g.tenant_id
    LEFT JOIN vendors v ON v.id = g.vendor_id
    LEFT JOIN warehouses w ON w.id = g.warehouse_id
    WHERE g.tenant_id = $1
"""


@router.get("")
async def list_goods_receipts(request: Request, purchase_order_id: Optional[UUID] = Query(None),
                              status: Optional[str] = Query(None, pattern="^(posted|void)$"),
                              aksi: Optional[str] = Query(None, pattern="^belum_ditagih$"),
                              limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    ctx = get_user_context(request)
    po_id = purchase_order_id if isinstance(purchase_order_id, UUID) else None
    st = status if isinstance(status, str) else None
    ak = aksi if isinstance(aksi, str) else None  # pemanggilan langsung: default = objek Query
    pool = await get_pool()
    async with pool.acquire() as conn:
        syarat, par = "", [ctx["tenant_id"]]
        if po_id is not None:
            par.append(po_id)
            syarat += f" AND g.purchase_order_id = ${len(par)}"
        if st is not None:
            par.append(st)
            syarat += f" AND g.status = ${len(par)}"
        if ak is not None:
            syarat += f" AND {GD.sql_aksi(ak)}"  # predikat SAMA dengan /summary
        total = await conn.fetchval(f"SELECT count(*) FROM ({SQL_HEADER}{syarat}) x", *par)
        rows = await conn.fetch(f"{SQL_HEADER}{syarat} ORDER BY g.receipt_date DESC, g.created_at DESC LIMIT {int(limit)} OFFSET {int(offset)}", *par)
    return {"success": True, "data": {"items": [_bentuk(r) for r in rows], "total": int(total), "limit": limit, "offset": offset}}


@router.get("/summary")
async def goods_receipts_summary(request: Request, purchase_order_id: Optional[UUID] = Query(None)):
    """Ringkasan + pill AKSI belum_ditagih (predikat SAMA dgn ?aksi=; nilai = Σ grni_open_amount). Uang string 2dp."""
    ctx = get_user_context(request)
    po_id = purchase_order_id if isinstance(purchase_order_id, UUID) else None
    pool = await get_pool()
    async with pool.acquire() as conn:
        par, syarat = [ctx["tenant_id"]], ""
        if po_id is not None:
            par.append(po_id)
            syarat = f" AND g.purchase_order_id = ${len(par)}"
        r = await conn.fetchrow(
            f"""SELECT count(*) AS total, count(*) FILTER (WHERE g.status = 'posted') AS posted, count(*) FILTER (WHERE g.status = 'void') AS void,
                       count(*) FILTER (WHERE {GD.sql_aksi(GD.AKSI_BELUM_DITAGIH)}) AS n_aksi,
                       COALESCE(SUM({GD.sql_grni_terbuka()}) FILTER (WHERE {GD.sql_aksi(GD.AKSI_BELUM_DITAGIH)}), 0) AS v_aksi
                FROM goods_receipts g WHERE g.tenant_id = $1{syarat}""", *par)
    return {"success": True, "data": {"total": int(r["total"]), "posted": int(r["posted"]), "void": int(r["void"]),
                                      f"{GD.AKSI_BELUM_DITAGIH}_count": int(r["n_aksi"]),
                                      f"{GD.AKSI_BELUM_DITAGIH}_value": f"{G._d(r['v_aksi']):.2f}"}}


@router.get("/{gr_id}")
async def get_goods_receipt(request: Request, gr_id: UUID):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        gr = await conn.fetchrow(SQL_HEADER + " AND g.id = $2", ctx["tenant_id"], gr_id)
        if gr is None:
            raise HTTPException(status_code=404, detail={"code": "GRN_TIDAK_DITEMUKAN", "message": "Penerimaan barang tidak ditemukan."})
        items = await conn.fetch(
            """SELECT i.*, poi.description, poi.unit,
                      COALESCE((SELECT SUM(l.quantity) FROM goods_receipt_bill_links l WHERE l.goods_receipt_item_id = i.id
                                AND l.status = 'active'), 0) AS quantity_billed
               FROM goods_receipt_items i JOIN purchase_order_items poi ON poi.id = i.purchase_order_item_id
               WHERE i.goods_receipt_id = $1 ORDER BY i.line_number""", gr_id)
        tagihan = await GD.tagihan_terkait(conn, ctx["tenant_id"], gr_id)
    return {"success": True, "data": {**_bentuk(gr, items), "bills": tagihan}}


@router.get("/{gr_id}/documents")
async def get_goods_receipt_documents(request: Request, gr_id: UUID):
    """Bundel panel berjangkar GRN, skema SAMA dgn bundel ND/tagihan/PO {anchor, default, groups}. paid = null (GRN bukan dokumen
    pembayaran); balance = grni_open_amount untuk GRN aktif (null bila batal)."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        tid = ctx["tenant_id"]
        gr = await conn.fetchrow(SQL_HEADER + " AND g.id = $2", tid, gr_id)
        if gr is None:
            raise HTTPException(status_code=404, detail={"code": "GRN_TIDAK_DITEMUKAN", "message": "Penerimaan barang tidak ditemukan."})
        kel = await GD.kelompok_grn(conn, tid, gr)
        kontak = await conn.fetchrow("SELECT COALESCE(mobile_phone, phone) AS phone, email FROM vendors WHERE id = $1 AND tenant_id = $2",
                                     gr["vendor_id"], tid) if gr["vendor_id"] else None
    m = GD.medan(gr)
    aktif = gr["status"] != "void"
    return {"anchor": {"kind": "goods_receipt", "id": str(gr["id"]), "number": gr["receipt_number"], "vendor": gr["vendor_name"] or "",
                       "position_text": m["status_text"], "total": f"{G._d(gr['amount']):.2f}", "paid": None,
                       "balance": m["grni_open_amount"] if aktif else None,
                       "vendor_phone": (kontak["phone"] if kontak else None) or None,
                       "vendor_email": (kontak["email"] if kontak else None) or None},
            "default": {"group": "grn", "index": 0},
            "groups": kel}


@router.get("/{gr_id}/history")
async def get_goods_receipt_history(request: Request, gr_id: UUID, limit: int = Query(200, ge=1, le=500)):
    """Riwayat GRN (services/grn_daftar.riwayat_grn), bentuk SAMA dengan riwayat ND/tagihan/PO. Terbaru dulu."""
    from ..services.dashboard_izin import boleh_baca
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        data = await GD.riwayat_grn(conn, ctx["tenant_id"], gr_id, lambda m: boleh_baca(request, m), limit if isinstance(limit, int) else 200)
    if data is None:
        raise HTTPException(status_code=404, detail={"code": "GRN_TIDAK_DITEMUKAN", "message": "Penerimaan barang tidak ditemukan."})
    return {"success": True, "data": data}


@router.post("/{gr_id}/void/preview")
async def preview_void_goods_receipt(request: Request, gr_id: UUID, body: Optional[VoidGoodsReceiptPreviewRequest] = None):
    """Pratinjau BATAL: blok (sudah ditagih, alasan kosong, periode tutup, stok sudah keluar) + jurnal pembalik yang akan terbentuk.
    Transaksi READ ONLY, selalu ROLLBACK."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        tr = conn.transaction(readonly=True)
        await tr.start()
        try:
            hari = await tanggal_dokumen(conn, ctx["tenant_id"])
            r = await G.rencana_void(conn, ctx["tenant_id"], gr_id, body.reason if body else None, hari, kunci=False)
            balik = []
            if r["gr"]["journal_id"] is not None and not r["ulang"]:
                balik = [{"account_id": str(x["account_id"]), "account_code": x["account_code"], "account_name": x["account_name"],
                          "debit": _uang(x["credit"]), "credit": _uang(x["debit"])} for x in await conn.fetch(
                    """SELECT jl.account_id, c.account_code, c.name AS account_name, jl.debit, jl.credit FROM journal_lines jl
                       LEFT JOIN chart_of_accounts c ON c.id = jl.account_id WHERE jl.journal_id = $1 ORDER BY jl.line_number""",
                    r["gr"]["journal_id"])]
            hd = await conn.fetchrow(SQL_HEADER + " AND g.id = $2", ctx["tenant_id"], gr_id)
            sebelum = GD.medan(hd)
        finally:
            await tr.rollback()
    return {"success": True, "data": {"goods_receipt_id": str(gr_id), "ok": not r["blocks"], "already_void": r["ulang"],
                                      "blocks": [{"code": b["code"], "message": b["message"]} for b in r["blocks"]],
                                      "reversal_journal": balik,
                                      # SERAGAM: keadaan sebelum/sesudah untuk kartu Tinjau (sesudah = batal; GRNI terbuka dokumen ini 0)
                                      "before": {"status_text": sebelum["status_text"], "grni_open_amount": sebelum["grni_open_amount"]},
                                      "after": {"status_text": GD.TEKS_TAGIH["batal"], "grni_open_amount": "0.00"},
                                      "payload": {"reason": body.reason if body else None}}}


@router.post("/{gr_id}/void")
async def void_goods_receipt(request: Request, gr_id: UUID, body: VoidGoodsReceiptRequest, response: Response = None):
    ctx = get_user_context(request)
    if not ctx.get("user_id"):
        raise HTTPException(status_code=401, detail="ID pengguna wajib ada.")
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
            kp, sidik, lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "GRN_VOID", gr_id,
                                                         lambda: body.model_dump(mode="json"), response)
            if lama is not None:
                return lama
            hari = await tanggal_dokumen(conn, ctx["tenant_id"])
            hasil = await G.void_grn(conn, ctx, gr_id, body.reason, hari)
            gr = await conn.fetchrow(SQL_HEADER + " AND g.id = $2", ctx["tenant_id"], gr_id)
            if not hasil["ulang"]:
                await catat_riwayat(conn, ctx["tenant_id"], "purchase_order", gr["purchase_order_id"], gr["po_number"], "GRN_DIBATALKAN",
                                    ctx["user_id"], f"Penerimaan barang {gr['receipt_number']} dibatalkan: {body.reason.strip()}",
                                    meta={"goods_receipt_id": str(gr_id)}, source="api:goods_receipts")
            resp = {"success": True, "message": f"Penerimaan barang {gr['receipt_number']} dibatalkan.",
                    "data": {**_bentuk(gr), "already_void": hasil["ulang"]}}
            return await idem_buat.simpan(conn, ctx, kp, sidik, "GRN_VOID", resp, gr_id)
