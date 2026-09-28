"""Jumlah TERKIRIM per baris Pesanan Penjualan + nilai BELUM DIKIRIM (26 Sep 2026, audit SO FE).

Sumber terkirim = Surat Jalan (invoice_fulfillments AKTIF) — sumber yang sama dengan header
sales_orders.shipped_qty (V264 recompute_so_shipped). Kolom sales_order_items.quantity_shipped MATI
sejak V264 (tak ada penulis; terukur 26 Sep: 0 di semua 212 baris, padahal kaos punya 215 unit terkirim).

Rantai per baris: invoice_fulfillment_items.invoice_item_id -> sales_invoice_items.sales_order_item_id.
Baris faktur tanpa tautan ke baris SO tak bisa dibagikan ke baris mana pun; jumlahnya dilaporkan
TERPISAH (fulfilled_qty_unlinked) supaya Σ baris + tak-tertaut == header shipped_qty (terukur 26 Sep: 0).

Nilai belum dikirim per baris = (qty − terkirim, min 0) × line_total / qty. line_total SO = NETO
(sesudah diskon baris, SEBELUM pajak; terukur 210/212 baris = qty×harga×(1−diskon%)). Diskon header,
ongkir, dan pajak TIDAK termasuk. Decimal + ROUND_HALF_UP (Law 9/25).
"""
from decimal import ROUND_HALF_UP, Decimal

NOL = Decimal("0")
SEN = Decimal("0.01")

# Filter fulfillment AKTIF identik dengan V264 recompute_so_shipped (header shipped_qty).
_AKTIF = "f.voided_at IS NULL AND f.status <> 'voided'"

SQL_TERKIRIM_PER_BARIS = f"""
    SELECT si.sales_order_id AS so_id, sii.sales_order_item_id AS soi_id, SUM(ifi.quantity) AS terkirim
    FROM sales_invoices si
    JOIN sales_invoice_items sii ON sii.invoice_id = si.id
    JOIN invoice_fulfillment_items ifi ON ifi.invoice_item_id = sii.id
    JOIN invoice_fulfillments f ON f.id = ifi.fulfillment_id AND f.tenant_id = si.tenant_id AND {_AKTIF}
    WHERE si.tenant_id = $1 AND si.sales_order_id = ANY($2::uuid[])
    GROUP BY 1, 2
"""


# Faktur yang BISA dikirim (Surat Jalan) = gerbang POST /sales-invoices/{id}/fulfill — satu sumber, dipakai
# gerbang itu DAN tugas dashboard so_to_ship (pending_invoice_id, D5 28 Sep 2026).
FAKTUR_TERBIT_KIRIM = ("posted", "partial", "paid")
KIRIM_TERBUKA = ("pending", "partial")

SQL_FAKTUR_TERTUNDA_PER_SO = """
    SELECT DISTINCT ON (si.sales_order_id) si.sales_order_id AS so_id, si.id, si.invoice_number
    FROM sales_invoices si
    WHERE si.tenant_id = $1 AND si.sales_order_id = ANY($2::uuid[])
      AND si.status = ANY($3::text[]) AND si.fulfillment_status = ANY($4::text[])
    ORDER BY si.sales_order_id, si.invoice_date, si.created_at, si.invoice_number
"""


async def faktur_tertunda_per_so(conn, tenant_id: str, so_ids: list) -> dict:
    """-> {so_id: {"id", "invoice_number"}} — faktur TERBIT tertua per SO yang pengirimannya masih terbuka.
    SO tanpa faktur seperti itu tidak ada di hasil (pemanggil -> null)."""
    if not so_ids:
        return {}
    rows = await conn.fetch(SQL_FAKTUR_TERTUNDA_PER_SO, tenant_id, list(so_ids),
                            list(FAKTUR_TERBIT_KIRIM), list(KIRIM_TERBUKA))
    return {r["so_id"]: {"id": r["id"], "invoice_number": r["invoice_number"]} for r in rows}


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else NOL


async def terkirim_per_baris(conn, tenant_id: str, so_ids: list) -> tuple:
    """-> ({soi_id: Decimal terkirim}, {so_id: Decimal terkirim tanpa tautan baris})."""
    if not so_ids:
        return {}, {}
    rows = await conn.fetch(SQL_TERKIRIM_PER_BARIS, tenant_id, list(so_ids))
    per_baris, tanpa_tautan = {}, {}
    for r in rows:
        if r["soi_id"] is None:
            tanpa_tautan[r["so_id"]] = tanpa_tautan.get(r["so_id"], NOL) + _d(r["terkirim"])
        else:
            per_baris[r["soi_id"]] = _d(r["terkirim"])
    return per_baris, tanpa_tautan


