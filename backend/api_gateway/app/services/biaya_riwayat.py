"""Biaya t2 (kontrak MASTER 7 Nov 2026): bundel dokumen + riwayat SATU biaya, bentuk SAMA dengan modul lain.

documents : {anchor, default, groups} (pola bill_payments get_payment_documents). anchor.position_text = biaya_daftar.position_text
            (SATU penentu dengan kolom Status daftar). Kelompok: biaya itu sendiri · jurnal (EXPENSE + pembaliknya bila dibatalkan) · bukti.
history   : ekor bersama so_riwayat._selesaikan (nama aktor satu kueri, zona tenant, terbaru dulu). Tabel expenses TIDAK punya
            posted_at/voided_at dan biaya tak menulis audit_logs -> waktu posting/batal = created_at jurnalnya. Aktor posting =
            journal_entries.created_by; aktor batal = bank_transactions.voided_by (null bila tak ada mutasi bank -> "tak diketahui",
            bukan ditebak). Alasan batal = ekor deskripsi jurnal pembalik ("Reversal: <nomor> - <alasan>").
            Law 34: penghapusan bukti TIDAK tercatat di mana pun -> riwayat hanya memuat bukti yang MASIH ADA; jangan diklaim lengkap.
            Nominal saat dibuat tak tersimpan (draf bisa diubah, tanpa audit) -> kejadian "dibuat" tanpa nominal (Law 12)."""
from decimal import Decimal
from typing import Optional

from . import biaya_daftar as BD
from .so_riwayat import _Kumpul, _rp, _selesaikan


def _uang(v) -> str:
    return f"{Decimal(str(v or 0)):.2f}"


def _tgl(v) -> Optional[str]:
    return v.isoformat() if v else None


PENANDA_BIAYA_B6 = "biaya-b6-bundel-w20261110"  # bentuk bundel == modul lain: paper A4 + anchor.vendor


def _dok(kind, id_, number, date, amount, status, batal=False) -> dict:
    return {"kind": kind, "id": str(id_), "number": number, "date": _tgl(date), "amount": amount,
            "paper": "A4", "sent": {"state": "none", "sent_at": None, "viewed_at": None}, "batal": batal, "status": status}


async def _muat(conn, tid: str, expense_id):
    return await conn.fetchrow(
        """SELECT id, expense_number, expense_date, status, total_amount, account_name, paid_through_name, journal_id, vendor_name,
                  created_at, created_by
           FROM expenses WHERE id = $1 AND tenant_id = $2""", expense_id, tid)


async def _jurnal(conn, tid: str, e):
    """(jurnal EXPENSE, jurnal pembaliknya) -- filter tenant eksplisit; pembalik = reversal_of_id jurnal biaya."""
    if not e["journal_id"]:
        return None, None
    j = await conn.fetchrow(
        """SELECT id, journal_number, journal_date, total_debit, created_at, created_by
           FROM journal_entries WHERE id = $1 AND tenant_id = $2""", e["journal_id"], tid)
    r = await conn.fetchrow(
        """SELECT id, journal_number, journal_date, total_debit, description, created_at
           FROM journal_entries WHERE reversal_of_id = $1 AND tenant_id = $2 AND source_type = 'EXPENSE_REVERSAL'
           ORDER BY created_at LIMIT 1""", e["journal_id"], tid)
    return j, r


async def _bukti(conn, tid: str, expense_id):
    return await conn.fetch(
        """SELECT d.id, d.file_name, d.uploaded_at, d.uploaded_by
           FROM document_attachments da JOIN documents d ON d.id = da.document_id AND d.tenant_id = da.tenant_id
           WHERE da.entity_type = 'expense' AND da.entity_id = $1 AND da.tenant_id = $2 AND d.deleted_at IS NULL
           ORDER BY d.uploaded_at, d.id""", expense_id, tid)


