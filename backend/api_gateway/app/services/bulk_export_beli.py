"""Ekspor CSV massal daftar PEMBELIAN (B1a, 28 Okt 2026, MASTER GO): kembar services/bulk_export (Penjualan) untuk 6 modul.

Kolom = kolom layar daftar seragam (seragam/modul/<modul>.tsx `kolom`, tanpa "Dok."); kolom keadaan (Jadwal terima / Jatuh tempo) diekspor
sebagai TANGGAL (== Penjualan). Status = kata YANG SAMA dengan sel Status layar, dihitung pembantu turunan YANG SAMA dengan baris daftar:
po_daftar.fakta_po (position_text), grn_daftar.medan (status_text), tagihan_daftar.posisi_tagihan atas keadaan V440 (compute_ap_rincian),
teks_galat 'bp' (layar Pembayaran Keluar), uang_muka_vendor_daftar.fakta_vd (status_text), nota_debit_daftar.medan_banyak (status_text).
Nilai/sisa = medan daftar yang sama (sisa JURNAL; draf/batal kosong). GRN = dokumen gudang: TANPA kolom nilai (putusan MASTER 28 Okt).
Tambahan: Status (kode) · Referensi (sub-baris daftar: ref_no / reference_number / reference) · ID.
BACA SAJA: penyusun ini tidak menulis apa pun (audit BULK_EXPORT ditulis router)."""
from dataclasses import dataclass
from typing import Awaitable, Callable, List
from uuid import UUID

from . import grn_daftar as GD
from . import nota_debit_daftar as NDD
from . import pembayaran_keluar_daftar as PK
from . import po_daftar as PD
from . import tagihan_daftar as TD
from . import teks_galat as tg
from . import uang_muka_vendor_daftar as UMD

KOLOM_TAMBAHAN = ("Status (kode)", "Referensi", "ID")


@dataclass(frozen=True)
class SpecBeli:
    modul: str          # kunci URL (/api/{modul}/bulk/export)
    berkas: str         # awalan nama berkas
    kolom: tuple        # kolom layar; [0] = Vendor, [-1] = Status
    sql: str            # $1 = tenant, $2 = ids
    susun: Callable[..., Awaitable[dict]]  # (conn, tenant_id, rows) -> {id: [sel kolom..., status_kode, referensi]}


_ID = "= ANY($2::uuid[])"


async def _po(conn, tid, rows):
    fakta = await PD.fakta_po(conn, tid, [r["id"] for r in rows], await PD.tanggal_hari_ini(conn, tid))
    return {r["id"]: [r["vendor_name"], r["po_number"], r["po_date"], r["expected_date"], PD.r2(r["total_amount"]),
                      fakta[r["id"]]["sisa_ditagih"], fakta[r["id"]]["position_text"], r["status"], r["ref_no"]] for r in rows}


async def _grn(conn, tid, rows):
    return {r["id"]: [r["vendor_name"], r["receipt_number"], r["receipt_date"], r["po_number"], r["warehouse_name"],
                      GD.medan(r)["status_text"], r["status"], None] for r in rows}


async def _tagihan(conn, tid, rows):
    return {r["id"]: [r["vendor_name"], r["invoice_number"], r["issue_date"], r["due_date"], r["amount"], r["hutang"],
                      TD.posisi_tagihan(r["keadaan"], r["dibayar"]), r["keadaan"], r["ref_no"]] for r in rows}


async def _bayar(conn, tid, rows):
    out = {}
    for r in rows:
        f = PK.alokasi_fakta(r["status"], r["bruto"], r["dialokasi"])
        out[r["id"]] = [r["vendor_name"], r["payment_number"], r["payment_date"], f["alokasi_teks"], r["total_amount"],
                        f["belum_dialokasikan"], tg.status_id("bp", r["status"]), r["status"], r["reference_number"]]
    return out


async def _uang_muka(conn, tid, rows):
    fakta = await UMD.fakta_vd(conn, tid, await UMD.akun_baca(conn, tid), [r["id"] for r in rows])
    return {r["id"]: [r["vendor_name"], r["deposit_number"], r["deposit_date"], PD.r2(r["amount"]), fakta[r["id"]]["sisa"],
                      fakta[r["id"]]["status_text"], r["status"], r["reference"]] for r in rows}


async def _nota_debit(conn, tid, rows):
    medan = await NDD.medan_banyak(conn, tid, rows)
    out = {}
    for r in rows:
        m = medan.get(r["id"], {})
        sisa = m.get("remaining_amount", NDD.sisa_lama(r["status"], r["total_amount"], r["amount_applied"], r["amount_received"]))
        out[r["id"]] = [r["vendor_name"], r["credit_number"], r["credit_date"], r["original_bill_number"], r["total_amount"],
                        None if m.get("sisa_keadaan") == "draf" else sisa, m.get("status_text") or r["status"], r["status"], r["ref_no"]]
    return out


