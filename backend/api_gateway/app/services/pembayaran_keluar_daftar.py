"""PEMBELIAN-SERAGAM unit (2) / Pembayaran Keluar (22 Okt 2026, putusan MASTER): data daftar/detail dihitung SERVER dari WRAPPER
bill_payments_v2 (bukan dari jurnal ber-debit AP -- dulu jurnal MANUAL/VENDOR_OPENING_BALANCE ikut tampil sebagai "pembayaran" hantu dan
pembayaran voided/draf tak pernah tampil). Kembaran services/penerimaan_daftar (Penjualan).

  dialokasikan       = Σ bill_payment_allocations.amount_applied pembayaran ini; hanya wrapper POSTED (tabel alokasi tak berstatus:
                       "aktif" = pembayaran posted), lainnya 0.
  bruto              = total_amount + discount_amount (rumus tulis: unapplied = total + diskon − Σ alokasi, routers/bill_payments create).
  belum_dialokasikan = GREATEST(bruto − dialokasikan, 0) -- TURUNAN alokasi, BUKAN cache wrapper (`allocated_amount`/`unapplied_amount`
                       = 0 pada 24/26 pembayaran posted karena bills_service.record_payment tak mengisinya; Law 16).
  aksi               = 'belum_dialokasikan' bila belum_dialokasikan > 0
  alokasi_keadaan    = belum | sebagian | penuh | tanpa (draf/voided) ; alokasi_teks = teks SAMA dgn Penerimaan
  dengan_turunan     = posted: allocated_amount := dialokasikan, unapplied_amount := belum_dialokasikan (kolom cache DB tetap ada).
"""
from decimal import Decimal
from typing import Optional

NOL = Decimal("0")
TOLERANSI = Decimal("0.005")
AKSI = ("belum_dialokasikan",)
TEKS = {"belum": "Belum dialokasikan", "sebagian": "Sebagian dialokasikan", "penuh": "Teralokasi penuh", "tanpa": "—"}


def sql_dialokasi(bp: str = "bp") -> str:
    """Σ alokasi pembayaran `bp` (alias bill_payments_v2) -- hanya posted (tabel alokasi tak berstatus)."""
    return (f"(CASE WHEN {bp}.status = 'posted' THEN COALESCE((SELECT SUM(a.amount_applied) FROM bill_payment_allocations a "
            f"WHERE a.payment_id = {bp}.id), 0) ELSE 0 END)")


def sql_bruto(bp: str = "bp") -> str:
    return f"({bp}.total_amount + COALESCE({bp}.discount_amount, 0))"


def sql_belum(bp: str = "bp") -> str:
    return f"(CASE WHEN {bp}.status = 'posted' THEN GREATEST({sql_bruto(bp)} - {sql_dialokasi(bp)}, 0) ELSE 0 END)"


def alokasi_fakta(status: Optional[str], bruto, dialokasi) -> dict:
    """Pure: bruto = total_amount + discount_amount, dialokasi = Σ alokasi (posted)."""
    if status != "posted" or bruto is None:
        belum, kd, ke_tagihan = NOL, "tanpa", NOL
    else:
        ke_tagihan = Decimal(str(dialokasi or 0))
        belum = max(Decimal(str(bruto)) - ke_tagihan, NOL)
        kd = "penuh" if belum <= TOLERANSI else ("belum" if belum >= Decimal(str(bruto)) - TOLERANSI else "sebagian")
    return {"dialokasikan": float(ke_tagihan), "belum_dialokasikan": float(belum), "aksi": "belum_dialokasikan" if belum > TOLERANSI else None,
            "alokasi_keadaan": kd, "alokasi_teks": TEKS[kd]}


def dengan_turunan(fakta: dict) -> dict:
    """Posted: allocated_amount/unapplied_amount = turunan (fungsi SAMA dgn alokasi_fakta); draf/voided: kunci cache tak ditimpa."""
    if fakta["alokasi_keadaan"] == "tanpa":
        return dict(fakta)
    return {**fakta, "allocated_amount": fakta["dialokasikan"], "unapplied_amount": fakta["belum_dialokasikan"]}


