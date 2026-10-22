"""Pesanan Pembelian -- PENENTU per aksi (kontrak PO tahap 2, 22 Okt 2026; pola SO `_rencana_*`, acuan NetSuite PO).

SATU definisi aturan per aksi, dipakai DUA jalur:
  * endpoint TULIS (POST /purchase-orders, PATCH, /send, /receive, /close, /cancel, DELETE): blok pertama -> galat HTTP (pesan & status
    lama dipertahankan), tanpa blok -> tulis;
  * endpoint PRATINJAU (/calculate, /{id}/edit|send|receive|close|cancel|delete/preview): SEMUA blok {code, message}, 200, transaksi
    SELALU di-ROLLBACK (nol tulisan), `payload` siap kirim ke endpoint tulisnya.
Uang = services/po_hitung (Decimal 2dp HALF_UP) -- kalkulator yang SAMA dengan create/patch. Semua pembacaan tenant eksplisit; penentu
dipanggil SESUDAH _kunci_po (advisory + FOR UPDATE) di endpoint tulis maupun pratinjau.

Blok: {"code", "message", "status"} -- `status` = kode HTTP endpoint tulis (dibuang dari respons pratinjau)."""
from decimal import Decimal
from typing import Optional
from uuid import UUID

from . import po_daftar as PD
from . import po_hitung as PH

NOL = Decimal("0")
STATUS_BISA_UBAH = ("draft", "sent")
STATUS_BISA_TERIMA = ("sent", "partial_received")
STATUS_TAK_BISA_TUTUP = ("draft", "cancelled", "closed")
STATUS_TAK_BISA_BATAL = ("cancelled", "closed")


def blok(code: str, message: str, status: int = 400) -> dict:
    return {"code": code, "message": message, "status": status}


def untuk_respons(bloks: list) -> list:
    return [{"code": b["code"], "message": b["message"]} for b in bloks]


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else NOL


def _f(v):
    return float(PH.r2(v)) if v is not None else None


# ---------------------------------------------------------------- hitung (create / patch / calculate)
def hitung(items: list, discount_percent=0, discount_amount=0, tax_rate=0) -> dict:
    """items: dict baris (bentuk PurchaseOrderItemCreate.model_dump()). -> {baris (Decimal), header (Decimal)}. SATU kalkulator create/patch."""
    baris = [PH.hitung_baris(i) for i in items]
    return {"baris": baris, "header": PH.hitung_header(baris, discount_percent, discount_amount, tax_rate)}


def hitung_json(h: dict) -> dict:
    """Bentuk respons /calculate (sejajar SO /calculate): per baris + header, angka float 2dp."""
    t = h["header"]
    return {
        "subtotal": _f(t["subtotal"]), "discount_amount": _f(t["discount_amount"]), "tax_amount": _f(t["tax_amount"]),
        "total_amount": _f(t["total_amount"]),
        "items": [{"line_number": n, "description": b.get("description"), "quantity": _f(b.get("quantity")) if b.get("quantity") is not None else None,
                   "unit": b.get("unit"), "unit_price": _f(b.get("unit_price")), "discount_percent": float(b.get("discount_percent") or 0),
                   "tax_rate": float(b.get("tax_rate") or 0), "subtotal": _f(b["subtotal"]), "discount_amount": _f(b["discount_amount"]),
                   "tax_amount": _f(b["tax_amount"]), "total": _f(b["total"])}
                  for n, b in enumerate(h["baris"], 1)],
    }


async def blok_vendor(conn, tenant_id: str, vendor_id) -> tuple:
    """(vendor|None, [blok]) -- Law 24: vendor_id wajib milik tenant. Sama dengan _vendor_tenant router (422)."""
    if not vendor_id:
        return None, []
    try:
        vid = UUID(str(vendor_id))
    except (ValueError, TypeError):
        return None, [blok("VENDOR_TIDAK_SAH", "ID vendor tidak sah.", 422)]
    v = await conn.fetchrow("SELECT id, name FROM vendors WHERE id = $1 AND tenant_id = $2", vid, tenant_id)
    if not v:
        return None, [blok("VENDOR_TIDAK_DITEMUKAN", "Vendor tidak ditemukan di usaha ini.", 422)]
    return v, []


