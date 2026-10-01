"""Rekap Pesanan (P3 SO-dokumen, spek 03-DOKUMEN): dokumen BANGKITAN (tak disimpan) -- satu halaman yang merangkum
tagihan dan pembayaran sebuah Pesanan Penjualan. SATU pemuat untuk PDF dan HTML panel.

Angka TANPA rumus baru (Iron Law 1/16/29):
  tertutup / dp / faktur = proforma_terbayar.ringkasan_pesanan (journal-derived; sama dengan Posisi P1 & payment_summary)
  sisa tagihan          = total SO − tertutup
  sudah dibayar         = tertutup − nota kredit   (tertutup = settled faktur + DP belum diterapkan; settled termasuk NK)
  terbayar per proforma = proforma_terbayar.terbayar_proforma (atribusi kanonik, sama dengan detail proforma)
  sisa per faktur       = compute_ar_outstanding
Baris PEMBAYARAN = uang muka (diterima) + penerimaan yang dialokasikan ke faktur SO − pengembalian uang muka; jumlahnya
WAJIB = sudah dibayar (selisih dicatat, pola selisih_riwayat faktur). Filter tenant EKSPLISIT di tiap kueri.
"""
import base64
import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException

from .proforma_terbayar import ringkasan_pesanan, terbayar_proforma

logger = logging.getLogger(__name__)
NOL = Decimal("0")
JENIS_PROFORMA = {"DP": "Uang muka", "TERMIN": "Termin", "PELUNASAN": "Pelunasan"}


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else NOL


async def muat_kop(conn, tenant_id: str) -> dict:
    """Kop usaha (nama/alamat/telepon/logo) -- bentuk SAMA dengan pemuat PDF lain."""
    row = await conn.fetchrow('SELECT display_name, address, phone, logo_url FROM "Tenant" WHERE id = $1', tenant_id)
    info = {"name": row["display_name"] if row else tenant_id, "address": row["address"] if row else None,
            "phone": row["phone"] if row else None, "logo_url": row["logo_url"] if row else None}
    logo = None
    if info["logo_url"]:
        p = Path(__file__).parent.parent / "static" / "logos" / info["logo_url"]
        if p.exists():
            logo = "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()
    info["logo_data"] = logo
    return info


