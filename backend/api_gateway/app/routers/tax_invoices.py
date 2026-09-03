"""
Tax Invoice Router — CRUD + lifecycle for faktur pajak.

Tax invoices are SEPARATE documents from sales invoices/bills.
Linked via tax_invoice_sources junction table (M:N).
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional
import logging
import asyncpg

from ..schemas.tax_invoices import (
    TaxInvoiceCreate,
    TaxInvoiceBulkCreate,
    TaxInvoiceStatusUpdate,
    TaxInvoiceCancel,
    TaxInvoiceReplace,
    BulkAssignNSFP,
)

logger = logging.getLogger(__name__)
router = APIRouter()


VALID_TRANSITIONS = {
    "draft": ["nsfp_assigned", "cancelled"],
    "nsfp_assigned": ["exported", "cancelled"],
    "exported": ["uploaded", "cancelled"],
    "uploaded": ["approved", "cancelled"],
    "approved": ["replaced"],
}


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    if not user.get("tenant_id"):
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": user.get("tenant_id"), "user_id": user.get("user_id")}


def row_to_dict(row) -> dict:
    """Convert asyncpg Record to dict with UUID→str conversion."""
    d = dict(row)
    for k, v in d.items():
        if hasattr(v, "hex"):  # UUID
            d[k] = str(v)
        elif hasattr(v, "isoformat"):  # date/datetime
            d[k] = v.isoformat()
    return d


async def get_pkp_info(conn, tenant_id: str):
    """Get PKP settings. Raises if not PKP."""
    pkp = await conn.fetchrow(
        "SELECT is_pkp, npwp_pkp, nitku, nama_pkp, alamat_pkp, "
        "default_kode_transaksi, negara "
        "FROM tax_info WHERE tenant_id = $1",
        tenant_id,
    )
    if not pkp or not pkp["is_pkp"]:
        raise HTTPException(
            400, "Tenant bukan PKP. Aktifkan PKP di Settings terlebih dahulu."
        )
    if not pkp["npwp_pkp"]:
        raise HTTPException(400, "NPWP PKP belum diisi. Lengkapi di Settings > PKP.")
    return pkp


async def build_tax_invoice_from_sources(
    conn,
    tenant_id: str,
    user_id,
    source_type: str,
    source_ids: list,
    faktur_date_override=None,
    kode_override=None,
    email_override=None,
    fg_pengganti: int = 0,
    replaces_id=None,
):
    """Core logic: create tax_invoice + items + sources from source documents.
    Supports: sales_invoice, credit_note, vendor_credit."""
    pkp = await get_pkp_info(conn, tenant_id)

    # ── Determine direction, collect source data ──
    direction = "keluaran"
    invoices = []  # reuse name for all source types
    referensi_parts = []

    # Retur-specific fields
    retur_of_tax_invoice_id = None
    retur_of_faktur_number = None

    if source_type == "sales_invoice":
        # ── EXISTING sales_invoice logic ──
        for source_id in source_ids:
            inv = await conn.fetchrow(
                "SELECT id, status, customer_id, invoice_number, invoice_date, "
                "customer_npwp, customer_nik, subtotal, tax_amount, total_amount, kode_transaksi "
                "FROM sales_invoices WHERE id = $1 AND tenant_id = $2",
                source_id,
                tenant_id,
            )
            if not inv:
                raise HTTPException(404, f"Invoice {source_id} tidak ditemukan")
            if inv["status"] not in ("posted", "paid", "partial"):
                raise HTTPException(
                    400,
                    f"Invoice {inv['invoice_number']} belum posted (status: {inv['status']})",
                )
            existing = await conn.fetchval(
                "SELECT ti.id FROM tax_invoice_sources tis "
                "JOIN tax_invoices ti ON ti.id = tis.tax_invoice_id "
                "WHERE tis.source_type = 'sales_invoice' AND tis.source_id = $1 "
                "AND ti.tenant_id = $2 AND ti.status NOT IN ('cancelled', 'replaced')",
                source_id,
                tenant_id,
            )
            if existing:
                raise HTTPException(
                    400,
                    f"Invoice {inv['invoice_number']} sudah punya faktur pajak aktif",
                )
            invoices.append(inv)
            referensi_parts.append(inv["invoice_number"])

        first_cust = invoices[0]["customer_id"]
        if len(invoices) > 1:
            for inv in invoices[1:]:
                if inv["customer_id"] != first_cust:
                    raise HTTPException(
                        400, "Faktur gabungan hanya bisa untuk customer yang sama"
                    )

        customer = await conn.fetchrow(
            "SELECT display_name, tax_id, nik, is_pkp, alamat, nitku, negara, "
            "nomor_dokumen, jenis_id, email "
            "FROM customers WHERE id = $1 AND tenant_id = $2",
            first_cust,
            tenant_id,
        )
        faktur_date = faktur_date_override or invoices[0]["invoice_date"]
        kode = (
            kode_override
            or invoices[0].get("kode_transaksi")
            or pkp["default_kode_transaksi"]
            or "01"
        )
        npwp_pembeli = (
            customer["tax_id"] if customer else invoices[0]["customer_npwp"]
        ) or None
        nik_pembeli = (
            customer["nik"] if customer else invoices[0]["customer_nik"]
        ) or None
        nama_pembeli = customer["display_name"] if customer else "Unknown"
        alamat_pembeli = customer["alamat"] if customer else None
        jenis_id = customer["jenis_id"] if customer and customer["jenis_id"] else "TIN"
        negara = customer["negara"] if customer and customer["negara"] else "IDN"
        nitku_pembeli = customer["nitku"] if customer else None
        nomor_dokumen = customer["nomor_dokumen"] if customer else None
        email = email_override or (customer["email"] if customer else None)

        # Seller = us (PKP)
        npwp_penjual = pkp["npwp_pkp"]
        nitku_penjual = pkp["nitku"]
        nama_penjual = pkp["nama_pkp"]
        alamat_penjual = pkp["alamat_pkp"]
        source_document_type = "sales_invoice"
        items_table = "sales_invoice_items"
        items_fk = "invoice_id"

    elif source_type == "credit_note":
        direction = "keluaran"  # CN = faktur keluaran retur
        for source_id in source_ids:
            cn = await conn.fetchrow(
                "SELECT id, status, customer_id, customer_name, "
                "credit_note_number, credit_note_date, "
                "original_invoice_id, original_invoice_number, "
                "subtotal, tax_rate, tax_amount, total_amount, tax_invoice_id "
                "FROM credit_notes WHERE id = $1 AND tenant_id = $2",
                source_id,
                tenant_id,
            )
            if not cn:
                raise HTTPException(404, f"Credit note {source_id} tidak ditemukan")
            if cn["status"] == "draft":
                raise HTTPException(
                    400, f"Credit note {cn['credit_note_number']} belum di-post"
                )
            if cn["tax_invoice_id"]:
                raise HTTPException(
                    400,
                    f"Credit note {cn['credit_note_number']} sudah punya faktur pajak",
                )

            # Check not already linked
            existing = await conn.fetchval(
                "SELECT ti.id FROM tax_invoice_sources tis "
                "JOIN tax_invoices ti ON ti.id = tis.tax_invoice_id "
                "WHERE tis.source_type = 'credit_note' AND tis.source_id = $1 "
                "AND ti.tenant_id = $2 AND ti.status NOT IN ('cancelled', 'replaced')",
                source_id,
                tenant_id,
            )
            if existing:
                raise HTTPException(
                    400,
                    f"Credit note {cn['credit_note_number']} sudah punya faktur pajak aktif",
                )

            # Find original tax invoice (retur reference)
            if cn["original_invoice_id"]:
                orig_ti = await conn.fetchrow(
                    "SELECT ti.id, ti.faktur_number "
                    "FROM tax_invoice_sources tis "
                    "JOIN tax_invoices ti ON ti.id = tis.tax_invoice_id "
                    "WHERE tis.source_type = 'sales_invoice' AND tis.source_id = $1 "
                    "AND ti.tenant_id = $2 AND ti.status NOT IN ('cancelled', 'replaced') "
                    "ORDER BY ti.created_at DESC LIMIT 1",
                    cn["original_invoice_id"],
                    tenant_id,
                )
                if orig_ti:
                    retur_of_tax_invoice_id = orig_ti["id"]
                    retur_of_faktur_number = orig_ti["faktur_number"]

            invoices.append(cn)
            referensi_parts.append(cn["credit_note_number"])

        first_cust = invoices[0]["customer_id"]
        customer = await conn.fetchrow(
            "SELECT display_name, tax_id, nik, is_pkp, alamat, nitku, negara, "
            "nomor_dokumen, jenis_id, email "
            "FROM customers WHERE id = $1 AND tenant_id = $2",
            first_cust,
            tenant_id,
        )
        faktur_date = faktur_date_override or invoices[0]["credit_note_date"]
        kode = kode_override or pkp["default_kode_transaksi"] or "01"
        npwp_pembeli = (customer["tax_id"] if customer else None) or None
        nik_pembeli = (customer["nik"] if customer else None) or None
        nama_pembeli = (
            customer["display_name"]
            if customer
            else (invoices[0]["customer_name"] or "Unknown")
        )
        alamat_pembeli = customer["alamat"] if customer else None
        jenis_id = customer["jenis_id"] if customer and customer["jenis_id"] else "TIN"
        negara = customer["negara"] if customer and customer["negara"] else "IDN"
        nitku_pembeli = customer["nitku"] if customer else None
        nomor_dokumen = customer["nomor_dokumen"] if customer else None
        email = email_override or (customer["email"] if customer else None)

        # Seller = us (PKP) for keluaran
        npwp_penjual = pkp["npwp_pkp"]
        nitku_penjual = pkp["nitku"]
        nama_penjual = pkp["nama_pkp"]
        alamat_penjual = pkp["alamat_pkp"]
        source_document_type = "credit_note"
        items_table = "credit_note_items"
        items_fk = "credit_note_id"

    elif source_type == "vendor_credit":
        direction = "masukan"  # VC = faktur masukan retur
        for source_id in source_ids:
            vc = await conn.fetchrow(
                "SELECT id, status, vendor_id, vendor_name, "
                "credit_number, credit_date, "
                "original_bill_id, original_bill_number, "
                "subtotal, tax_rate, tax_amount, total_amount, tax_invoice_id "
                "FROM vendor_credits WHERE id = $1 AND tenant_id = $2",
                source_id,
                tenant_id,
            )
            if not vc:
                raise HTTPException(404, f"Vendor credit {source_id} tidak ditemukan")
            if vc["status"] == "draft":
                raise HTTPException(
                    400, f"Vendor credit {vc['credit_number']} belum di-post"
                )
            if vc["tax_invoice_id"]:
                raise HTTPException(
                    400, f"Vendor credit {vc['credit_number']} sudah punya faktur pajak"
                )

            existing = await conn.fetchval(
                "SELECT ti.id FROM tax_invoice_sources tis "
                "JOIN tax_invoices ti ON ti.id = tis.tax_invoice_id "
                "WHERE tis.source_type = 'vendor_credit' AND tis.source_id = $1 "
                "AND ti.tenant_id = $2 AND ti.status NOT IN ('cancelled', 'replaced')",
                source_id,
                tenant_id,
            )
            if existing:
                raise HTTPException(
                    400,
                    f"Vendor credit {vc['credit_number']} sudah punya faktur pajak aktif",
                )

            # Find original tax invoice masukan (retur reference)
            if vc["original_bill_id"]:
                orig_ti = await conn.fetchrow(
                    "SELECT ti.id, ti.faktur_number "
                    "FROM tax_invoice_sources tis "
                    "JOIN tax_invoices ti ON ti.id = tis.tax_invoice_id "
                    "WHERE tis.source_type = 'bill' AND tis.source_id = $1 "
                    "AND ti.tenant_id = $2 AND ti.status NOT IN ('cancelled', 'replaced') "
                    "ORDER BY ti.created_at DESC LIMIT 1",
                    vc["original_bill_id"],
                    tenant_id,
                )
                if orig_ti:
                    retur_of_tax_invoice_id = orig_ti["id"]
                    retur_of_faktur_number = orig_ti["faktur_number"]

            invoices.append(vc)
            referensi_parts.append(vc["credit_number"])

        first_vendor_id = invoices[0]["vendor_id"]
        vendor = await conn.fetchrow(
            "SELECT name, tax_id, nik, address, nitku, negara "
            "FROM vendors WHERE id = $1 AND tenant_id = $2",
            first_vendor_id,
            tenant_id,
        )

        faktur_date = faktur_date_override or invoices[0]["credit_date"]
        kode = kode_override or pkp["default_kode_transaksi"] or "01"

        # SWAP penjual/pembeli for masukan!
        # Penjual = VENDOR (they are the seller)
        npwp_penjual = vendor["tax_id"] if vendor else None
        nitku_penjual = vendor["nitku"] if vendor else None
        nama_penjual = (
            vendor["name"] if vendor else (invoices[0]["vendor_name"] or "Unknown")
        )
        alamat_penjual = vendor["address"] if vendor else None

        # Pembeli = US (PKP) - we are the buyer
        npwp_pembeli = pkp["npwp_pkp"]
        nik_pembeli = None
        nama_pembeli = pkp["nama_pkp"]
        alamat_pembeli = pkp["alamat_pkp"]
        jenis_id = "TIN"
        negara = pkp.get("negara") or "IDN"
        nitku_pembeli = pkp["nitku"]
        nomor_dokumen = None
        email = email_override

        source_document_type = "vendor_credit"
        items_table = "vendor_credit_items"
        items_fk = "vendor_credit_id"

    else:
        raise HTTPException(400, f"source_type '{source_type}' tidak valid")

    # ── Retur validation (DJP requirements) ──
    is_retur = source_type in ("credit_note", "vendor_credit")
    if is_retur:
        # DJP: retur faktur MUST reference the original faktur number
        if (
            False and not retur_of_faktur_number
        ):  # Relaxed: allow retur without original faktur ref
            src_label = (
                "credit note" if source_type == "credit_note" else "vendor credit"
            )
            raise HTTPException(
                400,
                f"Faktur retur memerlukan referensi faktur asli. "
                f"Pastikan {src_label} terkait dengan invoice/bill yang sudah punya faktur pajak.",
            )
        # DJP: kode_transaksi for retur is always "07"
        kode = "07"
        logger.info(
            f"Retur faktur: forcing kode_transaksi=07, ref={retur_of_faktur_number}"
        )

    referensi = ", ".join(referensi_parts)

    # ── INSERT tax_invoice header ──
    tax_invoice_id = await conn.fetchval(
        """
        INSERT INTO tax_invoices (
            tenant_id, direction, faktur_date, kode_transaksi,
            npwp_penjual, nitku_penjual, nama_penjual, alamat_penjual,
            npwp_pembeli, nik_pembeli, jenis_id_pembeli, negara_pembeli,
            nomor_dokumen_pembeli, nama_pembeli, alamat_pembeli, email_pembeli,
            nitku_pembeli, referensi,
            retur_of_tax_invoice_id, retur_of_faktur_number, source_document_type,
            dpp, dpp_nilai_lain, ppn, ppnbm, tarif_ppn, grand_total,
            status, fg_pengganti, replaces_id, created_by
        ) VALUES (
            $1, $2, $3, $4,
            $5, $6, $7, $8,
            $9, $10, $11, $12,
            $13, $14, $15, $16,
            $17, $18,
            $19, $20, $21,
            0, 0, 0, 0, 0, 0,
            'draft', $22, $23, $24
        ) RETURNING id
    """,
        tenant_id,
        direction,
        faktur_date,
        kode,
        npwp_penjual,
        nitku_penjual,
        nama_penjual,
        alamat_penjual,
        npwp_pembeli,
        nik_pembeli,
        jenis_id,
        negara,
        nomor_dokumen,
        nama_pembeli,
        alamat_pembeli,
        email,
        nitku_pembeli,
        referensi,
        retur_of_tax_invoice_id,
        retur_of_faktur_number,
        source_document_type,
        fg_pengganti,
        replaces_id,
        user_id,
    )

    # ── INSERT sources ──
    for source_id in source_ids:
        await conn.execute(
            "INSERT INTO tax_invoice_sources (tenant_id, tax_invoice_id, source_type, source_id) "
            "VALUES ($1, $2, $3, $4)",
            tenant_id,
            tax_invoice_id,
            source_type,
            source_id,
        )

    # ── INSERT items ──
    total_dpp = 0
    total_ppn = 0
    line_num = 0

    for source_id in source_ids:
        items = await conn.fetch(
            f"""
            SELECT id, item_id, description, quantity, unit_price,
                   discount_amount, subtotal, tax_rate, tax_amount,
                   total, line_number
            FROM {items_table}
            WHERE {items_fk} = $1
            ORDER BY line_number
        """,
            source_id,
        )

        for item in items:
            line_num += 1
            mapping = await conn.fetchrow(
                """
                SELECT dkbj.kode, dkbj.nama, dkbj.jenis, dsu.kode AS satuan_kode
                FROM product_djp_mapping pdm
                JOIN djp_kode_barang_jasa dkbj ON dkbj.id = pdm.djp_kode_barang_jasa_id
                JOIN djp_satuan_ukur dsu ON dsu.id = pdm.djp_satuan_ukur_id
                WHERE pdm.product_id = $1 AND pdm.tenant_id = $2
            """,
                item["item_id"],
                tenant_id,
            )

            kode_brg = mapping["kode"] if mapping else "000000"
            jenis_brg = mapping["jenis"] if mapping else "A"
            satuan = mapping["satuan_kode"] if mapping else "UM.0069"
            nama_brg = item["description"] or (
                mapping["nama"] if mapping else "Barang/Jasa"
            )

            item_dpp = float(item["subtotal"] or 0)
            item_ppn = float(item["tax_amount"] or 0)
            tarif = float(item["tax_rate"] or 0)
            diskon = float(item["discount_amount"] or 0)
            # DJP: retur faktur amounts must be POSITIVE
            if is_retur:
                item_dpp = abs(item_dpp)
                item_ppn = abs(item_ppn)
                diskon = abs(diskon)
            dpp_nl = item_dpp

            await conn.execute(
                """
                INSERT INTO tax_invoice_items (
                    tenant_id, tax_invoice_id, line_number, source_line_id,
                    barang_jasa, kode_barang_jasa, nama_barang_jasa, satuan_ukur,
                    harga_satuan, jumlah, diskon,
                    dpp, dpp_nilai_lain, tarif_ppn, ppn,
                    tarif_ppnbm, ppnbm, harga_total
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
            """,
                tenant_id,
                tax_invoice_id,
                line_num,
                item["id"],
                jenis_brg,
                kode_brg,
                nama_brg,
                satuan,
                abs(float(item["unit_price"] or 0))
                if is_retur
                else float(item["unit_price"] or 0),
                abs(float(item["quantity"] or 0))
                if is_retur
                else float(item["quantity"] or 0),
                diskon,
                item_dpp,
                dpp_nl,
                tarif,
                item_ppn,
                0.0,
                0.0,
                abs(float(item["total"] or 0))
                if is_retur
                else float(item["total"] or 0),
            )

            total_dpp += item_dpp
            total_ppn += item_ppn

    # ── Update totals ──
    grand = total_dpp + total_ppn
    await conn.execute(
        """
        UPDATE tax_invoices SET
            dpp = $1, dpp_nilai_lain = $2,
            ppn = $3, grand_total = $4
        WHERE id = $5
    """,
        total_dpp,
        total_dpp,
        total_ppn,
        grand,
        tax_invoice_id,
    )

    # ── Link back to source document ──
    if source_type == "credit_note":
        for source_id in source_ids:
            await conn.execute(
                "UPDATE credit_notes SET tax_invoice_id = $1 WHERE id = $2 AND tenant_id = $3",
                tax_invoice_id,
                source_id,
                tenant_id,
            )
    elif source_type == "vendor_credit":
        for source_id in source_ids:
            await conn.execute(
                "UPDATE vendor_credits SET tax_invoice_id = $1 WHERE id = $2 AND tenant_id = $3",
                tax_invoice_id,
                source_id,
                tenant_id,
            )

    return tax_invoice_id


async def fetch_detail(conn, tax_invoice_id, tenant_id: str) -> dict:
    """Fetch full tax invoice detail with items and sources."""
    ti = await conn.fetchrow(
        "SELECT * FROM tax_invoices WHERE id = $1 AND tenant_id = $2",
        tax_invoice_id,
        tenant_id,
    )
    if not ti:
        return None

    result = row_to_dict(ti)

    # Items
    items = await conn.fetch(
        "SELECT * FROM tax_invoice_items WHERE tax_invoice_id = $1 AND tenant_id = $2 "
        "ORDER BY line_number",
        tax_invoice_id,
        tenant_id,
    )
    result["items"] = [row_to_dict(i) for i in items]

    # Sources with document reference number
    sources_raw = await conn.fetch(
        "SELECT source_type, source_id FROM tax_invoice_sources "
        "WHERE tax_invoice_id = $1 AND tenant_id = $2",
        tax_invoice_id,
        tenant_id,
    )
    sources_list = []
    for s in sources_raw:
        sd = row_to_dict(s)
        # Resolve document number based on source type
        ref_num = None
        if s["source_type"] == "sales_invoice":
            ref_num = await conn.fetchval(
                "SELECT invoice_number FROM sales_invoices WHERE id = $1",
                s["source_id"],
            )
        elif s["source_type"] == "credit_note":
            ref_num = await conn.fetchval(
                "SELECT credit_note_number FROM credit_notes WHERE id = $1",
                s["source_id"],
            )
        elif s["source_type"] == "vendor_credit":
            ref_num = await conn.fetchval(
                "SELECT credit_number FROM vendor_credits WHERE id = $1", s["source_id"]
            )
        elif s["source_type"] == "bill":
            ref_num = await conn.fetchval(
                "SELECT bill_number FROM bills WHERE id = $1", s["source_id"]
            )
        sd["invoice_number"] = ref_num  # keep key for backward compat
        sd["document_number"] = ref_num
        sources_list.append(sd)
    result["sources"] = sources_list

    return result


# ─── ENDPOINTS ───────────────────────────────────────────────


# ═════════════════════════════════════════════════════════════════════════
# GATE NON-PKP — kelas `bank_deleted_at`: benerin KODE, jangan tambah tabel.
#
# Seluruh modul e-Faktur bersandar pada 9 tabel yang TIDAK ADA di database
# mana pun (`tax_info`, `tax_invoices`, `tax_invoice_sources`,
# `tax_invoice_items`, `nsfp_assignments`, `tax_groups`, `tax_group_items`,
# `product_djp_mapping`, `efaktur_exports`) -- lihat
# `backend/migrations/RECOVERY_MISSING_TABLES_BACKLOG.md` BARIS 48, yang
# menandainya "Coherent module — all-or-nothing".
#
# Akibatnya `GET /api/tax-invoices/by-source` 500 setiap kali, dan itu
# TERPANGGIL DI LIMA HALAMAN DETAIL HARIAN (Faktur Penjualan, Faktur
# Pembelian, Nota Kredit x2, Vendor Credit). Terukur 2026-09-03: 22 request,
# 44 galat `relation "tax_invoice_sources" does not exist`, sejak satu
# restart saja.
#
# Kenapa frontend tidak bisa menahannya sendiri: `TaxInvoiceStatusSection`
# SUDAH punya `if (!isPKP) return null` -- tetapi `useTaxInvoiceStatus()`
# dipanggil SEBELUM baris itu, dan hook React wajib berjalan sebelum early
# return. Jadi permintaannya selalu terkirim walau hasilnya tak pernah
# dirender. Penjagaan HARUS di sisi server.
#
# Sumber kebenaran PKP = `"Tenant".is_pkp` (kolom ini ADA dan terisi), BUKAN
# `tax_info.is_pkp` yang tabelnya hilang. Terukur: kedua tenant `is_pkp = f`.
#
# Untuk tenant PKP jalur lama DIBIARKAN APA ADANYA -- ia akan tetap 500
# sampai 9 tabel itu dimigrasikan. Itu DISENGAJA: menambalnya di sini akan
# menyembunyikan modul yang memang belum ada.
# ═════════════════════════════════════════════════════════════════════════


async def _tenant_pkp(conn, tenant_id: str) -> bool:
    """True bila tenant ini PKP menurut `"Tenant".is_pkp`.

    Gagal-tertutup: bila baris tenant tak ditemukan, diperlakukan NON-PKP --
    yang berarti balasan kosong yang sah, bukan 500.
    """
    return bool(
        await conn.fetchval(
            'SELECT is_pkp FROM "Tenant" WHERE id = $1',
            tenant_id,
        )
    )


@router.get("")
async def list_tax_invoices(
    request: Request,
    direction: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    masa_pajak: Optional[str] = Query(None),
    tahun_pajak: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    """List tax invoices with filters and pagination."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        if not await _tenant_pkp(conn, ctx["tenant_id"]):
            # Bentuk yang SAMA dengan hasil kosong biasa, supaya FE tidak
            # perlu tahu bedanya "tenant bukan PKP" dan "belum ada faktur".
            return {"data": [], "total": 0, "page": page, "per_page": per_page}

        conditions = ["ti.tenant_id = $1"]
        params: list = [ctx["tenant_id"]]
        idx = 2

        if direction:
            conditions.append(f"ti.direction = ${idx}")
            params.append(direction)
            idx += 1
        if status:
            conditions.append(f"ti.status = ${idx}")
            params.append(status)
            idx += 1
        if masa_pajak:
            conditions.append(f"ti.masa_pajak = ${idx}")
            params.append(masa_pajak)
            idx += 1
        if tahun_pajak:
            conditions.append(f"ti.tahun_pajak = ${idx}")
            params.append(tahun_pajak)
            idx += 1
        if search:
            conditions.append(
                f"(ti.faktur_number ILIKE ${idx} OR ti.referensi ILIKE ${idx} "
                f"OR ti.nama_pembeli ILIKE ${idx})"
            )
            params.append(f"%{search}%")
            idx += 1

        where = " AND ".join(conditions)
        offset = (page - 1) * per_page

        # Count
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM tax_invoices ti WHERE {where}", *params
        )

        # Fetch page
        params_page = params + [per_page, offset]
        rows = await conn.fetch(
            f"""
            SELECT ti.id, ti.direction, ti.faktur_number, ti.faktur_date,
                   ti.masa_pajak, ti.tahun_pajak, ti.kode_transaksi,
                   ti.nama_pembeli, ti.npwp_pembeli, ti.referensi,
                   ti.dpp, ti.ppn, ti.grand_total,
                   ti.status, ti.created_at
            FROM tax_invoices ti
            WHERE {where}
            ORDER BY ti.created_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
        """,
            *params_page,
        )

        data = []
        for r in rows:
            d = row_to_dict(r)
            # Get source invoice numbers
            src = await conn.fetch(
                "SELECT source_type, source_id FROM tax_invoice_sources "
                "WHERE tax_invoice_id = $1 AND tenant_id = $2",
                r["id"],
                ctx["tenant_id"],
            )
            src_nums = []
            for s in src:
                num = None
                if s["source_type"] == "sales_invoice":
                    num = await conn.fetchval(
                        "SELECT invoice_number FROM sales_invoices WHERE id=$1",
                        s["source_id"],
                    )
                elif s["source_type"] == "credit_note":
                    num = await conn.fetchval(
                        "SELECT credit_note_number FROM credit_notes WHERE id=$1",
                        s["source_id"],
                    )
                elif s["source_type"] == "vendor_credit":
                    num = await conn.fetchval(
                        "SELECT credit_number FROM vendor_credits WHERE id=$1",
                        s["source_id"],
                    )
                elif s["source_type"] == "bill":
                    num = await conn.fetchval(
                        "SELECT bill_number FROM bills WHERE id=$1", s["source_id"]
                    )
                if num:
                    src_nums.append(num)
            d["source_invoices"] = src_nums
            # Cast numerics
            for k in ("dpp", "ppn", "grand_total"):
                d[k] = float(d.get(k) or 0)
            data.append(d)

        return {"data": data, "total": total, "page": page, "per_page": per_page}


