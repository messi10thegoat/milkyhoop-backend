"""Penerimaan Barang (GRN) SERAGAM (23 Okt 2026, permintaan ANDROID via MASTER) -- medan turunan daftar/detail, pill AKSI, bundel dokumen,
riwayat. ADITIF (nol tulisan, nol jurnal). Pola kembar Nota Debit U3c (nota_debit_daftar / nota_debit_riwayat).

Medan per GRN (SATU sumber SQL untuk baris daftar, detail, ?aksi=, /summary -- paritas):
  grni_open_amount = Σ baris BER-STOK (qty - retur) x harga terima (2dp per baris) - Σ grni_amount tautan AKTIF (jenis apa pun) -- rumus SAMA
                     dengan penjaga #1 per dokumen; GRN batal = 0.
  billing_state    = batal | belum_ditagih | sebagian_ditagih | sudah_ditagih, dari qty: dasar = Σ (qty - retur) baris yang DAPAT ditautkan
                     tagihan (ber-stok, atau baris legacy), tertaut = Σ qty tautan AKTIF. Dasar 0 (GRN berisi barang non-stok/jasa saja; tagihan
                     atas baris non-stok tidak bertaut GRN -- grn_tagih hanya baris PO ber-stok) -> None + status_text "Diterima".
  aksi             = belum_ditagih (pill) <=> posted AND tertaut < dasar (mencakup sebagian ditagih) -- predikat SAMA ?aksi= & /summary."""
from decimal import Decimal
from typing import Awaitable, Callable, Optional

NOL = Decimal("0")
AKSI_BELUM_DITAGIH = "belum_ditagih"
AKSI = (AKSI_BELUM_DITAGIH,)
TEKS_TAGIH = {"batal": "Dibatalkan", "belum_ditagih": "Belum ditagih", "sebagian_ditagih": "Sebagian ditagih",
              "sudah_ditagih": "Sudah ditagih", None: "Diterima"}


def sql_qty_dasar(g: str = "g") -> str:
    return (f"(SELECT COALESCE(SUM(i.quantity - i.quantity_returned), 0) FROM goods_receipt_items i WHERE i.goods_receipt_id = {g}.id"
            " AND (i.ber_stok OR EXISTS (SELECT 1 FROM goods_receipt_bill_links lg WHERE lg.goods_receipt_item_id = i.id AND lg.kind = 'legacy')))")


def sql_qty_tertaut(g: str = "g") -> str:
    return (f"(SELECT COALESCE(SUM(l.quantity), 0) FROM goods_receipt_bill_links l JOIN goods_receipt_items i ON i.id = l.goods_receipt_item_id"
            f" WHERE i.goods_receipt_id = {g}.id AND l.status = 'active')")


def sql_grni_terbuka(g: str = "g") -> str:
    return (f"(CASE WHEN {g}.status = 'void' THEN 0 ELSE"
            f" (SELECT COALESCE(SUM(ROUND((i.quantity - i.quantity_returned) * i.unit_cost, 2)), 0) FROM goods_receipt_items i"
            f"  WHERE i.goods_receipt_id = {g}.id AND i.ber_stok)"
            f" - (SELECT COALESCE(SUM(l.grni_amount), 0) FROM goods_receipt_bill_links l JOIN goods_receipt_items i ON i.id = l.goods_receipt_item_id"
            f"  WHERE i.goods_receipt_id = {g}.id AND l.status = 'active') END)")


def sql_kolom(g: str = "g") -> str:
    """Kolom turunan untuk SELECT header (daftar & detail)."""
    return f"{sql_qty_dasar(g)} AS qty_dasar, {sql_qty_tertaut(g)} AS qty_tertaut, {sql_grni_terbuka(g)} AS grni_terbuka"


def sql_aksi(aksi: str, g: str = "g") -> str:
    if aksi != AKSI_BELUM_DITAGIH:
        raise ValueError(f"aksi GRN tak dikenal: {aksi}")
    return f"({g}.status = 'posted' AND {sql_qty_tertaut(g)} < {sql_qty_dasar(g)})"


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else NOL


