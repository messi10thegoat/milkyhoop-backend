"""Ekspor CSV massal daftar (U1b F1, 5 Okt 2026): kolom = kolom daftar resep FE (recipes/*.ts `kolom`) + Status (kode), Kode order,
Judul order, ID. Satu SQL per modul: tenant eksplisit + id = ANY(ids); angka dari KOLOM TERSIMPAN yang sama dengan daftar
(Pengiriman = HPP, bukan nilai jual). Status layar = label teks_galat (sumber sama dengan FE) dan status_detail untuk NK/uang muka.
BACA SAJA: tak ada tulisan selain satu baris audit BULK_EXPORT."""
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional
from uuid import UUID

from . import teks_galat as tg
from ..utils.tanggal_tenant import tanggal_pada, zona_tenant


@dataclass(frozen=True)
class SpecExport:
    modul: str                      # kunci URL (/api/{modul}/bulk/export)
    berkas: str                     # awalan nama berkas
    kolom: tuple                    # 6 kolom resep: Pelanggan, No., Tgl, kol4, Nilai, Status
    sql: str                        # id, pelanggan, nomor, tgl, kol4, dicatat, nilai, status (+ kolom bantu)
    jenis_kode: Optional[str]       # jenis tempel_kode (None: SO punya kolom kode sendiri)
    label_status: Callable          # (row) -> label layar


def _pengiriman(r) -> str:
    return {"posted": "Terkirim", "voided": "Batal", "void": "Batal", "cancelled": "Batal"}.get(str(r["status"]), str(r["status"]))


def _nk(r) -> str:
    from ..routers.credit_notes import status_detail_nk
    return tg.status_id("cn", status_detail_nk(r["status"], r["amount_applied"], r["amount_refunded"]))


def _dp(r) -> str:
    from ..routers.customer_deposits import status_detail_dp
    return tg.status_id("dp", status_detail_dp(r["status"], r["amount_applied"], r["amount_refunded"]))


def _jenis(j: str):
    return lambda r: tg.status_id(j, r["status"])


_ID = "= ANY($2::uuid[])"