@router.get("/by-source")
async def get_tax_invoice_by_source(
    request: Request,
    source_type: str = Query(
        ..., description="sales_invoice, bill, credit_note, vendor_credit"
    ),
    source_id: str = Query(..., description="UUID of source document"),
):
    """Lookup tax invoice by source document. Used for badge display."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        if not await _tenant_pkp(conn, ctx["tenant_id"]):
            # Bentuk PERSIS seperti cabang "tidak ketemu" di bawah, jadi
            # `useTaxInvoiceStatus` menerimanya sebagai "belum ada faktur
            # pajak" dan section tetap tidak merender apa pun.
            return {"has_tax_invoice": False, "tax_invoice": None}

        row = await conn.fetchrow(
            """
            SELECT ti.id, ti.faktur_number, ti.status, ti.direction,
                   ti.source_document_type, ti.dpp, ti.ppn
            FROM tax_invoice_sources tis
            JOIN tax_invoices ti ON ti.id = tis.tax_invoice_id
            WHERE tis.source_type = $1
              AND tis.source_id::text = $2
              AND ti.tenant_id = $3
              AND ti.status NOT IN ('cancelled', 'replaced')
            ORDER BY ti.created_at DESC
            LIMIT 1
        """,
            source_type,
            source_id,
            ctx["tenant_id"],
        )

        if not row:
            return {"has_tax_invoice": False, "tax_invoice": None}

        return {
            "has_tax_invoice": True,
            "tax_invoice": {
                "id": str(row["id"]),
                "faktur_number": row["faktur_number"],
                "status": row["status"],
                "direction": row["direction"],
                "source_document_type": row["source_document_type"],
                "total_dpp": float(row["dpp"] or 0),
                "total_ppn": float(row["ppn"] or 0),
            },
        }


@router.get("/{tax_invoice_id}")
async def get_tax_invoice(request: Request, tax_invoice_id: str):
    """Get full tax invoice detail with items and sources."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        result = await fetch_detail(conn, tax_invoice_id, ctx["tenant_id"])
        if not result:
            raise HTTPException(404, "Tax invoice tidak ditemukan")

        # Cast numeric fields
        for k in ("dpp", "dpp_nilai_lain", "ppn", "ppnbm", "tarif_ppn", "grand_total"):
            if k in result:
                result[k] = float(result.get(k) or 0)
        for item in result.get("items", []):
            for k in (
                "harga_satuan",
                "jumlah",
                "diskon",
                "dpp",
                "dpp_nilai_lain",
                "tarif_ppn",
                "ppn",
                "tarif_ppnbm",
                "ppnbm",
                "harga_total",
            ):
                if k in item:
                    item[k] = float(item.get(k) or 0)

        return result