def billing_state(status: str, qty_dasar, qty_tertaut) -> Optional[str]:
    if status == "void":
        return "batal"
    dasar, taut = _d(qty_dasar), _d(qty_tertaut)
    if dasar <= NOL:
        return None
    if taut <= NOL:
        return "belum_ditagih"
    return "sudah_ditagih" if taut >= dasar else "sebagian_ditagih"


def medan(r) -> dict:
    """SEMUA medan turunan untuk satu GRN (baris daftar DAN detail -- paritas). r membawa status, qty_dasar, qty_tertaut, grni_terbuka."""
    bs = billing_state(r["status"], r["qty_dasar"], r["qty_tertaut"])
    ak = AKSI_BELUM_DITAGIH if bs in ("belum_ditagih", "sebagian_ditagih") else None
    return {"billing_state": bs, "status_text": TEKS_TAGIH[bs], "grni_open_amount": f"{_d(r['grni_terbuka']):.2f}",
            "aksi": ak, "aksi_semua": [ak] if ak else []}


# ---------------------------------------------------------------- tagihan terkait (detail bills[])
SQL_TAGIHAN = """
    SELECT l.goods_receipt_item_id, l.kind, l.quantity, l.grni_amount, l.nilai_tagih, l.variance_amount, l.created_at,
           b.id AS bill_id, b.invoice_number, b.status_v2
    FROM goods_receipt_bill_links l
    JOIN goods_receipt_items i ON i.id = l.goods_receipt_item_id
    JOIN bill_items bi ON bi.id = l.bill_item_id
    JOIN bills b ON b.id = bi.bill_id AND b.tenant_id = $1
    WHERE i.goods_receipt_id = $2 AND l.status = 'active'
    ORDER BY l.created_at, b.invoice_number
"""


async def tagihan_terkait(conn, tenant_id: str, gr_id) -> list:
    """bills[] detail: tautan AKTIF ke baris tagihan. billed_amount legacy = null (tagihan lama tak menyimpan bagian B per tautan)."""
    return [{"bill_id": str(r["bill_id"]), "bill_number": r["invoice_number"], "bill_status": r["status_v2"], "kind": r["kind"],
             "goods_receipt_item_id": str(r["goods_receipt_item_id"]), "quantity": str(r["quantity"]),
             "received_amount": f"{_d(r['grni_amount']):.2f}",
             "billed_amount": f"{_d(r['nilai_tagih']):.2f}" if r["nilai_tagih"] is not None else None,
             "variance": f"{_d(r['variance_amount']):.2f}"} for r in await conn.fetch(SQL_TAGIHAN, tenant_id, gr_id)]


# ---------------------------------------------------------------- bundel dokumen
def dok_goods_receipt(r) -> dict:
    batal = r["status"] == "void"
    return {"kind": "goods_receipt", "id": str(r["id"]), "number": r["receipt_number"], "date": r["receipt_date"].isoformat(),
            "amount": f"{_d(r['amount']):.2f}", "paper": "A4", "sent": {"state": "not_sent", "sent_at": None, "viewed_at": None},
            "batal": batal, "status": "batal" if batal else "terbit"}


async def kelompok_grn(conn, tenant_id: str, gr) -> list:
    """Bundel berjangkar GRN (kamus FE pembelian): po (PO asal) · grn (ITU SENDIRI) · faktur (tagihan bertaut AKTIF; dokumen SAMA dgn bundel
    tagihan = TD.dok_tagihan)."""
    from . import tagihan_daftar as TD
    from .po_daftar import dok_purchase_order
    kel = []
    po = await conn.fetchrow("SELECT id, po_number, po_date, total_amount, status, sent_at FROM purchase_orders WHERE tenant_id = $1 AND id = $2",
                             tenant_id, gr["purchase_order_id"])
    if po:
        kel.append({"key": "po", "label": "Pesanan Pembelian", "docs": [dok_purchase_order(po)]})
    kel.append({"key": "grn", "label": "Penerimaan Barang", "docs": [dok_goods_receipt(gr)]})
    bills = await conn.fetch(
        f"""SELECT DISTINCT b.id, b.invoice_number, b.issue_date, b.amount, {TD.sql_keadaan_dok()} AS keadaan
            FROM bills b {TD.join_rincian()}
            WHERE b.tenant_id = $1 AND b.id IN (
                SELECT bi.bill_id FROM goods_receipt_bill_links l JOIN goods_receipt_items i ON i.id = l.goods_receipt_item_id
                JOIN bill_items bi ON bi.id = l.bill_item_id WHERE i.goods_receipt_id = $2 AND l.status = 'active')
            ORDER BY b.issue_date, b.invoice_number""", tenant_id, gr["id"])
    if bills:
        kel.append({"key": "faktur", "label": "Faktur Pembelian", "docs": [TD.dok_tagihan(b, b["keadaan"]) for b in bills]})
    return kel