async def dokumen_biaya(conn, tid: str, expense_id) -> Optional[dict]:
    """None = biaya tak ada di tenant ini (pemanggil -> 404)."""
    e = await _muat(conn, tid, expense_id)
    if not e:
        return None
    j, r = await _jurnal(conn, tid, e)
    bukti = await _bukti(conn, tid, expense_id)
    n = e["expense_number"]
    batal = e["status"] == "void"
    kel = [{"key": "biaya", "label": "Biaya",
            "docs": [_dok("expense", e["id"], n, e["expense_date"], _uang(e["total_amount"]), e["status"], batal)]}]
    jurnal = []
    if j:
        jurnal.append(_dok("journal", j["id"], j["journal_number"], j["journal_date"], _uang(j["total_debit"]), "diposting", batal))
    if r:
        jurnal.append(_dok("journal", r["id"], r["journal_number"], r["journal_date"], _uang(r["total_debit"]), "pembalik"))
    if jurnal:
        kel.append({"key": "jurnal", "label": "Jurnal", "docs": jurnal})
    if bukti:
        kel.append({"key": "bukti", "label": "Bukti",
                    "docs": [_dok("attachment", b["id"], b["file_name"], b["uploaded_at"], None, "ada") for b in bukti]})
    return {
        "anchor": {"kind": "expense", "id": str(e["id"]), "number": n, "vendor": e["vendor_name"] or "", "date": _tgl(e["expense_date"]),
                   "position_text": BD.position_text(e["status"], e["paid_through_name"], bool(bukti)),
                   "total": _uang(e["total_amount"]), "account_name": e["account_name"],
                   "biaya_keadaan": BD.keadaan(e["status"]), "status_text": BD.status_text(e["status"])},  # B1: SATU penentu dgn daftar/detail
        "default": {"group": "biaya", "index": 0},
        "groups": kel,
    }


def alasan_dari_deskripsi(deskripsi: Optional[str], nomor: str) -> Optional[str]:
    awalan = f"Reversal: {nomor} - "
    if deskripsi and deskripsi.startswith(awalan):
        return deskripsi[len(awalan):].strip() or None
    return None


async def riwayat_biaya(conn, tid: str, expense_id, limit: int = 200) -> Optional[dict]:
    """None = biaya tak ada di tenant ini (pemanggil -> 404)."""
    e = await _muat(conn, tid, expense_id)
    if not e:
        return None
    j, r = await _jurnal(conn, tid, e)
    n = e["expense_number"]
    k = _Kumpul()
    k.tambah(e["created_at"], "BIAYA_DIBUAT", f"Biaya {n} dibuat", e["created_by"], "expense", e["id"], n)
    if j:
        dari = f", dibayar dari {e['paid_through_name']}" if e["paid_through_name"] else ""
        k.tambah(j["created_at"], "BIAYA_DIPOSTING", f"Biaya {n} {_rp(e['total_amount'])} diposting{dari}", j["created_by"],
                 "expense", e["id"], n)
    if r:
        aktor = await conn.fetchval(
            """SELECT voided_by FROM bank_transactions WHERE journal_id = $1 AND tenant_id = $2 AND voided_by IS NOT NULL
               ORDER BY voided_at LIMIT 1""", e["journal_id"], tid)
        alasan = alasan_dari_deskripsi(r["description"], n)
        k.tambah(r["created_at"], "BIAYA_DIBATALKAN", f"Biaya {n} dibatalkan (void){': ' + alasan if alasan else ''}", aktor,
                 "expense", e["id"], n)
    for b in await _bukti(conn, tid, expense_id):
        k.tambah(b["uploaded_at"], "BUKTI_DITAMBAH", f"Bukti {b['file_name']} ditambahkan ke biaya {n}", b["uploaded_by"],
                 "expense", e["id"], n, kunci=b["id"])
    keluar, total = await _selesaikan(conn, tid, k, {"expense": [e["id"]]}, {"expense": True}, limit)    # BUG-187: ikut audit_logs (ubah draf)
    return {"expense_id": str(e["id"]), "expense_number": n, "events": keluar, "total": total, "omitted": []}


# BUG-187: label manusia untuk medan yang diubah lewat PATCH biaya draf (nama tak dikenal tampil dengan NAMA ASLINYA, tak disembunyikan)
LABEL_MEDAN = {
    "expense_date": "tanggal", "paid_through_id": "rekening bayar", "account_id": "akun biaya", "vendor_id": "vendor", "vendor_name": "vendor",
    "amount": "jumlah", "line_items": "rincian", "is_itemized": "rincian", "tax_id": "pajak", "tax_rate": "pajak", "pph_type": "PPh", "pph_rate": "PPh",
    "notes": "catatan", "reference": "referensi", "currency": "mata uang", "is_billable": "dapat ditagihkan", "billed_to_customer_id": "pelanggan penagihan",
}


def ringkas_ubah(nomor, medan, total_sebelum, total_sesudah) -> str:
    """'Biaya EXP-1 diubah: jumlah, tanggal · total Rp100.000 → Rp120.000' (bagian total hanya bila berubah)."""
    label = []
    for m in medan:
        t = LABEL_MEDAN.get(m, m)
        if t not in label:
            label.append(t)
    total = ""
    if total_sebelum is not None and total_sesudah is not None and Decimal(str(total_sebelum)) != Decimal(str(total_sesudah)):
        total = f" · total {_rp(total_sebelum)} → {_rp(total_sesudah)}"
    return f"Biaya {nomor} diubah{': ' + ', '.join(label) if label else ''}{total}"
