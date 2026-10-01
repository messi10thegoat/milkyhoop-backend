"""Posisi, Kirim, dan jumlah dokumen per Pesanan Penjualan untuk DAFTAR (P1 SO-dokumen, 1 Okt 2026).

Spek: docs/so-dokumen/02-DATA-DAN-API.md §Posisi/§Kirim + 01-PERUBAHAN.md §A, dengan PUTUSAN PEMILIK 1 Okt
(P0-AUDIT.md): zona WIB (zona tenant), Kirim "—" bila tak ada baris perlu-kirim, "Terkirim" bila semua terkirim,
proforma PELUNASAN tak memicu aturan 4 bila faktur sudah ada (-> "Menunggu pelunasan"), cicilan = fase 2.

SATU tempat aturannya: `posisi()` dan `kirim()` MURNI (diuji per aturan); `fakta_daftar()` mengumpulkan faktanya
dalam kueri ber-batch (daftar <= 100 SO) dari sumber yang SUDAH ADA -- tanpa rumus uang baru (Iron Law 1/16/29):
  sisa SO = total − ringkasan_pesanan.tertutup (journal-derived; sama dengan payment_summary/P0)
  proforma terbayar = proforma_terbayar.alokasikan (eksplisit uang muka + kolam pesanan)
  perlu-kirim = COALESCE(soi.perlu_kirim, products.track_inventory, false) (= ringkasan_menunggu_kirim, V318)
  terkirim = Surat Jalan AKTIF per baris (so_kirim.terkirim_per_baris)
Filter tenant EKSPLISIT di tiap kueri (set_config bukan pagar).
"""
from datetime import date, timedelta
from decimal import Decimal

from .proforma_terbayar import alokasikan, ringkasan_pesanan
from .so_kirim import _AKTIF, belum_dikirim, terkirim_per_baris

NOL = Decimal("0")
SELESAI_KIRIM = ("completed", "cancelled")  # putusan pemilik #8: Kirim "—"
BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def posisi(status: str, sisa: Decimal, belum_bayar: list, ada_faktur: bool, sisa_faktur: Decimal,
           ada_surat_jalan: bool, dp_diterima: Decimal) -> tuple:
    """-> (teks, muted). Aturan 02 §Posisi, yang PERTAMA cocok.

    belum_bayar: proforma issued yang belum lunas, urut issued_at: [{"purpose", "termin_ke"}].
    Aturan 3 (cicilan) = fase 2, tidak aktif.
    """
    if status == "cancelled":
        return "Batal", True
    if sisa <= NOL:
        return "Lunas", True
    for p in belum_bayar:
        if p["purpose"] == "PELUNASAN" and ada_faktur:
            continue  # putusan pemilik #3: tagihan faktur yang sama -> aturan 5
        if p["purpose"] == "DP":
            return "Menunggu DP", False
        if p["purpose"] == "TERMIN":
            return f"Menunggu termin {p['termin_ke']}", False
        return "Menunggu tagihan", False
    if ada_faktur and sisa_faktur > NOL:
        return "Menunggu pelunasan", False
    if ada_surat_jalan and not ada_faktur:
        return "Perlu faktur", False
    if dp_diterima > NOL:
        return "Produksi", False
    return "Belum ditagih", False


def kirim(status: str, tanggal_kirim, perlu_kirim: bool, semua_terkirim: bool, hari: date) -> tuple:
    """-> (teks, gaya 'normal'|'muted'|'strong'). 01 §A + putusan pemilik #2 dan #8; `hari` = tanggal usaha zona tenant.

    #8 (1 Okt 2026): SO SELESAI (completed -- juga hasil "Tutup pesanan") atau batal -> "—". SO yang masih TERBUKA
    (termasuk Lunas tapi belum selesai) dengan baris perlu-kirim belum terkirim tetap "telat" tebal = sinyal nyata.
    """
    if status in SELESAI_KIRIM or not perlu_kirim:
        return "—", "muted"
    if semua_terkirim:
        return "Terkirim", "muted"
    if tanggal_kirim is None:
        return "—", "muted"
    teks = f"{tanggal_kirim.day} {BULAN[tanggal_kirim.month - 1]}"
    if tanggal_kirim == hari:
        return "Hari ini", "strong"
    if tanggal_kirim == hari + timedelta(days=1):
        return "Besok", "normal"
    if tanggal_kirim < hari:
        return f"{teks}, telat", "strong"
    return teks, "normal"