# ---------------------------------------------------------------- riwayat
MODUL_GRN = {"goods_receipt": "purchase_order", "bill": "purchase_invoice"}


async def riwayat_grn(conn, tenant_id: str, gr_id, boleh: Callable[[str], Awaitable[bool]], limit: int = 200) -> Optional[dict]:
    """Riwayat GRN, bentuk SAMA dengan riwayat ND/tagihan/PO (so_riwayat._selesaikan). Sumber beraktor (tak ditebak):
    diterima (goods_receipts.created_by) · dibatalkan (voided_by + alasan) · tertaut tagihan (tautan aktif/dibalik; aktor: tautan
    'terima_dulu' lahir saat tagihan DIPOSTING -> bills.posted_by; 'tagih_dulu'/'legacy' lahir saat GRN -> goods_receipts.created_by).
    Tanpa izin baca faktur pembelian -> nomor tagihan tak disebut, modul masuk omitted. None = GRN tak ada di tenant ini."""
    from .so_riwayat import _Kumpul, _rp, _selesaikan
    g = await conn.fetchrow(
        """SELECT g.id, g.receipt_number, g.created_at, g.created_by, g.voided_at, g.voided_by, g.voided_reason,
                  (SELECT COALESCE(SUM(i.amount), 0) FROM goods_receipt_items i WHERE i.goods_receipt_id = g.id) AS amount
           FROM goods_receipts g WHERE g.id = $1 AND g.tenant_id = $2""", gr_id, tenant_id)
    if not g:
        return None
    izin = {}
    for modul in MODUL_GRN.values():
        if modul not in izin:
            izin[modul] = bool(await boleh(modul))
    lihat = {j: izin[m] for j, m in MODUL_GRN.items()}
    omitted = sorted({m for m in izin if not izin[m]})
    k = _Kumpul()
    n = g["receipt_number"]
    k.tambah(g["created_at"], "GRN_DITERIMA", f"Penerimaan barang {n} {_rp(g['amount'])} dicatat", g["created_by"], "goods_receipt", g["id"], n)
    alasan = f": {g['voided_reason']}" if g["voided_reason"] else ""
    k.tambah(g["voided_at"], "GRN_DIBATALKAN", f"Penerimaan barang {n} dibatalkan{alasan}", g["voided_by"], "goods_receipt", g["id"], n)
    for t in await conn.fetch(
        """SELECT l.id AS kunci, l.kind, l.quantity, l.created_at, l.status, l.reversed_at, b.invoice_number, b.posted_by
           FROM goods_receipt_bill_links l JOIN goods_receipt_items i ON i.id = l.goods_receipt_item_id
           JOIN bill_items bi ON bi.id = l.bill_item_id JOIN bills b ON b.id = bi.bill_id AND b.tenant_id = $1
           WHERE i.goods_receipt_id = $2""", tenant_id, gr_id):
        ke = f" ke faktur pembelian {t['invoice_number']}" if lihat["bill"] and t["invoice_number"] else ""
        aktor = t["posted_by"] if t["kind"] == "terima_dulu" else g["created_by"]
        k.tambah(t["created_at"], "GRN_DITAGIH", f"Penerimaan barang {n} tertaut{ke} ({t['quantity'].normalize():f} unit)", aktor,
                 "goods_receipt", g["id"], n, kunci=t["kunci"])
        k.tambah(t["reversed_at"], "GRN_TAUTAN_DILEPAS", f"Tautan penerimaan barang {n}{ke} dilepas", None, "goods_receipt", g["id"], n,
                 kunci=t["kunci"])
    keluar, total = await _selesaikan(conn, tenant_id, k, {}, lihat, limit)
    return {"goods_receipt_id": str(g["id"]), "receipt_number": n, "events": keluar, "total": total, "omitted": omitted}