# ---------------------------------------------------------------- ubah (PATCH)
async def rencana_ubah(conn, tenant_id: str, po, body) -> dict:
    """po = baris PO terkunci (id, status, po_number + kolom header). body = UpdatePurchaseOrderRequest.
    -> {blocks, perubahan[{field, before, after}], header_sesudah (Decimal|None bila uang tak berubah), baris_baru (list|None)}."""
    bloks = []
    if po["status"] not in STATUS_BISA_UBAH:
        bloks.append(blok("PO_TAK_BISA_DIUBAH",
                          f"Pesanan pembelian berstatus '{po['status']}' tidak bisa diubah. Hanya Draf atau Terkirim."))
    data = body.model_dump(exclude_unset=True)
    if data.get("vendor_id"):
        _, bv = await blok_vendor(conn, tenant_id, data["vendor_id"])
        bloks += bv
    lama = await conn.fetch(
        """SELECT id, description, quantity, quantity_received, quantity_billed, subtotal, discount_amount, tax_amount
           FROM purchase_order_items WHERE po_id = $1 ORDER BY line_number""", po["id"])
    baris_baru = None
    if data.get("items"):
        # PATCH mengganti SEMUA baris: baris yang sudah diterima/ditagih akan kehilangan jejak qty -> tolak (NetSuite: baris yang sudah
        # diterima tak boleh dikurangi di bawah qty diterima). Dengan status Draf/Terkirim mestinya nol; penjaga ini menjaga data lama.
        if any(_d(r.get("quantity_received")) > NOL or _d(r.get("quantity_billed")) > NOL for r in lama):
            bloks.append(blok("PO_BARIS_SUDAH_DITERIMA", "Baris yang barangnya sudah diterima atau ditagih tidak bisa diganti."))
        baris_baru = [PH.hitung_baris(i.model_dump()) for i in body.items]
    uang = baris_baru is not None or any(k in data for k in ("discount_percent", "discount_amount", "tax_rate"))
    header = None
    if uang:
        dasar = baris_baru if baris_baru is not None else [dict(r) for r in lama]
        header = PH.hitung_header(dasar, data.get("discount_percent", po.get("discount_percent")) or 0,
                                  data.get("discount_amount", po.get("discount_amount")) or 0, data.get("tax_rate", po.get("tax_rate")) or 0)
    perubahan = []
    for k, v in data.items():
        if k == "items":
            perubahan.append({"field": "items", "before": len(lama), "after": len(v)})
        else:
            sebelum = po.get(k)
            if str(sebelum if sebelum is not None else "") != str(v if v is not None else ""):
                perubahan.append({"field": k, "before": _j(sebelum), "after": _j(v)})
    if header is not None and _d(po.get("total_amount")) != header["total_amount"]:
        perubahan.append({"field": "total_amount", "before": _f(po.get("total_amount")), "after": _f(header["total_amount"])})
    return {"blocks": bloks, "perubahan": perubahan, "header_sesudah": header, "baris_baru": baris_baru}