def belum_dikirim(quantity, terkirim) -> Decimal:
    sisa = _d(quantity) - _d(terkirim)
    return sisa if sisa > NOL else NOL


def nilai_belum_dikirim(quantity, line_total, terkirim) -> Decimal:
    q = _d(quantity)
    if q <= NOL:
        return NOL
    return (belum_dikirim(q, terkirim) * _d(line_total) / q).quantize(SEN, rounding=ROUND_HALF_UP)


async def jumlah_surat_jalan(conn, tenant_id: str) -> int:
    """Surat Jalan AKTIF tenant (filter aktif = V264). 0 -> unshipped_value = seluruh sisa SO karena pengiriman tak
    pernah DICATAT, bukan bukti barang belum keluar (grapgrap 26 Sep). FE: tampilkan unshipped hanya bila > 0
    sampai pemilik memutuskan (MASTER 26 Sep)."""
    return int(await conn.fetchval(
        f"SELECT COUNT(*) FROM invoice_fulfillments f WHERE f.tenant_id = $1 AND {_AKTIF}", tenant_id) or 0)


async def ringkasan_belum_dikirim(conn, tenant_id: str, status_tidak: tuple) -> dict:
    """Σ nilai belum dikirim SO selain status_tidak (satu definisi dengan detail per baris)."""
    baris = await conn.fetch(
        """SELECT so.id AS so_id, soi.id AS soi_id, soi.quantity, soi.line_total
           FROM sales_orders so JOIN sales_order_items soi ON soi.sales_order_id = so.id
           WHERE so.tenant_id = $1 AND so.status <> ALL($2::text[])""",
        tenant_id, list(status_tidak),
    )
    so_ids = sorted({r["so_id"] for r in baris})
    per_baris, _ = await terkirim_per_baris(conn, tenant_id, so_ids)
    per_so = {}
    for r in baris:
        per_so[r["so_id"]] = per_so.get(r["so_id"], NOL) + nilai_belum_dikirim(
            r["quantity"], r["line_total"], per_baris.get(r["soi_id"])
        )
    return {"total": sum(per_so.values(), NOL), "count": sum(1 for v in per_so.values() if v > NOL)}


async def ringkasan_menunggu_kirim(conn, tenant_id: str, status_tidak: tuple) -> dict:
    """MENUNGGU KIRIM NYATA (27 Sep 2026): hanya baris yang PERLU DIKIRIM
    (COALESCE(perlu_kirim, products.track_inventory, false) — ekspresi sama dengan V318 so_memenuhi_selesai
    dan detail requires_fulfillment) dengan sisa qty > 0 menurut Surat Jalan aktif.
    total = Σ nilai sisa baris itu (NETO, definisi nilai_belum_dikirim); count = SO yang punya >=1 baris itu.
    Dulu kartu memakai STATUS SO (confirmed/partial_shipped) -> tenant serba non-stok (grapgrap) melihat
    Rp 56 jt / 21 pesanan "menunggu kirim" yang tak akan pernah dikirim (kelas "completed != lunas")."""
    baris = await conn.fetch(
        """SELECT so.id AS so_id, soi.id AS soi_id, soi.quantity, soi.line_total
           FROM sales_orders so
           JOIN sales_order_items soi ON soi.sales_order_id = so.id
           LEFT JOIN products p ON p.id = soi.item_id AND p.tenant_id = so.tenant_id
           WHERE so.tenant_id = $1 AND so.status <> ALL($2::text[])
             AND COALESCE(soi.perlu_kirim, p.track_inventory, false)""",
        tenant_id, list(status_tidak),
    )
    so_ids = sorted({r["so_id"] for r in baris})
    per_baris, _ = await terkirim_per_baris(conn, tenant_id, so_ids)
    total, so_menunggu = NOL, set()
    for r in baris:
        terkirim = per_baris.get(r["soi_id"])
        if belum_dikirim(r["quantity"], terkirim) > NOL:
            so_menunggu.add(r["so_id"])
            total += nilai_belum_dikirim(r["quantity"], r["line_total"], terkirim)
    return {"total": total, "count": len(so_menunggu)}
