"""PEMBELIAN-SERAGAM / Pesanan Pembelian (22 Okt 2026, P-U2): data daftar/detail/bundel dihitung SERVER, FE hanya memformat.

  terima_terbuka = status sent|partial_received DAN masih ada sisa terima DAN bertanggal `expected_date`
  terima_hari    = expected_date - hari ini (tanggal BISNIS tenant); None bila tak terbuka; <0 = lewat jadwal
  terima_keadaan = telat | hari_ini | akan | selesai (sudah diterima penuh, tanggal redup) | tanpa
  terima_teks    = "Hari ini" / "Besok" / "24 Okt" / "12 Okt, lewat jadwal" / "—"
  position_text  = Draf | Terkirim ke vendor | Diterima sebagian | Diterima | Ditagih sebagian | Ditagih | Selesai | Dibatalkan
  aksi (pill; putusan MASTER 22 Okt): perlu_dikirim > telat_terima > perlu_ditagih > draf_tagihan -- PARTISI (satu baris = satu pill;
                 ?aksi=k, hitungan summary dan `aksi` baris memakai predikat yang sama). `menunggu_barang` (keadaan normal tiap PO terkirim)
                 dan `perlu_ditutup` (Fully Billed = selesai otomatis, pola NetSuite) BUKAN pill; tutup manual = aksi di detail.
  amount_received/amount_billed = TURUNAN dari baris (qty_diterima x total / qty, 2dp HALF_UP), bukan kolom cache PO.
  DITAGIH (1 Nov 2026, putusan MASTER, cermin SO "faktur draf = belum difaktur" 25 Sep): amount_billed/sisa_ditagih/aksi/status dari bill
                 POSTED saja; porsi bill DRAF terpisah (draft_bill_amount). PAGAR (to-bill, posting, ubah/batal PO) tetap SEMUA bill non-void
                 (cache purchase_order_items.quantity_billed). Dua angka, dua tujuan -- satu predikat per tujuan (PRED_TAGIH).
  DRAF-TAGIHAN (4 Nov 2026, putusan MASTER): belum_ditagih_sesudah_draf = Σ max(diterima - qty PAGAR, 0); perlu_ditagih hanya bila > 0
                 (masih ada yang bisa dibuatkan faktur), draf_tagihan bila sisanya sudah seluruhnya faktur DRAF (pill "Buka faktur draf",
                 draft_bills). Status PO tetap dari bill POSTED (D1). bisa_diterima = SARINGAN daftar (bukan pill/partisi): status bisa-terima
                 penentu terima (po_rencana.status_bisa_terima) + sisa terima > 0.
  documents      = bundel (dokumen_po) tanpa rekap -- SATU definisi dgn /purchase-orders/{id}/documents.
  JASA-TANPA-TERIMA (5 Nov 2026, putusan pemilik LANGSUNG): baris jasa (grn_rencana.sql_jasa) tak dihitung sisa_terima / status terima;
                 dasar belum_ditagih jasa = qty PESANAN (barang = diterima). PO 'sent' berisi jasa -> perlu_ditagih. Kontrak pill detail/daftar:
                 terima_tersedia (ada barang bersisa terima, status bisa-terima) + tagih_tersedia (ada yang bisa dibuatkan faktur).
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from .dokumen_bagikan import lapis_kirim  # L7: sent bundel dari tautan bagikan

from .grn_rencana import sql_jasa
from .so_posisi import BULAN
from . import tagihan_daftar as TD  # dok tagihan: SATU pembentuk dgn bundel tagihan (bills-kontrak)

NOL = Decimal("0")
DUA = Decimal("0.01")
STATUS_TERIMA_TERBUKA = ("sent", "partial_received")
STATUS_SUDAH_TERIMA = ("received", "partial_billed", "billed", "closed")
STATUS_BISA_TAGIH = ("sent", "partial_received", "received", "partial_billed")  # sent: baris jasa (barang 'sent' = belum_ditagih 0)
# PO tanpa sisa ditagih (22 Okt 2026, temuan nyata kaos PO-2610-0001; pola NetSuite: baris PO yang DITUTUP tak bisa ditagih):
# dibatalkan ATAU selesai -> sisa_ditagih = 0 di baris/detail (fakta_po) dan with_totals sum_sisa (sql_sisa_ditagih). SATU definisi.
# 1 Nov 2026 (D3, cermin SO: SO draf tak "berjalan" -> belum difaktur 0, so_aksi.fakta `jalan`): + draft. Himpunan ini juga dipakai
# summary.pending_amount dan agregat (po_agregat.AKTIF_TIDAK).
STATUS_TANPA_SISA_TAGIH = ("draft", "cancelled", "closed")
# Predikat bill per tujuan (alias bills = b). POSTED = angka tampil; DRAF = porsi draf terpisah; PAGAR = batas qty (non-void, draf ikut).
PRED_TAGIH = {
    "posted": "COALESCE(b.status_v2, b.status) NOT IN ('draft', 'void')",
    "draf": "COALESCE(b.status_v2, b.status) = 'draft'",
    "pagar": "COALESCE(b.status_v2, b.status) <> 'void'",
}


def sql_qty_tagih(jenis: str, i: str = "i", tenant: str = "$2") -> str:
    """Σ qty bill_items bertaut ke baris PO `i` pada bill tenant `tenant` dengan predikat PRED_TAGIH[jenis]. SATU sumber."""
    return (f"COALESCE((SELECT SUM(bi.quantity) FROM bill_items bi JOIN bills b ON b.id = bi.bill_id "
            f"WHERE bi.purchase_order_item_id = {i}.id AND b.tenant_id = {tenant} AND {PRED_TAGIH[jenis]}), 0)")
AKSI = ("perlu_dikirim", "telat_terima", "perlu_ditagih", "draf_tagihan")  # urutan desak (PARTISI)
SARINGAN = ("bisa_diterima",)  # ?aksi= daftar saja: bukan pill, tak dihitung summary
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


def aksi_po(status: Optional[str], expected_date: Optional[date], sisa_terima, belum_ditagih, hari_ini: date,
            belum_ditagih_sesudah_draf=None) -> Optional[str]:
    """Pure. Satu kunci PALING mendesak atau None. belum_ditagih = Σ(dasar - ditagih POSTED) baris PO (dasar: barang = diterima, jasa =
    dipesan); belum_ditagih_sesudah_draf = Σ(dasar - ditagih PAGAR) (None = sama dengan belum_ditagih)."""
    if status == "draft":
        return "perlu_dikirim"
    telat = (status in STATUS_TERIMA_TERBUKA and _d(sisa_terima) > NOL and expected_date is not None and expected_date < hari_ini)
    if telat:
        return "telat_terima"
    if status in STATUS_BISA_TAGIH and _d(belum_ditagih) > NOL:
        sesudah = belum_ditagih if belum_ditagih_sesudah_draf is None else belum_ditagih_sesudah_draf
        return "perlu_ditagih" if _d(sesudah) > NOL else "draf_tagihan"
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
SQL_AGREGAT = f"""
    LEFT JOIN LATERAL (
        SELECT COALESCE(SUM(CASE WHEN q.jasa THEN 0 ELSE GREATEST(i.quantity - COALESCE(i.quantity_received, 0), 0) END), 0) AS sisa_terima,
               COALESCE(SUM(GREATEST(q.dasar - q.posted, 0)), 0) AS belum_ditagih,
               COALESCE(SUM(GREATEST(q.dasar - q.pagar, 0)), 0) AS belum_ditagih_sesudah_draf,
               COALESCE(SUM(ROUND(COALESCE(i.quantity_received, 0) * i.total / NULLIF(i.quantity, 0), 2)), 0) AS amount_received,
               COALESCE(SUM(ROUND(q.posted * i.total / NULLIF(i.quantity, 0), 2)), 0) AS amount_billed,
               COALESCE(SUM(ROUND(q.draf * i.total / NULLIF(i.quantity, 0), 2)), 0) AS draft_bill_amount
        FROM purchase_order_items i
        CROSS JOIN LATERAL (SELECT {sql_qty_tagih("posted", "i", "po.tenant_id")} AS posted,
                                   {sql_qty_tagih("draf", "i", "po.tenant_id")} AS draf,
                                   {sql_qty_tagih("pagar", "i", "po.tenant_id")} AS pagar,
                                   {sql_jasa("i", "po.tenant_id")} AS jasa) q0
        CROSS JOIN LATERAL (SELECT q0.*, CASE WHEN q0.jasa THEN i.quantity ELSE COALESCE(i.quantity_received, 0) END AS dasar) q
        WHERE i.po_id = po.id
    ) ag ON true"""


def sql_aksi(aksi: str, hari: str, po: str = "po", ag: str = "ag", status_terima: tuple = ()) -> str:
    """Predikat SQL setara aksi_po(...) == aksi (PARTISI) + saringan bisa_diterima (status_terima = po_rencana.status_bisa_terima(flag)).
    `hari` = placeholder tanggal bisnis (mis. '$2')."""
    telat = (f"({po}.status IN ('sent', 'partial_received') AND {ag}.sisa_terima > 0 AND {po}.expected_date IS NOT NULL "
             f"AND {po}.expected_date < {hari}::date)")
    tagih = f"({po}.status IN ({', '.join(repr(s) for s in STATUS_BISA_TAGIH)}) AND {ag}.belum_ditagih > 0 AND NOT {telat})"
    if aksi == "bisa_diterima":
        return f"({po}.status IN ({', '.join(repr(s) for s in status_terima) or 'NULL'}) AND {ag}.sisa_terima > 0)"
    return {
        "perlu_dikirim": f"({po}.status = 'draft')",
        "telat_terima": telat,
        "perlu_ditagih": f"({tagih} AND {ag}.belum_ditagih_sesudah_draf > 0)",
        "draf_tagihan": f"({tagih} AND {ag}.belum_ditagih_sesudah_draf <= 0)",
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


def sql_sisa_ditagih(po: str = "po", ag: str = "ag") -> str:
    """Ekspresi SQL sisa ditagih per PO -- padanan fakta_po (STATUS_TANPA_SISA_TAGIH -> 0)."""
    daftar = ", ".join(f"'{s}'" for s in STATUS_TANPA_SISA_TAGIH)
    return f"(CASE WHEN {po}.status IN ({daftar}) THEN 0 ELSE GREATEST({po}.total_amount - {ag}.amount_billed, 0) END)"


def sql_berjalan(po: str = "po") -> str:
    """Predikat SQL PO "berjalan" = status di luar STATUS_TANPA_SISA_TAGIH (satu himpunan dengan sql_sisa_ditagih)."""
    return f"{po}.status NOT IN ({', '.join(repr(x) for x in STATUS_TANPA_SISA_TAGIH)})"


async def fakta_po(conn, tenant_id: str, po_ids: list, hari_ini: date) -> dict:
    """{po_id: {aksi, terima_*, position_text(status ikut), amount_received, amount_billed, sisa_ditagih}} untuk po_ids — fungsi
    YANG SAMA untuk baris daftar dan detail. Turunan dari baris PO (bukan kolom cache)."""
    from .grn_rencana import FLAG
    from .po_rencana import status_bisa_terima
    if not po_ids:
        return {}
    out = {}
    for r in await conn.fetch(
        f"""SELECT po.id, po.status, po.expected_date, po.total_amount, ag.sisa_terima, ag.belum_ditagih, ag.amount_received, ag.amount_billed,
                   ag.draft_bill_amount, ag.belum_ditagih_sesudah_draf,
                   EXISTS (SELECT 1 FROM tenant_features tf WHERE tf.tenant_id = po.tenant_id AND tf.feature = '{FLAG}' AND tf.enabled) AS aturan_d
            FROM purchase_orders po {SQL_AGREGAT}
            WHERE po.tenant_id = $1 AND po.id = ANY($2::uuid[])""", tenant_id, list(po_ids)):
        sisa = NOL if r["status"] in STATUS_TANPA_SISA_TAGIH else r2(r["total_amount"]) - r2(r["amount_billed"])
        out[r["id"]] = {
            **terima_fakta(r["status"], r["expected_date"], r["sisa_terima"], hari_ini),
            "position_text": position_text(r["status"]),
            "aksi": aksi_po(r["status"], r["expected_date"], r["sisa_terima"], r["belum_ditagih"], hari_ini, r["belum_ditagih_sesudah_draf"]),
            "amount_received": r2(r["amount_received"]), "amount_billed": r2(r["amount_billed"]),
            "sisa_ditagih": max(sisa, NOL), "draft_bill_amount": r2(r["draft_bill_amount"]),
            "belum_ditagih_sesudah_draf": _d(r["belum_ditagih_sesudah_draf"]),
            # JASA-TANPA-TERIMA: kontrak pill detail (FE tak menebak dari status): Terima hanya bila ada BARANG bersisa terima
            "terima_tersedia": r["status"] in status_bisa_terima(bool(r["aturan_d"])) and _d(r["sisa_terima"]) > NOL,
            "tagih_tersedia": r["status"] in STATUS_BISA_TAGIH and _d(r["belum_ditagih_sesudah_draf"]) > NOL,
        }
    return out


async def faktur_draf_po(conn, tenant_id: str, po_ids: list) -> dict:
    """{po_id: [{id, bill_number}]} faktur DRAF bertaut baris PO (pill draf_tagihan "Buka faktur draf"); SATU kueri untuk daftar + detail."""
    out = {i: [] for i in po_ids}
    if po_ids:
        for r in await conn.fetch(
                f"""SELECT DISTINCT poi.po_id, b.id, b.invoice_number FROM bill_items bi JOIN bills b ON b.id = bi.bill_id AND b.tenant_id = $1
                    JOIN purchase_order_items poi ON poi.id = bi.purchase_order_item_id
                    WHERE poi.po_id = ANY($2::uuid[]) AND {PRED_TAGIH["draf"]} ORDER BY b.invoice_number""", tenant_id, list(po_ids)):
            out.setdefault(r["po_id"], []).append({"id": str(r["id"]), "bill_number": r["invoice_number"]})
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


def dok_purchase_order(r) -> dict:
    batal = r["status"] == "cancelled"
    return {"kind": "purchase_order", "id": str(r["id"]), "number": r["po_number"], "date": _tgl(r["po_date"]), "amount": _uang(r["total_amount"]),
            "paper": "A4",
            "sent": ({"state": "sent", "sent_at": r["sent_at"].isoformat(), "viewed_at": None} if r["sent_at"] else _belum_kirim()),
            "batal": batal, "status": "batal" if batal else ("draf" if r["status"] == "draft" else "terbit")}


def dok_bill(b) -> dict:
    """Dokumen tagihan di bundel PO / uang muka vendor = TD.dok_tagihan (22 Okt 2026): status draf | batal | lunas | diposting + medan paid,
    keadaan dari TD.sql_keadaan_dok() -- SAMA dengan bundel tagihan sendiri. Dulu tagihan LUNAS tampil "diposting" di sini. Baris wajib
    membawa `keadaan` (kueri pemanggil memakai sql_dok_bill + TD.join_rincian)."""
    return TD.dok_tagihan(b, b["keadaan"])


def sql_dok_bill(a: str = "b") -> str:
    """Kolom yang dibutuhkan dok_bill; pemanggil WAJIB menambah TD.join_rincian('$1', a)."""
    return f"{a}.id, {a}.invoice_number, {a}.issue_date, {a}.amount, {TD.sql_keadaan_dok(a)} AS keadaan"


def dok_vendor_deposit(d) -> dict:
    st = d["status"]
    return {"kind": "vendor_deposit", "id": str(d["id"]), "number": d["deposit_number"], "date": _tgl(d["deposit_date"]), "amount": _uang(d["amount"]),
            "paper": "A5-landscape", "sent": _belum_kirim(), "batal": st == "void", "status": "batal" if st == "void" else ("draf" if st == "draft" else "dibayar")}


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
        f"""SELECT b.purchase_order_id, {sql_dok_bill()} FROM bills b {TD.join_rincian()}
            WHERE b.tenant_id = $1 AND b.purchase_order_id = ANY($2::uuid[]) ORDER BY b.issue_date, b.invoice_number""", tenant_id, ids):
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
        kel = [{"key": "po", "label": "Pesanan Pembelian", "docs": [dok_purchase_order(r)]}]
        if bills.get(pid):
            kel.append({"key": "faktur", "label": "Faktur Pembelian", "docs": [dok_bill(b) for b in bills[pid]]})
        if vds.get(pid):
            kel.append({"key": "uang_muka", "label": "Uang Muka", "docs": [dok_vendor_deposit(d) for d in vds[pid]]})
        if bayar.get(pid):
            kel.append({"key": "bayar", "label": "Pembayaran", "docs": bayar[pid]})
        hasil[pid] = kel
    return await lapis_kirim(conn, tenant_id, hasil)  # L7: terkirim/dibuka dari tautan bagikan


def documents_baris(kelompok: list) -> list:
    """documents[{type}] baris daftar = dokumen bundel (urut kelompok); jenis dokumen = kind."""
    return [{"type": d["kind"]} for g in kelompok for d in g["docs"]]


# qty TERIKAT bill per baris PO (PAGAR) = Σ bill_items bertaut pada bill TIDAK void (draf ikut). Alias baris PO = i; $2 = tenant.
SQL_QTY_TAGIH = sql_qty_tagih("pagar")


# ---- Sinkron tagih (P-U2 sisi tulis): qty/jumlah/status tertagih TURUNAN dari bill_items bertaut --------------------------------------
async def sinkron_tagih(conn, tenant_id: str, po_id) -> dict:
    """Hitung ulang dari bill_items bertaut (`bill_items.purchase_order_item_id`): purchase_order_items.quantity_billed = qty TERIKAT bill
    non-void (PAGAR, draf ikut); purchase_orders.amount_billed (cache) dan STATUS dari bill POSTED saja (1 Nov 2026 D1, pola NetSuite: status
    PO dari vendor bill terposting). SATU fungsi untuk `/to-bill`, posting bill (3 jalur bills_service) DAN pemulihan saat bill dihapus/di-void.
    Wajib di transaksi pemanggil, SESUDAH PO dikunci (FOR UPDATE). Status hanya bergeser di keluarga terima/tagih
    (sent·partial_received·received·partial_billed·billed); draft/closed/cancelled tak disentuh. -> {status, amount_billed}."""
    po = await conn.fetchrow("SELECT id, status FROM purchase_orders WHERE id = $1 AND tenant_id = $2", po_id, tenant_id)
    if not po:
        return {}
    await conn.execute(f"UPDATE purchase_order_items i SET quantity_billed = {SQL_QTY_TAGIH} WHERE i.po_id = $1", po_id, tenant_id)
    baris = [dict(b) for b in await conn.fetch(
        f"""SELECT i.quantity, i.quantity_received, {sql_qty_tagih("posted")} AS quantity_billed, i.total, {sql_jasa("i", "$2")} AS jasa
            FROM purchase_order_items i WHERE i.po_id = $1""", po_id, tenant_id)]  # quantity_billed di sini = POSTED (angka/status)
    jml = jumlah_turunan(baris)
    q_tagih = sum((_d(b["quantity_billed"]) for b in baris), NOL)
    semua_tagih = bool(baris) and all(_d(b["quantity_billed"]) >= _d(b["quantity"]) for b in baris)
    barang = [b for b in baris if not b["jasa"]]  # JASA-TANPA-TERIMA: status terima dari baris barang saja
    semua_terima = bool(barang) and all(_d(b["quantity_received"]) >= _d(b["quantity"]) for b in barang)
    q_terima = sum((_d(b["quantity_received"]) for b in barang), NOL)
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


async def po_terkait_bill(conn, tenant_id: str, bill_id) -> list:
    """id PO yang terkait bill ini lewat bill_items.purchase_order_item_id. Panggil SEBELUM bill DIHAPUS (baris tertaut ikut hilang); untuk void cukup sesudah."""
    return [r["po_id"] for r in await conn.fetch(
        """SELECT DISTINCT i.po_id FROM bill_items bi JOIN purchase_order_items i ON i.id = bi.purchase_order_item_id
           JOIN purchase_orders po ON po.id = i.po_id WHERE bi.bill_id = $1 AND po.tenant_id = $2""", bill_id, tenant_id)]


async def sinkron_po_bill(conn, tenant_id: str, bill_id) -> list:
    """Sesudah bill berubah status (posting: D1 1 Nov 2026) -> sinkron PO yang tertaut. Transaksi pemanggil."""
    po_ids = await po_terkait_bill(conn, tenant_id, bill_id)
    return await pulihkan_tagih(conn, tenant_id, po_ids) if po_ids else []


async def pulihkan_tagih(conn, tenant_id: str, po_ids: list) -> list:
    """Pemulihan qty tertagih PO sesudah bill di-void/dihapus (BACKEND memanggil dari bills.py di transaksi yang SAMA, SESUDAH bill berubah):
    tiap PO dikunci (advisory PO:{tenant}:{id} + FOR UPDATE; urutan id menaik -> tanpa kebuntuan) lalu sinkron_tagih. -> [{po_id, status}]."""
    hasil = []
    for pid in sorted(set(po_ids), key=str):
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"PO:{tenant_id}:{pid}")
        await conn.fetchrow("SELECT id FROM purchase_orders WHERE id = $1 AND tenant_id = $2 FOR UPDATE", pid, tenant_id)
        h = await sinkron_tagih(conn, tenant_id, pid)
        hasil.append({"po_id": pid, **h})
    return hasil