@router.post("", status_code=201)
async def create_tax_invoice(request: Request, payload: TaxInvoiceCreate):
    """Create tax invoice from source document(s)."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            tax_invoice_id = await build_tax_invoice_from_sources(
                conn,
                ctx["tenant_id"],
                ctx["user_id"],
                payload.source_type,
                payload.source_ids,
                faktur_date_override=payload.faktur_date,
                kode_override=payload.kode_transaksi,
                email_override=payload.email_pembeli,
            )

            result = await fetch_detail(conn, tax_invoice_id, ctx["tenant_id"])
            # Cast numerics
            for k in (
                "dpp",
                "dpp_nilai_lain",
                "ppn",
                "ppnbm",
                "tarif_ppn",
                "grand_total",
            ):
                if k in result:
                    result[k] = float(result.get(k) or 0)
            for item in result.get("items", []):
                for k in (
                    "harga_satuan",
                    "jumlah",
                    "diskon",
                    "dpp",
                    "dpp_nilai_lain",
                    "tarif_ppn",
                    "ppn",
                    "tarif_ppnbm",
                    "ppnbm",
                    "harga_total",
                ):
                    if k in item:
                        item[k] = float(item.get(k) or 0)
            return result


@router.post("/bulk")
async def bulk_create_tax_invoices(request: Request, payload: TaxInvoiceBulkCreate):
    """Bulk create tax invoices for all eligible invoices in a period."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Validate PKP
        await get_pkp_info(conn, ctx["tenant_id"])

        # Determine source document type
        sdt = payload.source_document_type or "sales_invoice"

        if sdt == "credit_note":
            eligible = await conn.fetch(
                """
                SELECT cn.id
                FROM credit_notes cn
                WHERE cn.tenant_id = $1
                  AND cn.status IN ('posted', 'approved')
                  AND EXTRACT(MONTH FROM cn.credit_note_date) = $2
                  AND EXTRACT(YEAR FROM cn.credit_note_date) = $3
                  AND cn.tax_invoice_id IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM tax_invoice_sources tis
                      JOIN tax_invoices ti ON ti.id = tis.tax_invoice_id
                      WHERE tis.source_type = 'credit_note'
                        AND tis.source_id = cn.id
                        AND ti.tenant_id = $1
                        AND ti.status NOT IN ('cancelled', 'replaced')
                  )
                ORDER BY cn.credit_note_date, cn.credit_note_number
            """,
                ctx["tenant_id"],
                int(payload.masa_pajak),
                payload.tahun_pajak,
            )
        elif sdt == "vendor_credit":
            eligible = await conn.fetch(
                """
                SELECT vc.id
                FROM vendor_credits vc
                WHERE vc.tenant_id = $1
                  AND vc.status IN ('posted', 'approved')
                  AND EXTRACT(MONTH FROM vc.credit_date) = $2
                  AND EXTRACT(YEAR FROM vc.credit_date) = $3
                  AND vc.tax_invoice_id IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM tax_invoice_sources tis
                      JOIN tax_invoices ti ON ti.id = tis.tax_invoice_id
                      WHERE tis.source_type = 'vendor_credit'
                        AND tis.source_id = vc.id
                        AND ti.tenant_id = $1
                        AND ti.status NOT IN ('cancelled', 'replaced')
                  )
                ORDER BY vc.credit_date, vc.credit_number
            """,
                ctx["tenant_id"],
                int(payload.masa_pajak),
                payload.tahun_pajak,
            )
        else:
            # Default: sales_invoice
            sdt = "sales_invoice"
            eligible = await conn.fetch(
                """
                SELECT si.id
                FROM sales_invoices si
                WHERE si.tenant_id = $1
                  AND si.status IN ('posted', 'paid', 'partial')
                  AND EXTRACT(MONTH FROM si.invoice_date) = $2
                  AND EXTRACT(YEAR FROM si.invoice_date) = $3
                  AND NOT EXISTS (
                      SELECT 1 FROM tax_invoice_sources tis
                      JOIN tax_invoices ti ON ti.id = tis.tax_invoice_id
                      WHERE tis.source_type = 'sales_invoice'
                        AND tis.source_id = si.id
                        AND ti.tenant_id = $1
                        AND ti.status NOT IN ('cancelled', 'replaced')
                  )
                ORDER BY si.invoice_date, si.invoice_number
            """,
                ctx["tenant_id"],
                int(payload.masa_pajak),
                payload.tahun_pajak,
            )

        created = 0
        skipped = 0
        errors = []

        for row in eligible:
            try:
                async with conn.transaction():
                    await build_tax_invoice_from_sources(
                        conn,
                        ctx["tenant_id"],
                        ctx["user_id"],
                        sdt,
                        [row["id"]],
                        kode_override=payload.kode_transaksi,
                    )
                    created += 1
            except HTTPException as e:
                errors.append({"source_id": str(row["id"]), "error": e.detail})
                skipped += 1
            except Exception as e:
                errors.append({"source_id": str(row["id"]), "error": str(e)})
                skipped += 1

        return {"created": created, "skipped": skipped, "errors": errors}


