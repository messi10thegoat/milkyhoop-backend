"""PEMBELIAN-SERAGAM / Pesanan Pembelian (22 Okt 2026, P-U2): data daftar/detail/bundel dihitung SERVER, FE hanya memformat.

  terima_terbuka = status sent|partial_received DAN masih ada sisa terima DAN bertanggal `expected_date`
  terima_hari    = expected_date - hari ini (tanggal BISNIS tenant); None bila tak terbuka; <0 = lewat jadwal
  terima_keadaan = telat | hari_ini | akan | selesai (sudah diterima penuh, tanggal redup) | tanpa
  terima_teks    = "Hari ini" / "Besok" / "24 Okt" / "12 Okt, lewat jadwal" / "—"
  position_text  = Draf | Terkirim ke vendor | Diterima sebagian | Diterima | Ditagih sebagian | Ditagih | Selesai | Dibatalkan
  aksi (pill; putusan MASTER 22 Okt): perlu_dikirim > telat_terima > perlu_ditagih -- PARTISI (satu baris = satu pill;
                 ?aksi=k, hitungan summary dan `aksi` baris memakai predikat yang sama). `menunggu_barang` (keadaan normal tiap PO terkirim)
                 dan `perlu_ditutup` (Fully Billed = selesai otomatis, pola NetSuite) BUKAN pill; tutup manual = aksi di detail.
  amount_received/amount_billed = TURUNAN dari baris (qty_diterima x total / qty, 2dp HALF_UP), bukan kolom cache PO.
  documents      = bundel (dokumen_po) tanpa rekap -- SATU definisi dgn /purchase-orders/{id}/documents.
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from .so_posisi import BULAN

NOL = Decimal("0")
DUA = Decimal("0.01")
STATUS_TERIMA_TERBUKA = ("sent", "partial_received")
STATUS_SUDAH_TERIMA = ("received", "partial_billed", "billed", "closed")
STATUS_BISA_TAGIH = ("partial_received", "received", "partial_billed")
AKSI = ("perlu_dikirim", "telat_terima", "perlu_ditagih")  # urutan desak
POSISI = {"draft": "Draf", "sent": "Terkirim ke vendor", "partial_received": "Diterima sebagian", "received": "Diterima",
          "partial_billed": "Ditagih sebagian", "billed": "Ditagih", "closed": "Selesai", "cancelled": "Dibatalkan"}


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else NOL


def r2(x) -> Decimal:
    return _d(x).quantize(DUA, rounding=ROUND_HALF_UP)


def position_text(status: Optional[str]) -> str:
    """Kolom Status FE (SATU fungsi: baris daftar, detail, anchor bundel)."""
    return POSISI.get(status or "", status or "")


def terima_fakta(status: Optional[str], expected_date: Optional[date], sisa_terima, hari_ini: date) -> dict:
    """Pure. sisa_terima = Σ(qty - diterima) baris PO."""
    terbuka = status in STATUS_TERIMA_TERBUKA and _d(sisa_terima) > NOL and expected_date is not None
    hari = (expected_date - hari_ini).days if terbuka else None
    tgl = f"{expected_date.day} {BULAN[expected_date.month - 1]}" if expected_date is not None else None
    if terbuka:
        keadaan = "telat" if hari < 0 else "hari_ini" if hari == 0 else "akan"
        teks = f"{tgl}, lewat jadwal" if hari < 0 else "Hari ini" if hari == 0 else "Besok" if hari == 1 else tgl
    elif status in STATUS_SUDAH_TERIMA and expected_date is not None:
        keadaan, teks = "selesai", tgl
    else:
        keadaan, teks = "tanpa", "—"
    return {"terima_terbuka": terbuka, "terima_hari": hari, "terima_keadaan": keadaan, "terima_teks": teks}


def aksi_po(status: Optional[str], expected_date: Optional[date], sisa_terima, belum_ditagih, hari_ini: date) -> Optional[str]:
    """Pure. Satu kunci PALING mendesak atau None. belum_ditagih = Σ(diterima - ditagih) baris PO."""
    if status == "draft":
        return "perlu_dikirim"
    telat = (status in STATUS_TERIMA_TERBUKA and _d(sisa_terima) > NOL and expected_date is not None and expected_date < hari_ini)
    if telat:
        return "telat_terima"
    if status in STATUS_BISA_TAGIH and _d(belum_ditagih) > NOL:
        return "perlu_ditagih"
    return None


def jumlah_turunan(baris: list) -> dict:
    """Pure. baris: [{quantity, quantity_received, quantity_billed, total}] -> amount_received/amount_billed (2dp HALF_UP, per baris)."""
    rec = bil = NOL
    for b in baris:
        q = _d(b["quantity"])
        if q <= NOL:
            continue
        rec += r2(_d(b.get("quantity_received")) * _d(b["total"]) / q)
        bil += r2(_d(b.get("quantity_billed")) * _d(b["total"]) / q)
    return {"amount_received": rec, "amount_billed": bil}


# ---- SQL (alias po = purchase_orders, ag = LATERAL agregat; $1 = tenant) --------------------------------------------------------------
SQL_AGREGAT = """
    LEFT JOIN LATERAL (
        SELECT COALESCE(SUM(GREATEST(i.quantity - COALESCE(i.quantity_received, 0), 0)), 0) AS sisa_terima,
               COALESCE(SUM(GREATEST(COALESCE(i.quantity_received, 0) - COALESCE(i.quantity_billed, 0), 0)), 0) AS belum_ditagih,
               COALESCE(SUM(ROUND(COALESCE(i.quantity_received, 0) * i.total / NULLIF(i.quantity, 0), 2)), 0) AS amount_received,
               COALESCE(SUM(ROUND(COALESCE(i.quantity_billed, 0) * i.total / NULLIF(i.quantity, 0), 2)), 0) AS amount_billed
        FROM purchase_order_items i WHERE i.po_id = po.id
    ) ag ON true"""


def sql_aksi(aksi: str, hari: str, po: str = "po", ag: str = "ag") -> str:
    """Predikat SQL setara aksi_po(...) == aksi (PARTISI). `hari` = placeholder tanggal bisnis (mis. '$2')."""
    telat = (f"({po}.status IN ('sent', 'partial_received') AND {ag}.sisa_terima > 0 AND {po}.expected_date IS NOT NULL "
             f"AND {po}.expected_date < {hari}::date)")
    return {
        "perlu_dikirim": f"({po}.status = 'draft')",
        "telat_terima": telat,
        "perlu_ditagih": (f"({po}.status IN ('partial_received', 'received', 'partial_billed') AND {ag}.belum_ditagih > 0 "
                          f"AND NOT {telat})"),
    }[aksi]


def syarat_cari(search: Optional[str], mulai: int, po: str = "po") -> tuple:
    """(predikat, parameter) ?search= daftar PO ($1 = tenant); tiap kata satu parameter mulai $mulai, semua kata wajib cocok.
    SATU sumber untuk daftar DAN /summary?search=. Kosong -> (None, [])."""
    if not isinstance(search, str):  # panggilan langsung (uji) tanpa argumen = objek Query -> tanpa saring
        return None, []
    kata = search.strip().split()
    if not kata:
        return None, []
    syarat = [(f"({po}.po_number ILIKE ${mulai + i} OR {po}.vendor_name ILIKE ${mulai + i} OR {po}.ref_no ILIKE ${mulai + i} "
               f"OR {po}.notes ILIKE ${mulai + i} OR {po}.vendor_id IN (SELECT v.id FROM vendors v WHERE v.tenant_id = {po}.tenant_id "
               f"AND (v.code ILIKE ${mulai + i} OR v.name ILIKE ${mulai + i} OR v.search_text ILIKE ${mulai + i})))") for i in range(len(kata))]
    return (syarat[0] if len(syarat) == 1 else f"({' AND '.join(syarat)})"), [f"%{w}%" for w in kata]


async def tanggal_hari_ini(conn, tenant_id: str) -> date:
    from ..utils.tanggal_tenant import tanggal_dokumen
    return await tanggal_dokumen(conn, tenant_id)


async def fakta_po(conn, tenant_id: str, po_ids: list, hari_ini: date) -> dict:
    """{po_id: {aksi, terima_*, position_text(status ikut), amount_received, amount_billed, sisa_ditagih}} untuk po_ids — fungsi
    YANG SAMA untuk baris daftar dan detail. Turunan dari baris PO (bukan kolom cache)."""
    if not po_ids:
        return {}
    out = {}
    for r in await conn.fetch(
        f"""SELECT po.id, po.status, po.expected_date, po.total_amount, ag.sisa_terima, ag.belum_ditagih, ag.amount_received, ag.amount_billed
            FROM purchase_orders po {SQL_AGREGAT}
            WHERE po.tenant_id = $1 AND po.id = ANY($2::uuid[])""", tenant_id, list(po_ids)):
        aktif = r["status"] != "cancelled"
        sisa = r2(r["total_amount"]) - r2(r["amount_billed"]) if aktif else NOL
        out[r["id"]] = {
            **terima_fakta(r["status"], r["expected_date"], r["sisa_terima"], hari_ini),
            "position_text": position_text(r["status"]),
            "aksi": aksi_po(r["status"], r["expected_date"], r["sisa_terima"], r["belum_ditagih"], hari_ini),
            "amount_received": r2(r["amount_received"]), "amount_billed": r2(r["amount_billed"]),
            "sisa_ditagih": max(sisa, NOL),
        }
    return out


# ---- BUNDEL dokumen --------------------------------------------------------------------------------------------------------------------
JENIS_DOK = ("purchase_order", "bill", "vendor_deposit", "payment_out")


def _tgl(v) -> Optional[str]:
    return v.isoformat() if isinstance(v, date) else (str(v)[:10] if v else None)


def _uang(v) -> str:
    return f"{_d(v):.2f}"


def _belum_kirim() -> dict:
    return {"state": "none", "sent_at": None, "viewed_at": None}


SQL_BAYAR = """SELECT DISTINCT bp.id, bp.payment_number, bp.payment_date, bp.total_amount, bpa.bill_id
               FROM bill_payments_v2 bp JOIN bill_payment_allocations bpa ON bpa.payment_id = bp.id
               WHERE bp.tenant_id = $1 AND bpa.bill_id = ANY($2::uuid[]) AND bp.status = 'posted'
               ORDER BY bp.payment_date, bp.payment_number"""


async def dokumen_po(conn, tenant_id: str, po_ids: list) -> dict:
    """{po_id: [kelompok]} bundel PO: po (PO itu sendiri) · faktur (bill bertaut purchase_order_id; draf & void tetap tampil) ·
    uang_muka (vendor_deposits purchase_order_id; tak void) · bayar (Pembayaran Keluar posted atas bill-bill itu, via alokasi).
    Pembentuk SATU tempat; documents[] baris daftar = turunannya (documents_baris)."""
    if not po_ids:
        return {}
    ids = list(po_ids)
    po = {r["id"]: r for r in await conn.fetch(
        "SELECT id, po_number, po_date, total_amount, status, sent_at FROM purchase_orders WHERE tenant_id = $1 AND id = ANY($2::uuid[])",
        tenant_id, ids)}
    bills: dict = {}
    for b in await conn.fetch(
        """SELECT id, purchase_order_id, invoice_number, issue_date, grand_total, status_v2, status FROM bills
           WHERE tenant_id = $1 AND purchase_order_id = ANY($2::uuid[]) ORDER BY issue_date, invoice_number""", tenant_id, ids):
        bills.setdefault(b["purchase_order_id"], []).append(b)
    vds: dict = {}
    for d in await conn.fetch(
        """SELECT id, purchase_order_id, deposit_number, deposit_date, amount, status FROM vendor_deposits
           WHERE tenant_id = $1 AND purchase_order_id = ANY($2::uuid[]) AND status <> 'void' ORDER BY deposit_date, deposit_number""",
        tenant_id, ids):
        vds.setdefault(d["purchase_order_id"], []).append(d)
    bayar: dict = {}
    bill_ke_po = {b["id"]: p for p, bs in bills.items() for b in bs}
    if bill_ke_po:
        for p in await conn.fetch(SQL_BAYAR, tenant_id, list(bill_ke_po)):
            lst = bayar.setdefault(bill_ke_po[p["bill_id"]], [])
            if not any(x["id"] == str(p["id"]) for x in lst):
                lst.append({"kind": "payment_out", "id": str(p["id"]), "number": p["payment_number"], "date": _tgl(p["payment_date"]),
                            "amount": _uang(p["total_amount"]), "paper": "A5-landscape", "sent": _belum_kirim(),
                            "batal": False, "status": "dibayar"})
    hasil = {}
    for pid in ids:
        r = po.get(pid)
        if not r:
            continue
        batal_po = r["status"] == "cancelled"
        kel = [{"key": "po", "label": "Pesanan Pembelian", "docs": [{
            "kind": "purchase_order", "id": str(r["id"]), "number": r["po_number"], "date": _tgl(r["po_date"]), "amount": _uang(r["total_amount"]),
            "paper": "A4",
            "sent": ({"state": "sent", "sent_at": r["sent_at"].isoformat(), "viewed_at": None} if r["sent_at"] else _belum_kirim()),
            "batal": batal_po, "status": "batal" if batal_po else ("draf" if r["status"] == "draft" else "terbit")}]}]
        if bills.get(pid):
            kel.append({"key": "faktur", "label": "Faktur Pembelian", "docs": [{
                "kind": "bill", "id": str(b["id"]), "number": b["invoice_number"], "date": _tgl(b["issue_date"]), "amount": _uang(b["grand_total"]),
                "paper": "A4", "sent": _belum_kirim(), "batal": (b["status_v2"] or b["status"]) == "void",
                "status": "batal" if (b["status_v2"] or b["status"]) == "void" else ("draf" if (b["status_v2"] or b["status"]) == "draft" else "diposting")}
                for b in bills[pid]]})
        if vds.get(pid):
            kel.append({"key": "uang_muka", "label": "Uang Muka", "docs": [{
                "kind": "vendor_deposit", "id": str(d["id"]), "number": d["deposit_number"], "date": _tgl(d["deposit_date"]), "amount": _uang(d["amount"]),
                "paper": "A5-landscape", "sent": _belum_kirim(), "batal": False,
                "status": "draf" if d["status"] == "draft" else "dibayar"} for d in vds[pid]]})
        if bayar.get(pid):
            kel.append({"key": "bayar", "label": "Pembayaran", "docs": bayar[pid]})
        hasil[pid] = kel
    return hasil


def documents_baris(kelompok: list) -> list:
    """documents[{type}] baris daftar = dokumen bundel (urut kelompok); jenis dokumen = kind."""
    return [{"type": d["kind"]} for g in kelompok for d in g["docs"]]


# qty tertagih per baris PO = Σ bill_items bertaut pada bill TIDAK void (draf ikut). Alias baris PO = i; $2 = tenant.
SQL_QTY_TAGIH = """COALESCE((SELECT SUM(bi.quantity) FROM bill_items bi JOIN bills b ON b.id = bi.bill_id
               WHERE bi.purchase_order_item_id = i.id AND b.tenant_id = $2 AND COALESCE(b.status_v2, b.status) <> 'void'), 0)"""


# ---- Sinkron tagih (P-U2 sisi tulis): qty/jumlah/status tertagih TURUNAN dari bill_items bertaut --------------------------------------
async def sinkron_tagih(conn, tenant_id: str, po_id) -> dict:
    """Hitung ulang purchase_order_items.quantity_billed, purchase_orders.amount_billed (cache) dan status dari bill_items yang bertaut
    (`bill_items.purchase_order_item_id`) pada bill TIDAK void (draf ikut = pola NetSuite: PO 'Partially Billed' sejak bill dibuat dari PO).
    SATU fungsi untuk `/to-bill` DAN pemulihan saat bill dihapus/di-void (dipanggil BACKEND dari bills.py). Wajib di transaksi pemanggil,
    SESUDAH PO dikunci (FOR UPDATE). Status hanya bergeser di keluarga terima/tagih (sent·partial_received·received·partial_billed·billed);
    draft/closed/cancelled tak disentuh. -> {status, amount_billed}."""
    po = await conn.fetchrow("SELECT id, status FROM purchase_orders WHERE id = $1 AND tenant_id = $2", po_id, tenant_id)
    if not po:
        return {}
    await conn.execute(f"UPDATE purchase_order_items i SET quantity_billed = {SQL_QTY_TAGIH} WHERE i.po_id = $1", po_id, tenant_id)
    baris = await conn.fetch(
        "SELECT quantity, quantity_received, quantity_billed, total FROM purchase_order_items WHERE po_id = $1", po_id)
    jml = jumlah_turunan([dict(b) for b in baris])
    q_tagih = sum((_d(b["quantity_billed"]) for b in baris), NOL)
    semua_tagih = bool(baris) and all(_d(b["quantity_billed"]) >= _d(b["quantity"]) for b in baris)
    semua_terima = bool(baris) and all(_d(b["quantity_received"]) >= _d(b["quantity"]) for b in baris)
    q_terima = sum((_d(b["quantity_received"]) for b in baris), NOL)
    status = po["status"]
    if status in ("sent", "partial_received", "received", "partial_billed", "billed"):
        if semua_tagih:
            status = "billed"
        elif q_tagih > NOL:
            status = "partial_billed"
        elif semua_terima:
            status = "received"
        elif q_terima > NOL:
            status = "partial_received"
        else:
            status = "sent"
    await conn.execute("UPDATE purchase_orders SET amount_billed = $2, status = $3, updated_at = NOW() WHERE id = $1",
                       po_id, jml["amount_billed"], status)
    return {"status": status, "amount_billed": jml["amount_billed"]}