SPEC_BELI = {
    "purchase-orders": SpecBeli(
        "purchase-orders", "pesanan-pembelian",
        ("Vendor", "No. PO", "Tgl. PO", "Jadwal terima", "Total", "Belum ditagih", "Status"),
        f"""SELECT po.id, po.vendor_name, po.po_number, po.po_date, po.expected_date, po.total_amount, po.status, po.ref_no
            FROM purchase_orders po WHERE po.tenant_id = $1 AND po.id {_ID}""", _po),
    "goods-receipts": SpecBeli(
        "goods-receipts", "penerimaan-barang",
        ("Vendor", "No. penerimaan", "Tgl. terima", "Pesanan pembelian", "Gudang", "Status"),
        f"""SELECT g.id, COALESCE(v.name, po.vendor_name) AS vendor_name, g.receipt_number, g.receipt_date, po.po_number,
                   w.name AS warehouse_name, g.status, {GD.sql_kolom("g")}
            FROM goods_receipts g
            JOIN purchase_orders po ON po.id = g.purchase_order_id AND po.tenant_id = g.tenant_id
            LEFT JOIN vendors v ON v.id = g.vendor_id
            LEFT JOIN warehouses w ON w.id = g.warehouse_id
            WHERE g.tenant_id = $1 AND g.id {_ID}""", _grn),
    "bills": SpecBeli(
        "bills", "faktur-pembelian",
        ("Vendor", "No. faktur", "Tgl. faktur", "Jatuh tempo", "Total", "Sisa", "Status"),
        f"""SELECT b.id, b.vendor_name, b.invoice_number, b.issue_date, b.due_date, b.amount, b.ref_no,
                   {TD.sql_keadaan("$3::date")} AS keadaan, {TD.sql_dibayar()} AS dibayar, {TD.sql_hutang()} AS hutang
            FROM bills b {TD.join_rincian()} WHERE b.tenant_id = $1 AND b.id {_ID}""", _tagihan),
    "bill-payments": SpecBeli(
        "bill-payments", "pembayaran-keluar",
        ("Vendor", "No. pembayaran", "Tgl. bayar", "Alokasi", "Jumlah", "Belum dialokasikan", "Status"),
        f"""SELECT bp.id, COALESCE(bp.vendor_name, '') AS vendor_name, bp.payment_number, bp.payment_date, bp.total_amount,
                   COALESCE(bp.status, 'draft') AS status, bp.reference_number,
                   {PK.sql_bruto("bp")} AS bruto, {PK.sql_dialokasi("bp")} AS dialokasi
            FROM bill_payments_v2 bp WHERE bp.tenant_id = $1 AND bp.id {_ID}""", _bayar),
    "vendor-deposits": SpecBeli(
        "vendor-deposits", "uang-muka-pembelian",
        ("Vendor", "No. uang muka", "Tgl. bayar", "Jumlah", "Sisa", "Status"),
        f"""SELECT vd.id, v.name AS vendor_name, vd.deposit_number, vd.deposit_date, vd.amount, vd.status, vd.reference
            FROM vendor_deposits vd JOIN vendors v ON vd.vendor_id = v.id AND v.tenant_id = vd.tenant_id
            WHERE vd.tenant_id = $1 AND vd.id {_ID}""", _uang_muka),
    "vendor-credits": SpecBeli(
        "vendor-credits", "nota-debit",
        ("Vendor", "No. nota debit", "Tgl. nota", "Faktur asal", "Nilai", "Sisa", "Status"),
        f"""SELECT vc.id, vc.vendor_name, vc.credit_number, vc.credit_date, vc.total_amount, vc.amount_applied, vc.amount_received,
                   vc.status, vc.ref_no, vc.original_bill_id, vc.original_bill_number
            FROM vendor_credits vc WHERE vc.tenant_id = $1 AND vc.id {_ID}""", _nota_debit),
}


async def susun_baris(conn, tenant_id: str, spec: SpecBeli, ids: List[UUID]) -> tuple:
    """-> (baris_csv, ditemukan, dilewati). Urutan = urutan ids; id yang tak ada di tenant ini dilewati (tanpa bocor)."""
    arg = [tenant_id, ids]
    if "$3::date" in spec.sql:  # keadaan tagihan butuh tanggal bisnis tenant (== daftar)
        arg.append(await PD.tanggal_hari_ini(conn, tenant_id))
    rows = await conn.fetch(spec.sql, *arg)
    peta = await spec.susun(conn, tenant_id, rows) if rows else {}
    baris = [peta[_u(i)] + [str(i)] for i in ids if _u(i) in peta]
    return baris, len(baris), len(ids) - len(baris)


def _u(i) -> UUID:
    return i if isinstance(i, UUID) else UUID(str(i))