@router.post("/{tax_invoice_id}/assign-nsfp")
async def assign_nsfp(request: Request, tax_invoice_id: str):
    """Assign NSFP number to a draft tax invoice."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            ti = await conn.fetchrow(
                "SELECT id, status, faktur_number FROM tax_invoices "
                "WHERE id = $1 AND tenant_id = $2",
                tax_invoice_id,
                ctx["tenant_id"],
            )
            if not ti:
                raise HTTPException(404, "Tax invoice tidak ditemukan")
            if ti["status"] != "draft":
                raise HTTPException(
                    400,
                    f"Hanya faktur DRAFT yang bisa di-assign NSFP (current: {ti['status']})",
                )
            if ti["faktur_number"]:
                raise HTTPException(400, "Faktur sudah punya NSFP")

            # Call DB function (has advisory lock inside)
            faktur_number = await conn.fetchval(
                "SELECT generate_efaktur_number($1)", ctx["tenant_id"]
            )
            if not faktur_number:
                raise HTTPException(
                    400,
                    "Tidak ada range NSFP aktif. Tambahkan range di Settings > NSFP.",
                )

            nsfp_number = int(faktur_number.split(".")[-1])

            await conn.execute(
                """
                UPDATE tax_invoices SET
                    faktur_number = $1, nsfp_number = $2, status = 'nsfp_assigned',
                    updated_at = now()
                WHERE id = $3
            """,
                faktur_number,
                nsfp_number,
                tax_invoice_id,
            )

            await conn.execute(
                """
                INSERT INTO nsfp_assignments (tenant_id, tax_invoice_id, faktur_number)
                VALUES ($1, $2, $3)
            """,
                ctx["tenant_id"],
                tax_invoice_id,
                faktur_number,
            )

            return {"faktur_number": faktur_number, "status": "nsfp_assigned"}


@router.post("/bulk-assign-nsfp")
async def bulk_assign_nsfp(request: Request, payload: BulkAssignNSFP):
    """Bulk assign NSFP to draft tax invoices."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        if payload.tax_invoice_ids:
            ids = payload.tax_invoice_ids
        else:
            rows = await conn.fetch(
                "SELECT id FROM tax_invoices "
                "WHERE tenant_id = $1 AND status = 'draft' AND faktur_number IS NULL "
                "ORDER BY created_at",
                ctx["tenant_id"],
            )
            ids = [str(r["id"]) for r in rows]

        assigned = 0
        errors = []

        for tid in ids:
            try:
                async with conn.transaction():
                    ti = await conn.fetchrow(
                        "SELECT id, status, faktur_number FROM tax_invoices "
                        "WHERE id = $1 AND tenant_id = $2",
                        tid,
                        ctx["tenant_id"],
                    )
                    if not ti or ti["status"] != "draft" or ti["faktur_number"]:
                        errors.append({"id": tid, "error": "Not eligible"})
                        continue

                    faktur_number = await conn.fetchval(
                        "SELECT generate_efaktur_number($1)", ctx["tenant_id"]
                    )
                    if not faktur_number:
                        errors.append({"id": tid, "error": "No active NSFP range"})
                        break

                    nsfp_number = int(faktur_number.split(".")[-1])

                    await conn.execute(
                        """
                        UPDATE tax_invoices SET
                            faktur_number = $1, nsfp_number = $2, status = 'nsfp_assigned',
                            updated_at = now()
                        WHERE id = $3
                    """,
                        faktur_number,
                        nsfp_number,
                        ti["id"],
                    )

                    await conn.execute(
                        """
                        INSERT INTO nsfp_assignments
                            (tenant_id, tax_invoice_id, faktur_number)
                        VALUES ($1, $2, $3)
                    """,
                        ctx["tenant_id"],
                        ti["id"],
                        faktur_number,
                    )

                    assigned += 1
            except Exception as e:
                errors.append({"id": tid, "error": str(e)})

        return {"assigned": assigned, "errors": errors}


