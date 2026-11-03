"""Cetak Faktur Pembelian (P2P cetak, 25 Okt 2026): pemuat konteks untuk /api/documents/bill/{id}/(html|pdf).

SATU sumber dengan rute lama /api/bills/{id}/pdf: data = BillsService.get_bill_v2 (di koneksi pemanggil lewat
get_bill_v2_conn, tanpa acquire bersarang -- Law 32), kop = rekap_pesanan.muat_kop. Terbayar/sisa = turunan jurnal
(compute_ap_rincian, Law 1/16) persis seperti layar detail; angka dokumen = kolom tersimpan, tidak dihitung ulang.

PDF keluarga (3 Nov 2026, putusan MASTER; kerangka PO / penawaran.css): + kontak vendor (po_cetak.kontak_vendor, fungsi SAMA
dgn PO) · No. PO asal (bills.purchase_order_id) · syarat bayar (jarak jatuh tempo, pola _label_terms SI) · keadaan cetak =
faktur_cetak.keadaan_cetak YANG SAMA dgn faktur penjualan (stempel LUNAS/TERLAMBAT/BATAL) dengan riwayat pelunasan dari
JURNAL per baris -- cermin cabang compute_ap_rincian (V440): pembayaran (debit AP proporsional per alokasi), nota debit,
uang muka -- sehingga jumlah riwayat == amount_paid (selisih dicatat, pola SI)."""
import logging
from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException

from . import faktur_cetak as FC
from .bills_service import PESAN_TAGIHAN_TAK_ADA, BillsService
from .po_cetak import kontak_vendor
from .rekap_pesanan import muat_kop

logger = logging.getLogger(__name__)

# Riwayat pelunasan SATU tagihan = cabang compute_ap_rincian (V440) PER BARIS (predikat jurnal SAMA: POSTED, belum dibalik,
# akun PAYABLE, debit). $1 tenant, $2 bill.
SQL_RIWAYAT_TAGIHAN = """
WITH pd_bayar AS (
    SELECT bp.id AS payment_id, COALESCE(SUM(jl.debit), 0) AS ap_debit
    FROM bill_payments_v2 bp
    JOIN journal_entries je ON je.id = bp.journal_id
    JOIN journal_lines jl ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON coa.id = jl.account_id
    WHERE bp.tenant_id = $1
      AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
      AND coa.account_type = 'PAYABLE' AND jl.debit > 0
      AND bp.id IN (SELECT payment_id FROM bill_payment_allocations WHERE bill_id = $2)
    GROUP BY bp.id
),
pd_bagian AS (
    SELECT bpa.bill_id, bpa.payment_id, pb.ap_debit,
           CASE WHEN SUM(bpa.amount_applied) OVER (PARTITION BY bpa.payment_id) > 0
                THEN ROUND(pb.ap_debit * bpa.amount_applied
                           / SUM(bpa.amount_applied) OVER (PARTITION BY bpa.payment_id), 2)
                ELSE 0 END AS bagian,
           ROW_NUMBER() OVER (PARTITION BY bpa.payment_id ORDER BY bpa.id DESC) AS urut_akhir
    FROM bill_payment_allocations bpa
    JOIN pd_bayar pb ON pb.payment_id = bpa.payment_id
),
pd_final AS (
    SELECT x.bill_id, x.payment_id,
           x.bagian + CASE WHEN x.urut_akhir = 1 AND x.jumlah_bagian > 0 THEN x.ap_debit - x.jumlah_bagian ELSE 0 END AS jumlah
    FROM (SELECT b.*, SUM(b.bagian) OVER (PARTITION BY b.payment_id) AS jumlah_bagian FROM pd_bagian b) x
)
SELECT 'pembayaran' AS sumber, bp.payment_date AS tanggal, bp.payment_number AS nomor, bp.payment_method AS metode,
       ba.bank_name AS bank, pf.jumlah
FROM pd_final pf
JOIN bill_payments_v2 bp ON bp.id = pf.payment_id AND bp.tenant_id = $1
LEFT JOIN bank_accounts ba ON ba.id = bp.bank_account_id AND ba.tenant_id = $1
WHERE pf.bill_id = $2
UNION ALL
SELECT 'nota_debit', vca.application_date, vc.credit_number, NULL, NULL, SUM(jl.debit)
FROM vendor_credit_applications vca
JOIN vendor_credits vc ON vc.id = vca.vendor_credit_id AND vc.tenant_id = $1
JOIN journal_entries je ON je.id = vca.journal_id
JOIN journal_lines jl ON jl.journal_id = je.id
JOIN chart_of_accounts coa ON coa.id = jl.account_id
WHERE vca.tenant_id = $1 AND vca.bill_id = $2
  AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
  AND coa.account_type = 'PAYABLE' AND jl.debit > 0
GROUP BY vca.id, vca.application_date, vc.credit_number
UNION ALL
SELECT 'uang_muka', vda.applied_date, vd.deposit_number, NULL, NULL, SUM(jl.debit)
FROM vendor_deposit_applications vda
JOIN vendor_deposits vd ON vd.id = vda.vendor_deposit_id AND vd.tenant_id = $1
JOIN journal_entries je ON je.id = vda.journal_id AND je.source_type = 'DEPOSIT_APPLICATION'
JOIN journal_lines jl ON jl.journal_id = je.id
JOIN chart_of_accounts coa ON coa.id = jl.account_id
WHERE je.tenant_id = $1 AND vda.bill_id = $2
  AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
  AND coa.account_type = 'PAYABLE' AND jl.debit > 0
GROUP BY vda.id, vda.applied_date, vd.deposit_number
"""