async def bruto_dialokasi(conn, tid: str, ids: list) -> dict:
    """{payment_id: (bruto, dialokasi)} -- ungkapan SAMA dgn sql_belum."""
    if not ids:
        return {}
    return {r["id"]: (r["bruto"], r["dialokasi"]) for r in await conn.fetch(
        f"SELECT bp.id, {sql_bruto('bp')} AS bruto, {sql_dialokasi('bp')} AS dialokasi FROM bill_payments_v2 bp "
        "WHERE bp.tenant_id = $1 AND bp.id = ANY($2::uuid[])", tid, list(ids))}


# ==== KONTRAK DAFTAR seragam Pembayaran Keluar (22 Okt 2026, celah WORKSPACE no. 2; kembar receive_payments + bills 95d7b901) ============
#   position_text = Draf | Batal | alokasi_teks (posted)  -- SATU fungsi baris, detail, jangkar bundel
#   ?search=      = syarat_cari (SATU sumber daftar & /summary?search=)
#   documents     = kelompok_untuk (SATU definisi dgn /bill-payments/{id}/documents)
def posisi_bayar(status: Optional[str], fakta: dict) -> str:
    if status == "draft":
        return "Draf"
    if status == "voided":
        return "Batal"
    return fakta["alokasi_teks"]


def syarat_cari(search, alias: str, mulai: int) -> tuple:
    """(predikat, parameter) ?search= -- tiap kata wajib cocok salah satu: nomor, nama vendor, referensi, catatan, nama/kode vendor master,
    nomor faktur pembelian teralokasi. Alias `alias` = baris bill_payments_v2 (id uuid). Tanpa kata -> (None, [])."""
    kata = (search or "").strip().split() if isinstance(search, str) else []
    if not kata:
        return None, []
    s = []
    for i in range(len(kata)):
        p = f"${mulai + i}"
        s.append(f"({alias}.payment_number ILIKE {p} OR {alias}.vendor_name ILIKE {p} OR {alias}.reference_number ILIKE {p} "
                 f"OR {alias}.notes ILIKE {p} OR {alias}.vendor_id IN (SELECT v.id FROM vendors v WHERE v.tenant_id = $1 "
                 f"AND (v.name ILIKE {p} OR v.code ILIKE {p})) OR EXISTS (SELECT 1 FROM bill_payment_allocations a JOIN bills b "
                 f"ON b.id = a.bill_id AND b.tenant_id = $1 WHERE a.payment_id = {alias}.id::uuid AND b.invoice_number ILIKE {p}))")
    return (s[0] if len(s) == 1 else f"({' AND '.join(s)})"), [f"%{w}%" for w in kata]


def dok_bayar(p) -> dict:
    """Dokumen Pembayaran Keluar ITU SENDIRI: status draf | batal | dibayar (kind payment_out, == bundel PO/tagihan)."""
    st = p["status"] or "draft"
    return {"kind": "payment_out", "id": str(p["id"]), "number": p["payment_number"],
            "date": p["payment_date"].isoformat() if p["payment_date"] else None, "amount": f"{Decimal(str(p['total_amount'] or 0)):.2f}",
            "paper": "A5-landscape", "sent": {"state": "none", "sent_at": None, "viewed_at": None},
            "batal": st == "voided", "status": {"draft": "draf", "voided": "batal"}.get(st, "dibayar")}