@router.patch("/{tax_invoice_id}/status")
async def update_status(
    request: Request, tax_invoice_id: str, payload: TaxInvoiceStatusUpdate
):
    """Update tax invoice status (exported, uploaded, approved)."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        ti = await conn.fetchrow(
            "SELECT id, status FROM tax_invoices WHERE id = $1 AND tenant_id = $2",
            tax_invoice_id,
            ctx["tenant_id"],
        )
        if not ti:
            raise HTTPException(404, "Tax invoice tidak ditemukan")

        current = ti["status"]
        allowed = VALID_TRANSITIONS.get(current, [])
        if payload.status not in allowed:
            raise HTTPException(
                400,
                f"Transisi {current} → {payload.status} tidak valid. "
                f"Allowed: {allowed}",
            )

        await conn.execute(
            "UPDATE tax_invoices SET status = $1, updated_at = now() WHERE id = $2",
            payload.status,
            tax_invoice_id,
        )

        return {"id": str(tax_invoice_id), "status": payload.status}


@router.post("/{tax_invoice_id}/cancel")
async def cancel_tax_invoice(
    request: Request, tax_invoice_id: str, payload: TaxInvoiceCancel
):
    """Cancel a tax invoice."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        ti = await conn.fetchrow(
            "SELECT id, status FROM tax_invoices WHERE id = $1 AND tenant_id = $2",
            tax_invoice_id,
            ctx["tenant_id"],
        )
        if not ti:
            raise HTTPException(404, "Tax invoice tidak ditemukan")

        if ti["status"] in ("cancelled", "replaced"):
            raise HTTPException(
                400, f"Faktur sudah {ti['status']}, tidak bisa di-cancel"
            )
        if ti["status"] == "approved":
            raise HTTPException(
                400, "Faktur APPROVED tidak bisa di-cancel. Gunakan Replace."
            )

        # Reason required for exported/uploaded
        if ti["status"] in ("exported", "uploaded") and not payload.reason:
            raise HTTPException(
                400, "Alasan pembatalan wajib untuk faktur yang sudah exported/uploaded"
            )

        await conn.execute(
            """
            UPDATE tax_invoices SET
                status = 'cancelled', cancellation_reason = $1,
                cancelled_at = now(), cancelled_by = $2, updated_at = now()
            WHERE id = $3
        """,
            payload.reason,
            ctx["user_id"],
            tax_invoice_id,
        )

        return {"id": str(tax_invoice_id), "status": "cancelled"}