SPEC = {
    "sales-orders": SpecExport(
        "sales-orders", "pesanan", ("Pelanggan", "No. pesanan", "Tgl. pesanan", "Tgl. kirim", "Total", "Status"),
        f"""SELECT t.id, t.customer_name AS pelanggan, t.order_number AS nomor, t.order_date AS tgl,
                   t.expected_ship_date AS kol4, t.created_at AS dicatat, t.total_amount AS nilai, t.status,
                   t.order_code AS kode_order, t.order_title AS judul_order
            FROM sales_orders t WHERE t.tenant_id = $1 AND t.id {_ID}""", None, _jenis("so")),
    "quotes": SpecExport(
        "quotes", "penawaran", ("Pelanggan", "No. penawaran", "Tgl. penawaran", "Berlaku sampai", "Total", "Status"),
        f"""SELECT t.id, t.customer_name AS pelanggan, t.quote_number AS nomor, t.quote_date AS tgl,
                   t.expiry_date AS kol4, t.created_at AS dicatat, t.total_amount AS nilai, t.status
            FROM quotes t WHERE t.tenant_id = $1 AND t.id {_ID}""", "quote", _jenis("quote")),
    "proformas": SpecExport(
        "proformas", "proforma", ("Pelanggan", "No. proforma", "Tgl. proforma", "Jatuh tempo", "Nilai", "Status"),
        f"""SELECT t.id, t.customer_name AS pelanggan, t.proforma_number AS nomor, t.proforma_date AS tgl,
                   t.due_date AS kol4, t.created_at AS dicatat, t.amount AS nilai, t.status
            FROM proformas t WHERE t.tenant_id = $1 AND t.id {_ID}""", "proforma", _jenis("proforma")),
    "sales-invoices": SpecExport(
        "sales-invoices", "faktur", ("Pelanggan", "No. faktur", "Tgl. faktur", "Jatuh tempo", "Total", "Status"),
        f"""SELECT t.id, t.customer_name AS pelanggan, t.invoice_number AS nomor, t.invoice_date AS tgl,
                   t.due_date AS kol4, t.created_at AS dicatat, t.total_amount AS nilai, t.status
            FROM sales_invoices t WHERE t.tenant_id = $1 AND t.id {_ID}""", "sales_invoice", _jenis("si")),
    "customer-deposits": SpecExport(
        "customer-deposits", "uang-muka", ("Pelanggan", "No. uang muka", "Tgl. terima", "Dicatat", "Jumlah", "Status"),
        f"""SELECT t.id, t.customer_name AS pelanggan, t.deposit_number AS nomor, t.deposit_date AS tgl,
                   t.created_at AS kol4, t.created_at AS dicatat, t.amount AS nilai, t.status,
                   t.amount_applied, t.amount_refunded
            FROM customer_deposits t WHERE t.tenant_id = $1 AND t.id {_ID}""", "customer_deposit", _dp),
    "receive-payments": SpecExport(
        "receive-payments", "penerimaan", ("Pelanggan", "No. penerimaan", "Tgl. bayar", "Dicatat", "Jumlah", "Status"),
        f"""SELECT t.id, t.customer_name AS pelanggan, t.payment_number AS nomor, t.payment_date AS tgl,
                   t.created_at AS kol4, t.created_at AS dicatat, t.total_amount AS nilai, t.status
            FROM receive_payments t WHERE t.tenant_id = $1 AND t.id {_ID}""", "receive_payment", _jenis("rp")),
    "deliveries": SpecExport(
        "deliveries", "surat-jalan", ("Pelanggan", "No. surat jalan", "Tgl. kirim", "Dicatat", "HPP", "Status"),
        f"""SELECT t.id, COALESCE(c.nama, si.customer_name) AS pelanggan, t.fulfillment_number AS nomor,
                   t.fulfillment_date AS tgl, t.created_at AS kol4, t.created_at AS dicatat,
                   (SELECT COALESCE(SUM(fi.total_cost), 0) FROM invoice_fulfillment_items fi WHERE fi.fulfillment_id = t.id) AS nilai,
                   t.status
            FROM invoice_fulfillments t
            JOIN sales_invoices si ON si.id = t.invoice_id AND si.tenant_id = t.tenant_id
            LEFT JOIN customers c ON c.id = si.customer_id AND c.tenant_id = t.tenant_id
            WHERE t.tenant_id = $1 AND t.id {_ID}""", "delivery", _pengiriman),
    "credit-notes": SpecExport(
        "credit-notes", "nota-kredit", ("Pelanggan", "No. nota kredit", "Tanggal", "Dicatat", "Nilai", "Status"),
        f"""SELECT t.id, t.customer_name AS pelanggan, t.credit_note_number AS nomor, t.credit_note_date AS tgl,
                   t.created_at AS kol4, t.created_at AS dicatat, t.total_amount AS nilai, t.status,
                   t.amount_applied, t.amount_refunded
            FROM credit_notes t WHERE t.tenant_id = $1 AND t.id {_ID}""", "credit_note", _nk),
}

KOLOM_TAMBAHAN = ("Status (kode)", "Kode order", "Judul order", "ID")


async def susun_baris(conn, tenant_id: str, spec: SpecExport, ids: List[UUID]) -> tuple:
    """-> (baris_csv, ditemukan, dilewati). Urutan = urutan ids; id yang tak ada di tenant ini dilewati (tanpa bocor)."""
    rows = {str(r["id"]): dict(r) for r in await conn.fetch(spec.sql, tenant_id, ids)}
    urut = [rows[str(i)] for i in ids if str(i) in rows]
    if spec.jenis_kode and urut:
        from .kode_order import tempel_kode
        await tempel_kode(conn, tenant_id, spec.jenis_kode, urut)
    zona = await zona_tenant(conn, tenant_id)

    def hari(v):  # "Dicatat" = tanggal bisnis tenant (WIB), bukan UTC
        return tanggal_pada(v, zona) if isinstance(v, datetime) else v

    baris = []
    for r in urut:
        kode = r.get("order_code") if "order_code" in r else r.get("kode_order")
        if "order_codes" in r:  # penerimaan: bisa melunasi faktur beberapa SO
            kode = "; ".join(str(c.get("order_code") if isinstance(c, dict) else c) for c in (r.get("order_codes") or []) if c) or None
        judul = r.get("order_title") if "order_title" in r else r.get("judul_order")
        baris.append([r["pelanggan"], r["nomor"], r["tgl"], hari(r["kol4"]), r["nilai"], spec.label_status(r),
                      r["status"], kode, judul, str(r["id"])])
    return baris, len(urut), len(ids) - len(urut)