async def muat_rekap_pesanan(conn, ctx, so_id) -> dict:
    """-> {"rekap": {...}, "tenant_info": {...}}. 404 bila SO tak ada di tenant ini."""
    tid = ctx["tenant_id"]
    so = await conn.fetchrow(
        """SELECT id, order_number, order_date, customer_name, total_amount, status,
                  payment_bank_name, payment_account_number, payment_account_holder
           FROM sales_orders WHERE id = $1 AND tenant_id = $2""",
        so_id, tid,
    )
    if not so:
        raise HTTPException(status_code=404, detail="Sales order not found")
    sid = so["id"]
    r = (await ringkasan_pesanan(conn, tid, [sid]))[sid]
    total = _d(so["total_amount"])
    sisa = total - r["tertutup"]
    dibayar = r["tertutup"] - r["credit_note"]

    # ── TAGIHAN: proforma (tak-batal; SO batal -> semua tampil "Batal", putusan pemilik #4) + faktur terbit ──
    terbayar = await terbayar_proforma(conn, tid, [sid])
    tagihan = []
    for p in await conn.fetch(
        """SELECT id, proforma_number, proforma_date, purpose, amount, status FROM proformas
           WHERE tenant_id = $1 AND sales_order_id = $2 AND status <> 'draft'
           ORDER BY proforma_date NULLS LAST, proforma_number""",
        tid, sid,
    ):
        if p["status"] == "cancelled" and so["status"] != "cancelled":
            continue
        bayar = terbayar.get(p["id"], {}).get("paid", NOL)
        batal = so["status"] == "cancelled" or p["status"] == "cancelled"
        tagihan.append({"nomor": p["proforma_number"], "tanggal": p["proforma_date"],
                        "jenis": "Proforma · " + JENIS_PROFORMA.get(p["purpose"], p["purpose"] or "-"),
                        "jumlah": _d(p["amount"]),
                        "status": "Batal" if batal else ("Lunas" if bayar >= _d(p["amount"]) else
                                                         ("Sebagian" if bayar > NOL else "Belum dibayar"))})
    faktur = await conn.fetch(
        """SELECT id, invoice_number, invoice_date, total_amount, status FROM sales_invoices
           WHERE tenant_id = $1 AND sales_order_id = $2 AND status NOT IN ('draft', 'void')
           ORDER BY invoice_date, invoice_number""",
        tid, sid,
    )
    sisa_f = {}
    if faktur:
        sisa_f = {x["invoice_id"]: _d(x["outstanding"]) for x in await conn.fetch(
            "SELECT invoice_id, outstanding FROM compute_ar_outstanding($1) WHERE invoice_id = ANY($2::uuid[])",
            tid, [f["id"] for f in faktur])}
    for f in faktur:
        s = sisa_f.get(f["id"], NOL)
        tagihan.append({"nomor": f["invoice_number"], "tanggal": f["invoice_date"], "jenis": "Faktur",
                        "jumlah": _d(f["total_amount"]),
                        "status": "Lunas" if s <= NOL else ("Sebagian" if s < _d(f["total_amount"]) else "Belum dibayar")})

    # ── PEMBAYARAN: uang muka (diterima) + penerimaan atas faktur SO − pengembalian ──
    bayar_rows = []
    for d in await conn.fetch(
        """SELECT cd.deposit_number, cd.deposit_date, cd.payment_method, cd.amount, cd.amount_refunded
           FROM customer_deposits cd
           LEFT JOIN proformas p ON p.id = cd.proforma_id AND p.tenant_id = cd.tenant_id
           WHERE cd.tenant_id = $1 AND cd.status <> 'void' AND cd.journal_id IS NOT NULL
             AND COALESCE(cd.sales_order_id, p.sales_order_id) = $2""",
        tid, sid,
    ):
        bayar_rows.append({"tanggal": d["deposit_date"], "jenis": "Uang muka", "nomor": d["deposit_number"],
                           "metode": "Tunai" if (d["payment_method"] or "").lower() == "cash" else "Transfer",
                           "jumlah": _d(d["amount"])})
        if _d(d["amount_refunded"]) > NOL:
            bayar_rows.append({"tanggal": None, "jenis": "Pengembalian uang muka", "nomor": d["deposit_number"],
                               "metode": "", "jumlah": -_d(d["amount_refunded"])})
    # Atas faktur SO: riwayat KANONIK per faktur (faktur_cetak.riwayat_pembayaran = yang dicetak faktur, kredit AR
    # jurnal). Penerimaan selalu; penerapan uang muka HANYA bila uang mukanya TAK tertaut SO (yang tertaut sudah
    # tercatat di atas saat DITERIMA -> jangan dihitung dua kali). Nota kredit = baris ringkasan, bukan pembayaran.
    # Terukur 1 Okt: kaos SO-2609-0180 dilunasi DA dari uang muka lepas -> tanpa cabang ini Σ kurang 10.389.600.
    from . import faktur_cetak as _fc
    tertaut = {b["nomor"] for b in bayar_rows}
    per_nomor = {}
    for f in faktur:
        for h in await _fc.riwayat_pembayaran(conn, tid, f["id"]):
            if h["sumber"] == "nota_kredit" or (h["sumber"] == "uang_muka" and h["nomor"] in tertaut):
                continue
            k = (h["sumber"], h["nomor"])
            if k in per_nomor:
                per_nomor[k]["jumlah"] += h["jumlah"]
            else:
                per_nomor[k] = {"tanggal": h["tanggal"], "nomor": h["nomor"], "metode": h["metode"] or "-",
                                "jenis": "Pembayaran faktur" if h["sumber"] == "penerimaan" else "Uang muka (diterapkan)",
                                "jumlah": h["jumlah"]}
    bayar_rows.extend(per_nomor.values())
    bayar_rows.sort(key=lambda b: (b["tanggal"] or date.max, b["nomor"] or ""))
    selisih = sum((b["jumlah"] for b in bayar_rows), NOL) - dibayar
    if selisih != NOL:
        logger.warning("[REKAP] SO %s: Σ pembayaran != sudah dibayar (selisih %s)", so["order_number"], selisih)

    rek = await _fc.muat_rekening(conn, tid)
    status = "batal" if so["status"] == "cancelled" else ("lunas" if sisa <= NOL else "terbuka")
    rekap = {
        "id": str(sid), "order_number": so["order_number"], "order_date": so["order_date"],
        "customer_name": so["customer_name"], "status": status,
        "total": total, "dibayar": dibayar, "nota_kredit": r["credit_note"],
        "sisa": NOL if status == "batal" else max(sisa, NOL),  # pesanan batal: tak ada yang masih ditagih
        "tagihan": tagihan, "pembayaran": bayar_rows, "selisih_pembayaran": selisih,
        "payment_bank_name": so["payment_bank_name"], "payment_account_number": so["payment_account_number"],
        "rekening_pemilik_cetak": _fc.pemilik_dari(rek, so["payment_bank_name"], so["payment_account_number"],
                                                   so["payment_account_holder"]),
    }
    return {"rekap": rekap, "tenant_info": await muat_kop(conn, tid)}