async def kelompok_untuk(conn, tid: str, ids: list) -> dict:
    """{payment_id: [kelompok]} bundel Pembayaran Keluar: po (PO asal tagihan teralokasi) · bayar (pembayaran ITU SENDIRI, selalu; draf/batal
    tampil sebagai dirinya) · faktur (tagihan teralokasi -- hanya pembayaran POSTED = alokasi aktif; TD.dok_tagihan, status SAMA dgn bundel
    tagihan) · nota_debit (ND bertaut tagihan-tagihan itu, bukan draf/void). Filter tenant eksplisit."""
    from . import tagihan_daftar as TD
    from .po_daftar import dok_purchase_order
    if not ids:
        return {}
    ids = list(ids)
    bayar = {r["id"]: r for r in await conn.fetch(
        "SELECT id, payment_number, payment_date, total_amount, status FROM bill_payments_v2 WHERE tenant_id = $1 AND id = ANY($2::uuid[])",
        tid, ids)}
    alok: dict = {}
    for r in await conn.fetch(
            """SELECT DISTINCT a.payment_id, a.bill_id FROM bill_payment_allocations a
               JOIN bill_payments_v2 bp ON bp.id = a.payment_id AND bp.tenant_id = $1 AND bp.status = 'posted'
               WHERE a.payment_id = ANY($2::uuid[])""", tid, ids):
        alok.setdefault(r["payment_id"], []).append(r["bill_id"])
    semua = sorted({b for v in alok.values() for b in v}, key=str)
    tgh, nd_per, po_per = {}, {}, {}
    if semua:
        for b in await conn.fetch(
                f"""SELECT b.id, b.invoice_number, b.issue_date, b.amount, {TD.sql_keadaan_dok()} AS keadaan
                    FROM bills b {TD.join_rincian()} WHERE b.tenant_id = $1 AND b.id = ANY($2::uuid[])""", tid, semua):
            tgh[b["id"]] = (b["issue_date"], b["invoice_number"], TD.dok_tagihan(b, b["keadaan"]))
        for r in await conn.fetch(
                """SELECT original_bill_id AS bill_id, id, credit_number, credit_date, total_amount FROM vendor_credits
                   WHERE tenant_id = $1 AND original_bill_id = ANY($2::uuid[]) AND status NOT IN ('draft', 'void')
                   ORDER BY credit_date, credit_number""", tid, semua):
            nd_per.setdefault(r["bill_id"], []).append(
                {"kind": "debit_note", "id": str(r["id"]), "number": r["credit_number"],
                 "date": r["credit_date"].isoformat() if r["credit_date"] else None, "amount": f"{Decimal(str(r['total_amount'] or 0)):.2f}",
                 "paper": "A4", "sent": {"state": "none", "sent_at": None, "viewed_at": None}, "batal": False, "status": "diposting"})
        for r in await conn.fetch(
                """SELECT DISTINCT b.id AS bill_id, po.id, po.po_number, po.po_date, po.total_amount, po.status, po.sent_at
                   FROM bills b JOIN purchase_orders po ON po.tenant_id = $1 AND (po.id = b.purchase_order_id OR po.id IN (
                        SELECT i.po_id FROM bill_items bi JOIN purchase_order_items i ON i.id = bi.purchase_order_item_id WHERE bi.bill_id = b.id))
                   WHERE b.tenant_id = $1 AND b.id = ANY($2::uuid[])""", tid, semua):
            po_per.setdefault(r["bill_id"], []).append(r)
    hasil = {}
    for pid in ids:
        p = bayar.get(pid)
        if not p:
            continue
        bills = alok.get(pid, [])
        kel = []
        pos = {}
        for b in bills:
            for r in po_per.get(b, []):
                pos[r["id"]] = r
        if pos:
            kel.append({"key": "po", "label": "Pesanan Pembelian",
                        "docs": [dok_purchase_order(r) for r in sorted(pos.values(), key=lambda r: (r["po_date"], r["po_number"]))]})
        kel.append({"key": "bayar", "label": "Pembayaran", "docs": [dok_bayar(p)]})
        fk = [tgh[b] for b in bills if b in tgh]
        if fk:
            kel.append({"key": "faktur", "label": "Faktur Pembelian", "docs": [d for _, _, d in sorted(fk, key=lambda x: (x[0], x[1]))]})
        nd = [d for b in bills for d in nd_per.get(b, [])]
        if nd:
            kel.append({"key": "nota_debit", "label": "Nota Debit", "docs": sorted(nd, key=lambda d: (d["date"] or "", d["number"] or ""))})
        hasil[pid] = kel
    return hasil


def documents_baris(kelompok: list) -> list:
    return [{"type": d["kind"]} for g in kelompok for d in g["docs"]]