def _j(v):
    if isinstance(v, Decimal):
        return float(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return None if v is None else (v if isinstance(v, (int, float, bool)) else str(v))


# ---------------------------------------------------------------- kirim / tutup / batal / hapus
def rencana_kirim(po) -> dict:
    bloks = [] if po["status"] == "draft" else [
        blok("PO_TAK_BISA_DIKIRIM", f"Pesanan pembelian berstatus '{po['status']}' tidak bisa dikirim. Hanya Draf.")]
    return {"blocks": bloks, "status_sesudah": "sent"}


async def rencana_tutup(conn, po) -> dict:
    bloks = [] if po["status"] not in STATUS_TAK_BISA_TUTUP else [
        blok("PO_TAK_BISA_DITUTUP", f"Pesanan pembelian berstatus '{po['status']}' tidak bisa ditutup.")]
    q = await conn.fetchrow(
        """SELECT COALESCE(SUM(GREATEST(quantity - COALESCE(quantity_received, 0), 0)), 0) AS sisa_terima,
                  COALESCE(SUM(GREATEST(COALESCE(quantity_received, 0) - COALESCE(quantity_billed, 0), 0)), 0) AS belum_ditagih
           FROM purchase_order_items WHERE po_id = $1""", po["id"]) or {}
    catatan = []
    if _d(q.get("sisa_terima")) > NOL:
        catatan.append({"code": "SISA_BARANG_TAK_DITERIMA", "message": f"Sisa {PH.r2(q['sisa_terima'])} unit belum diterima tidak akan ditunggu lagi."})
    if _d(q.get("belum_ditagih")) > NOL:
        catatan.append({"code": "DITERIMA_BELUM_DITAGIH",
                        "message": f"{PH.r2(q['belum_ditagih'])} unit sudah diterima tetapi belum ditagih; sesudah ditutup tidak bisa ditagih dari pesanan ini."})
    return {"blocks": bloks, "status_sesudah": "closed", "sisa_ditagih_sesudah": NOL, "notes": catatan}


async def rencana_batal(conn, tenant_id: str, po, alasan: Optional[str]) -> dict:
    bloks = []
    if po["status"] in STATUS_TAK_BISA_BATAL:
        bloks.append(blok("PO_TAK_BISA_DIBATALKAN", f"Pesanan pembelian berstatus '{po['status']}' tidak bisa dibatalkan."))
    q = await conn.fetchrow(
        "SELECT COALESCE(SUM(quantity_received), 0) AS diterima, COALESCE(SUM(quantity_billed), 0) AS ditagih "
        "FROM purchase_order_items WHERE po_id = $1", po["id"]) or {}
    if _d(q.get("diterima")) > NOL:
        bloks.append(blok("PO_SUDAH_DITERIMA", "Pesanan pembelian yang barangnya sudah diterima tidak bisa dibatalkan."))
    if _d(q.get("ditagih")) > NOL:
        bloks.append(blok("PO_SUDAH_DITAGIH", "Pesanan pembelian yang sudah ditagih tidak bisa dibatalkan."))
    if not (alasan or "").strip():
        bloks.append(blok("ALASAN_WAJIB", "Alasan pembatalan wajib diisi.", 422))
    um = await conn.fetch(
        """SELECT deposit_number FROM vendor_deposits WHERE tenant_id = $1 AND purchase_order_id = $2 AND status NOT IN ('draft', 'void')
           ORDER BY deposit_number""", tenant_id, po["id"])
    catatan = [{"code": "UANG_MUKA_TETAP", "message": f"Uang muka {r['deposit_number']} tetap tercatat; terapkan ke tagihan lain atau minta pengembalian."}
               for r in um]
    return {"blocks": bloks, "status_sesudah": "cancelled", "sisa_ditagih_sesudah": NOL, "notes": catatan}


def rencana_hapus(po) -> dict:
    bloks = [] if po["status"] == "draft" else [
        blok("PO_BUKAN_DRAF", "Hanya pesanan pembelian berstatus Draf yang bisa dihapus. Yang sudah terkirim dibatalkan.")]
    return {"blocks": bloks}


# ---------------------------------------------------------------- terima
async def rencana_terima(conn, po, receive_date, items: Optional[list], hari_ini) -> dict:
    """items: [{po_item_id, quantity_received}] atau None/[] = SEMUA sisa (bawaan pratinjau). -> {blocks, baris[], nilai_diterima (penerimaan ini),
    amount_received_sesudah, status_sesudah, payload}. Aturan & urutan = /receive."""
    bloks = []
    if po["status"] not in STATUS_BISA_TERIMA:
        bloks.append(blok("PO_TAK_BISA_DITERIMA", f"Barang tidak bisa diterima untuk pesanan pembelian berstatus '{po['status']}'."))
    if receive_date is None:
        receive_date = hari_ini
    if receive_date > hari_ini:
        bloks.append(blok("TANGGAL_TERIMA_MASA_DEPAN", "Tanggal terima tidak boleh di masa depan."))
    if po.get("po_date") and receive_date < po["po_date"]:
        bloks.append(blok("TANGGAL_TERIMA_SEBELUM_PO", "Tanggal terima tidak boleh sebelum tanggal pesanan."))
    semua = await conn.fetch(
        """SELECT id, description, quantity, quantity_received, quantity_billed, unit, total FROM purchase_order_items
           WHERE po_id = $1 ORDER BY line_number FOR UPDATE""", po["id"])
    peta = {str(r["id"]): r for r in semua}
    if not items:
        items = [{"po_item_id": str(r["id"]), "quantity_received": _d(r["quantity"]) - _d(r["quantity_received"])}
                 for r in semua if _d(r["quantity"]) - _d(r["quantity_received"]) > NOL]
        if not items:
            bloks.append(blok("TAK_ADA_SISA_TERIMA", "Semua barang pesanan ini sudah diterima."))
    akan = {}
    for it in items:
        pid, qty = str(it["po_item_id"]), _d(it["quantity_received"])
        r = peta.get(pid)
        if r is None:
            bloks.append(blok("BARIS_TIDAK_DITEMUKAN", f"Baris pesanan {pid} tidak ditemukan."))
            continue
        sisa = _d(r["quantity"]) - _d(r["quantity_received"])
        if qty > sisa:
            bloks.append(blok("QTY_MELEBIHI_SISA", f"Jumlah diterima ({qty}) melebihi sisa ({sisa})."))
        akan[pid] = akan.get(pid, NOL) + qty
    sebelum = PD.jumlah_turunan([dict(r) for r in semua])["amount_received"]
    sesudah_baris = [{**dict(r), "quantity_received": _d(r["quantity_received"]) + akan.get(str(r["id"]), NOL)} for r in semua]
    sesudah = PD.jumlah_turunan(sesudah_baris)["amount_received"]
    lengkap = all(_d(r["quantity_received"]) >= _d(r["quantity"]) for r in sesudah_baris)
    baris = [{"po_item_id": str(r["id"]), "description": r.get("description"), "unit": r.get("unit"), "qty": _d(r["quantity"]),
              "sudah_diterima": _d(r["quantity_received"]), "sisa": _d(r["quantity"]) - _d(r["quantity_received"]),
              "akan_diterima": akan.get(str(r["id"]), NOL)} for r in semua]
    payload = {"receive_date": receive_date.isoformat(),
               "items": [{"po_item_id": k, "quantity_received": str(v)} for k, v in akan.items() if v > NOL]}
    return {"blocks": bloks, "baris": baris, "nilai_diterima": sesudah - sebelum, "amount_received_sesudah": sesudah,
            "status_sesudah": "received" if lengkap else "partial_received", "payload": payload, "akan": akan, "semua": semua}