JENIS_RIWAYAT = {"pembayaran": "Pembayaran", "nota_debit": "Nota debit", "uang_muka": "Uang muka"}


def _hari(v) -> date | None:
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10]) if v else None


def label_syarat(issue, due) -> str | None:
    """Syarat bayar dari jarak jatuh tempo (pola _label_terms faktur penjualan): 0 -> "Tunai", n -> "n hari"; tak terhitung -> None."""
    a, b = _hari(issue), _hari(due)
    if not a or not b or b < a:
        return None
    n = (b - a).days
    return "Tunai" if n == 0 else f"{n} hari"


async def riwayat_tagihan(conn, tid: str, bill_id) -> list:
    """-> [{sumber, tanggal, nomor, jenis, metode, jumlah}] urut tanggal (bentuk SAMA faktur_cetak.riwayat_pembayaran)."""
    hasil = []
    for r in await conn.fetch(SQL_RIWAYAT_TAGIHAN, tid, bill_id):
        jumlah = Decimal(str(r["jumlah"] or 0))
        if jumlah <= FC.NOL:
            continue
        hasil.append({"sumber": r["sumber"], "tanggal": r["tanggal"], "nomor": r["nomor"] or "-", "jenis": JENIS_RIWAYAT[r["sumber"]],
                      "metode": FC.label_metode(r["metode"], r["bank"]) if r["sumber"] == "pembayaran" else "", "jumlah": jumlah})
    hasil.sort(key=lambda h: (h["tanggal"] or date.min, h["nomor"]))
    return hasil


async def lengkapi_cetak_bill(conn, tid: str, bill: dict) -> dict:
    """Medan cetak tambahan di atas get_bill_v2 (dipakai /documents/bill DAN rute lama /bills/{id}/pdf -- satu sumber)."""
    from ..utils.tanggal_tenant import tanggal_dokumen, zona_tenant
    uid = UUID(str(bill["id"]))
    bill["tenant"] = await muat_kop(conn, tid)
    bill["vendor_kontak"] = await kontak_vendor(conn, tid, bill.get("vendor_id"))
    bill["po_number"] = await conn.fetchval(
        """SELECT po.po_number FROM bills b JOIN purchase_orders po ON po.id = b.purchase_order_id AND po.tenant_id = b.tenant_id
           WHERE b.id = $1 AND b.tenant_id = $2""", uid, tid)
    bill["syarat_bayar"] = label_syarat(bill.get("issue_date"), bill.get("due_date"))
    from .pdf_service import PDFService
    bill["has_cents"] = PDFService.money_has_cents(
        *[bill.get(k) for k in ("subtotal", "dpp", "tax_amount", "grand_total", "amount", "amount_paid", "amount_due",
                                "invoice_discount_total", "cash_discount_total")],
        *[x for it in bill.get("items") or [] for x in (it.get("price") or it.get("unit_price"), it.get("total") or it.get("subtotal"))])
    riwayat = await riwayat_tagihan(conn, tid, uid)
    # keadaan_cetak membaca total_amount/amount_paid/amount_due/status/due_date/voided_at (bentuk faktur penjualan)
    bentuk = {"status": bill.get("status"), "total_amount": bill.get("grand_total") or bill.get("amount"),
              "amount_paid": bill.get("amount_paid"), "amount_due": bill.get("amount_due"), "due_date": bill.get("due_date"),
              "voided_at": bill.get("voided_at")}
    bill["cetak"] = FC.keadaan_cetak(bentuk, await tanggal_dokumen(conn, tid), riwayat, await zona_tenant(conn, tid))
    if bill["cetak"]["selisih_riwayat"] != 0:
        logger.warning("[PDF] tagihan %s: jumlah riwayat != dibayar (selisih %s)", uid, bill["cetak"]["selisih_riwayat"])
    return bill


async def muat_pdf_bill(conn, ctx: dict, bill_id) -> dict:
    """-> {bill}. bill["tenant"] = kop (bentuk yang dibaca bill_invoice.html). 404 bila tak ada di tenant ini."""
    try:
        uid = bill_id if isinstance(bill_id, UUID) else UUID(str(bill_id))
    except ValueError:
        raise HTTPException(status_code=404, detail=PESAN_TAGIHAN_TAK_ADA)
    bill = await BillsService(None).get_bill_v2_conn(conn, ctx["tenant_id"], uid)
    if not bill:
        raise HTTPException(status_code=404, detail=PESAN_TAGIHAN_TAK_ADA)
    return {"bill": await lengkapi_cetak_bill(conn, ctx["tenant_id"], bill)}