@router.post("/{tax_invoice_id}/replace")
async def replace_tax_invoice(
    request: Request, tax_invoice_id: str, payload: TaxInvoiceReplace
):
    """Create replacement faktur for an approved tax invoice."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            original = await conn.fetchrow(
                "SELECT * FROM tax_invoices WHERE id = $1 AND tenant_id = $2",
                tax_invoice_id,
                ctx["tenant_id"],
            )
            if not original:
                raise HTTPException(404, "Tax invoice tidak ditemukan")
            if original["status"] != "approved":
                raise HTTPException(400, "Hanya faktur APPROVED yang bisa di-replace")

            faktur_date = payload.faktur_date or original["faktur_date"]

            # Create new tax invoice (copy from original)
            new_id = await conn.fetchval(
                """
                INSERT INTO tax_invoices (
                    tenant_id, direction, faktur_date, kode_transaksi,
                    npwp_penjual, nitku_penjual, nama_penjual, alamat_penjual,
                    npwp_pembeli, nik_pembeli, jenis_id_pembeli, negara_pembeli,
                    nomor_dokumen_pembeli, nama_pembeli, alamat_pembeli, email_pembeli,
                    nitku_pembeli, referensi,
                    dpp, dpp_nilai_lain, ppn, ppnbm, tarif_ppn, grand_total,
                    status, fg_pengganti, replaces_id, notes, created_by
                ) VALUES (
                    $1, $2, $3, $4,
                    $5, $6, $7, $8,
                    $9, $10, $11, $12,
                    $13, $14, $15, $16,
                    $17, $18,
                    $19, $20, $21, $22, $23, $24,
                    'draft', 1, $25, $26, $27
                ) RETURNING id
            """,
                ctx["tenant_id"],
                original["direction"],
                faktur_date,
                original["kode_transaksi"],
                original["npwp_penjual"],
                original["nitku_penjual"],
                original["nama_penjual"],
                original["alamat_penjual"],
                original["npwp_pembeli"],
                original["nik_pembeli"],
                original["jenis_id_pembeli"],
                original["negara_pembeli"],
                original["nomor_dokumen_pembeli"],
                original["nama_pembeli"],
                original["alamat_pembeli"],
                original["email_pembeli"],
                original["nitku_pembeli"],
                original["referensi"],
                original["dpp"],
                original["dpp_nilai_lain"],
                original["ppn"],
                original["ppnbm"],
                original["tarif_ppn"],
                original["grand_total"],
                tax_invoice_id,
                payload.reason,
                ctx["user_id"],
            )

            # Copy items
            orig_items = await conn.fetch(
                "SELECT * FROM tax_invoice_items WHERE tax_invoice_id = $1 AND tenant_id = $2 "
                "ORDER BY line_number",
                tax_invoice_id,
                ctx["tenant_id"],
            )
            for item in orig_items:
                await conn.execute(
                    """
                    INSERT INTO tax_invoice_items (
                        tenant_id, tax_invoice_id, line_number, source_line_id,
                        barang_jasa, kode_barang_jasa, nama_barang_jasa, satuan_ukur,
                        harga_satuan, jumlah, diskon,
                        dpp, dpp_nilai_lain, tarif_ppn, ppn,
                        tarif_ppnbm, ppnbm, harga_total
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
                """,
                    ctx["tenant_id"],
                    new_id,
                    item["line_number"],
                    item["source_line_id"],
                    item["barang_jasa"],
                    item["kode_barang_jasa"],
                    item["nama_barang_jasa"],
                    item["satuan_ukur"],
                    item["harga_satuan"],
                    item["jumlah"],
                    item["diskon"],
                    item["dpp"],
                    item["dpp_nilai_lain"],
                    item["tarif_ppn"],
                    item["ppn"],
                    item["tarif_ppnbm"],
                    item["ppnbm"],
                    item["harga_total"],
                )

            # Copy sources
            orig_sources = await conn.fetch(
                "SELECT * FROM tax_invoice_sources WHERE tax_invoice_id = $1 AND tenant_id = $2",
                tax_invoice_id,
                ctx["tenant_id"],
            )
            for src in orig_sources:
                await conn.execute(
                    "INSERT INTO tax_invoice_sources (tenant_id, tax_invoice_id, source_type, source_id) "
                    "VALUES ($1, $2, $3, $4)",
                    ctx["tenant_id"],
                    new_id,
                    src["source_type"],
                    src["source_id"],
                )

            # Mark original as replaced
            await conn.execute(
                """
                UPDATE tax_invoices SET
                    status = 'replaced', replaced_by_id = $1, updated_at = now()
                WHERE id = $2
            """,
                new_id,
                tax_invoice_id,
            )

            result = await fetch_detail(conn, new_id, ctx["tenant_id"])
            for k in (
                "dpp",
                "dpp_nilai_lain",
                "ppn",
                "ppnbm",
                "tarif_ppn",
                "grand_total",
            ):
                if k in result:
                    result[k] = float(result.get(k) or 0)
            for item in result.get("items", []):
                for k in (
                    "harga_satuan",
                    "jumlah",
                    "diskon",
                    "dpp",
                    "dpp_nilai_lain",
                    "tarif_ppn",
                    "ppn",
                    "tarif_ppnbm",
                    "ppnbm",
                    "harga_total",
                ):
                    if k in item:
                        item[k] = float(item.get(k) or 0)
            return result