async def fakta_daftar(conn, tenant_id: str, rows: list, hari: date) -> dict:
    """rows: baris sales_orders (id, status, total_amount, expected_ship_date, quote_id).
    -> {str(so_id): {position_text, position_muted, ship_text, ship_style, doc_count}}."""
    ids = [r["id"] for r in rows]
    if not ids:
        return {}
    rg = await ringkasan_pesanan(conn, tenant_id, ids)

    pfs = [dict(p) for p in await conn.fetch(
        """SELECT id, sales_order_id, proforma_number, purpose, amount, status, issued_at
           FROM proformas WHERE tenant_id = $1 AND sales_order_id = ANY($2::uuid[])""",
        tenant_id, ids,
    )]
    eks = {r["pid"]: Decimal(str(r["s"])) for r in await conn.fetch(
        """SELECT proforma_id AS pid, SUM(amount) AS s FROM customer_deposits
           WHERE tenant_id = $1 AND status <> 'void' AND proforma_id = ANY($2::uuid[]) GROUP BY 1""",
        tenant_id, [p["id"] for p in pfs],
    )} if pfs else {}
    faktur = {r["so_id"]: r["n"] for r in await conn.fetch(
        """SELECT sales_order_id AS so_id, count(*) AS n FROM sales_invoices
           WHERE tenant_id = $1 AND sales_order_id = ANY($2::uuid[]) AND status NOT IN ('draft', 'void')
           GROUP BY 1""",
        tenant_id, ids,
    )}
    sj = {r["so_id"]: r["n"] for r in await conn.fetch(
        f"""SELECT si.sales_order_id AS so_id, count(DISTINCT f.id) AS n
            FROM invoice_fulfillments f JOIN sales_invoices si ON si.id = f.invoice_id AND si.tenant_id = f.tenant_id
            WHERE si.tenant_id = $1 AND si.sales_order_id = ANY($2::uuid[]) AND {_AKTIF}
            GROUP BY 1""",
        tenant_id, ids,
    )}
    baris = await conn.fetch(
        """SELECT soi.id, soi.sales_order_id AS so_id, soi.quantity
           FROM sales_order_items soi
           JOIN sales_orders so ON so.id = soi.sales_order_id AND so.tenant_id = $1
           LEFT JOIN products p ON p.id = soi.item_id AND p.tenant_id = so.tenant_id
           WHERE soi.sales_order_id = ANY($2::uuid[]) AND COALESCE(soi.perlu_kirim, p.track_inventory, false)""",
        tenant_id, ids,
    )
    per_baris, _ = await terkirim_per_baris(conn, tenant_id, ids)
    dep = {r["so_id"]: r["n"] for r in await conn.fetch(
        """SELECT COALESCE(cd.sales_order_id, p.sales_order_id) AS so_id, count(*) AS n
           FROM customer_deposits cd
           LEFT JOIN proformas p ON p.id = cd.proforma_id AND p.tenant_id = cd.tenant_id
           WHERE cd.tenant_id = $1 AND cd.status <> 'void'
             AND COALESCE(cd.sales_order_id, p.sales_order_id) = ANY($2::uuid[])
           GROUP BY 1""",
        tenant_id, ids,
    )}
    kw = {r["so_id"]: r["n"] for r in await conn.fetch(
        """SELECT si.sales_order_id AS so_id, count(DISTINCT rpa.payment_id) AS n
           FROM receive_payment_allocations rpa
           JOIN receive_payments rp ON rp.id = rpa.payment_id AND rp.tenant_id = $1 AND rp.status = 'posted'
           JOIN sales_invoices si ON si.id = rpa.invoice_id AND si.tenant_id = $1
           WHERE si.sales_order_id = ANY($2::uuid[]) AND rpa.status = 'active'
           GROUP BY 1""",
        tenant_id, ids,
    )}

    per_so_pf, per_so_baris = {}, {}
    for p in pfs:
        per_so_pf.setdefault(p["sales_order_id"], []).append(p)
    for b in baris:
        per_so_baris.setdefault(b["so_id"], []).append(b)

    hasil = {}
    for r in rows:
        sid = r["id"]
        o = rg[sid]
        sisa = Decimal(str(r["total_amount"] or 0)) - o["tertutup"]
        daftar_pf = per_so_pf.get(sid, [])
        alok = alokasikan(daftar_pf, o["tertutup"], eks) if daftar_pf else {}
        issued = sorted((p for p in daftar_pf if p["status"] == "issued"),
                        key=lambda p: (p["issued_at"] is None, p["issued_at"], p["proforma_number"] or ""))
        termin = [p["id"] for p in sorted((p for p in daftar_pf if p["status"] != "cancelled" and p["purpose"] == "TERMIN"),
                                          key=lambda p: (p["issued_at"] is None, p["issued_at"], p["proforma_number"] or ""))]
        belum_bayar = [{"purpose": p["purpose"], "termin_ke": (termin.index(p["id"]) + 1) if p["id"] in termin else None}
                       for p in issued if alok[p["id"]]["paid"] < Decimal(str(p["amount"]))]
        n_faktur = faktur.get(sid, 0)
        teks_pos, muted = posisi(r["status"], sisa, belum_bayar, n_faktur > 0, o["invoice_outstanding"],
                                 sj.get(sid, 0) > 0, o["dp_received"])
        bk = per_so_baris.get(sid, [])
        semua = bool(bk) and all(belum_dikirim(b["quantity"], per_baris.get(b["id"])) == NOL for b in bk)
        teks_kirim, gaya = kirim(r["status"], r["expected_ship_date"], bool(bk), semua, hari)
        n_dok = ((1 if r["quote_id"] else 0) + sum(1 for p in daftar_pf if p["status"] != "cancelled")
                 + dep.get(sid, 0) + kw.get(sid, 0) + sj.get(sid, 0) + n_faktur)
        hasil[str(sid)] = {"position_text": teks_pos, "position_muted": muted,
                           "ship_text": teks_kirim, "ship_style": gaya, "doc_count": n_dok}
    return hasil
