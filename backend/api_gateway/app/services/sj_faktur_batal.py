"""Batal mandiri Surat Jalan asal FAKTUR (VOID-SJ-FAKTUR, 15 Okt 2026; putusan MASTER + izin LANGSUNG pemilik: aturan buku baru).

Pola NetSuite: Item Fulfillment = transaksi sendiri; membatalkannya membalik HPP/stok (dan pengakuan pendapatan bila diakui saat
kirim, setara ARM), faktur TETAP utuh (piutang, pembayaran, nomor, status). Endpoint SAMA dengan SJ asal 'so'
(POST /deliveries/{id}/void[/preview]); services/sj_so.rencana_void/void mendelegasikan ke sini bila asal = 'faktur'.

Jejak SJ faktur yang dibalik (routers/sales_invoices._execute_fulfillment), SEMUA dalam transaksi PEMANGGIL (Law 23):
  - jurnal HPP f.journal_id (INVOICE_FULFILLMENT: Dr HPP / Cr Persediaan) -> pembalik INVOICE_FULFILLMENT_REVERSAL + reversed_by_id
    (Law 2/26: jurnal asli tetap POSTED);
  - kartu stok: HANYA baris inventory_ledger dengan journal_id = f.journal_id (source_id = id FAKTUR dipakai bersama semua SJ faktur
    itu; pembalik per source_id akan membalik SJ lain juga) -> record_inventory_reversal(journal_id=...), batch ikut dipulihkan;
  - jurnal pendapatan f.revenue_journal_id (INVOICE_REVENUE: Dr Dimuka / Cr Penjualan) -> pembalik INVOICE_REVENUE_REVERSAL: pendapatan
    kembali DITANGGUHKAN (Check 16: Σ(allocated - recognized) == saldo Dimuka tetap setara);
  - cache baris faktur (Law 16, in-tx): fulfilled_qty turun qty SJ; recognized_amount turun bagian pendapatan SJ itu (`bagi_pendapatan`,
    jumlahnya == total jurnal pendapatan PERSIS); fulfillment_status/revenue_status dihitung ulang -> faktur kembali "perlu dikirim";
  - SJ status 'voided' (trigger trg_so_shipped_recompute menghitung ulang SO); serah/resi/lampiran TIDAK dihapus -- ikut batal lewat
    relasi (SJ voided; PUT serah menolak SJ_VOID), riwayat mencatat resi yang ikut batal.
Baris non-stok: nol jurnal, nol kartu -> hanya fulfilled_qty + status.
Kunci (Law 13): INVOICE_VOID:{faktur} lalu INVOICE_FULFILL (tenant, faktur) -- sama dengan void faktur dan /fulfill.
"""
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from .so_riwayat import catat_riwayat
from ..utils.tanggal_tenant import tanggal_dokumen

SUMBER_KARTU = "INVOICE_FULFILLMENT"
SUMBER_HPP_BALIK = "INVOICE_FULFILLMENT_REVERSAL"
SUMBER_PDPT_BALIK = "INVOICE_REVENUE_REVERSAL"
_NOL = Decimal("0")
_DUA = Decimal("0.01")

SQL_BARIS = """
SELECT ifi.id, ifi.invoice_item_id, ifi.quantity, ifi.total_cost, sii.description, sii.quantity AS q_faktur,
       sii.allocated_amount, sii.recognized_amount, sii.fulfilled_qty
FROM invoice_fulfillment_items ifi
JOIN invoice_fulfillments f ON f.id = ifi.fulfillment_id AND f.tenant_id = $2
JOIN sales_invoice_items sii ON sii.id = ifi.invoice_item_id AND sii.invoice_id = f.invoice_id
WHERE ifi.fulfillment_id = $1
ORDER BY ifi.id
"""


def _d(v) -> Decimal:
    return Decimal(str(v if v is not None else 0))


def _r2(x) -> Decimal:
    return Decimal(str(x)).quantize(_DUA, rounding=ROUND_HALF_UP)


def bagi_pendapatan(total, baris: list) -> dict:
    """Murni. Bagian pendapatan SJ per baris SJ ber-stok (total_cost > 0; non-stok tak pernah diakui saat kirim).
    Proporsional allocated x qty_sj / qty_faktur (2dp HALF_UP), sisa pembulatan diserap baris terakhir -> Σ == total PERSIS.
    Tiap bagian 0 <= bagian <= recognized_amount baris; bila tak mungkin -> ValueError (blok, bukan tebakan)."""
    total = _d(total)
    stok = [b for b in baris if _d(b["total_cost"]) > _NOL]
    if total == _NOL:
        return {b["id"]: _NOL for b in stok}
    if not stok:
        raise ValueError("ada jurnal pendapatan tetapi tak ada baris ber-stok")
    out, jalan = {}, _NOL
    for b in stok[:-1]:
        q_faktur = _d(b["q_faktur"])
        bagian = _r2(_d(b["allocated_amount"]) * _d(b["quantity"]) / q_faktur) if q_faktur > _NOL else _NOL
        bagian = max(_NOL, min(bagian, _d(b["recognized_amount"]), total - jalan))
        out[b["id"]] = bagian
        jalan += bagian
    akhir = stok[-1]
    sisa = total - jalan
    if sisa < _NOL or sisa > _d(akhir["recognized_amount"]):
        raise ValueError(f"sisa {sisa} di luar 0..{akhir['recognized_amount']}")
    out[akhir["id"]] = sisa
    return out


async def kunci(conn, tid: str, invoice_id) -> None:
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"INVOICE_VOID:{str(invoice_id)}")
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))", tid, f"INVOICE_FULFILL:{str(invoice_id)}")


async def rencana(conn, tid: str, f, alasan: Optional[str], blok, periode_terbuka) -> dict:
    """f = baris invoice_fulfillments (asal 'faktur'). blok/periode_terbuka = pembantu sj_so (satu bentuk blok). Semua blok."""
    blocks: list = []
    ulang = f["status"] == "voided"
    inv = await conn.fetchrow("SELECT id, invoice_number, status FROM sales_invoices WHERE id = $1 AND tenant_id = $2",
                              f["invoice_id"], tid)
    baris = list(await conn.fetch(SQL_BARIS, f["id"], tid))
    pendapatan, total_pdpt = {}, _NOL
    if not ulang:
        if not (alasan or "").strip():
            blocks.append(blok("SJ_ALASAN_WAJIB", "Alasan pembatalan wajib diisi.", 422))
        if inv is None or inv["status"] in ("void", "draft"):
            blocks.append(blok("SJ_FAKTUR_TIDAK_AKTIF",
                               f"Faktur {inv['invoice_number'] if inv else ''} sudah tidak aktif; Surat Jalan ini tak bisa dibatalkan "
                               "terpisah.".replace("  ", " ")))
        nk = await conn.fetch(
            """SELECT credit_note_number FROM credit_notes
               WHERE tenant_id = $1 AND original_invoice_id = $2 AND status NOT IN ('draft', 'void') ORDER BY credit_note_number""",
            tid, f["invoice_id"])
        if nk:
            blocks.append(blok("SJ_FAKTUR_ADA_NOTA_KREDIT",
                               f"Faktur {inv['invoice_number'] if inv else ''} punya nota kredit "
                               + ", ".join(r["credit_note_number"] or "-" for r in nk)
                               + "; batalkan nota kreditnya dulu sebelum membatalkan Surat Jalan ini."))
        for jid in (f["journal_id"], f["revenue_journal_id"]):
            if jid is not None and await conn.fetchval(
                    "SELECT reversed_by_id IS NOT NULL FROM journal_entries WHERE id = $1 AND tenant_id = $2", jid, tid):
                blocks.append(blok("SJ_FAKTUR_JURNAL_SUDAH_DIBALIK",
                                   "Jurnal Surat Jalan ini sudah dibalik sebelumnya; pembatalan dihentikan supaya buku tidak dibalik dua kali."))
                break
        if f["revenue_journal_id"] is not None:
            total_pdpt = _d(await conn.fetchval("SELECT total_debit FROM journal_entries WHERE id = $1 AND tenant_id = $2",
                                                f["revenue_journal_id"], tid))
        try:
            pendapatan = bagi_pendapatan(total_pdpt, baris)
        except ValueError:
            blocks.append(blok("SJ_FAKTUR_PENDAPATAN_TAK_COCOK",
                               "Pendapatan yang diakui Surat Jalan ini tidak cocok dengan baris fakturnya; pembatalan dihentikan. "
                               "Hubungi admin untuk pemeriksaan."))
        for b in baris:
            if _d(b["fulfilled_qty"]) < _d(b["quantity"]):
                blocks.append(blok("SJ_FAKTUR_QTY_TAK_COCOK",
                                   f"Jumlah terkirim baris '{b['description'] or ''}' di faktur lebih kecil dari Surat Jalan ini; "
                                   "pembatalan dihentikan. Hubungi admin untuk pemeriksaan."))
                break
        hari = await tanggal_dokumen(conn, tid)
        if f["journal_id"] is not None or f["revenue_journal_id"] is not None:
            msg = await periode_terbuka(conn, tid, hari)
            if msg:
                blocks.append(blok("SJ_PERIODE_TUTUP", msg))
    notes = [] if ulang else [
        "Faktur " + (inv["invoice_number"] if inv else "") + " tetap berlaku (piutang dan pembayaran tidak berubah) dan kembali "
        "perlu dikirim."] + (["Barang kembali ke stok; HPP dan pengakuan pendapatan Surat Jalan ini dibalik."]
                             if f["journal_id"] is not None else [])
    return {"sj": f, "blocks": blocks, "ulang": ulang, "notes": notes, "faktur": inv, "baris": baris,
            "pendapatan": pendapatan, "total_pendapatan": total_pdpt}


async def void(conn, ctx: dict, r: dict, alasan: str, jurnal) -> None:
    """Tulis (rencana tanpa blok, di bawah `kunci`). jurnal = sj_so._jurnal (cek Σdebit == Σkredit, Law 4)."""
    from .inventory_helpers import record_inventory_reversal
    from ..routers.sales_invoices import _update_invoice_fulfillment_status
    tid, uid = ctx["tenant_id"], ctx.get("user_id")
    f, inv = r["sj"], r["faktur"]
    hari = await tanggal_dokumen(conn, tid)
    nomor = f["fulfillment_number"]

    async def balik(jid, prefix, sumber, deskripsi):
        orig = await conn.fetch(
            "SELECT account_id, debit, credit, memo FROM journal_lines WHERE journal_id = $1 ORDER BY line_number", jid)
        rid, _ = await jurnal(conn, tid, uid, prefix=prefix, tanggal=hari, source_type=sumber, source_id=f["invoice_id"],
                              deskripsi=deskripsi, baris=[(o["account_id"], o["credit"], o["debit"], f"VOID: {o['memo']}") for o in orig],
                              reversal_of=jid, alasan=alasan)
        await conn.execute("UPDATE journal_entries SET reversed_by_id = $2, reversed_at = NOW() WHERE id = $1 AND tenant_id = $3",
                           jid, rid, tid)
        return rid

    if f["journal_id"] is not None:
        rid = await balik(f["journal_id"], "COGS-REV", SUMBER_HPP_BALIK, f"VOID HPP Surat Jalan {nomor}")
        await record_inventory_reversal(conn, tid, source_type=SUMBER_KARTU, source_id=f["invoice_id"], reversal_journal_id=rid,
                                        created_by=uid, reversal_date=hari, notes_prefix=f"VOID {nomor}", journal_id=f["journal_id"])
    if f["revenue_journal_id"] is not None:
        await balik(f["revenue_journal_id"], "REV", SUMBER_PDPT_BALIK, f"VOID Pengakuan Pendapatan Surat Jalan {nomor}")
    for b in r["baris"]:
        await conn.execute(
            """UPDATE sales_invoice_items SET fulfilled_qty = fulfilled_qty - $2, recognized_amount = recognized_amount - $3
               WHERE id = $1 AND invoice_id = $4""",
            b["invoice_item_id"], str(_d(b["quantity"])), str(r["pendapatan"].get(b["id"], _NOL)), f["invoice_id"])
    await _update_invoice_fulfillment_status(conn, f["invoice_id"], tid)
    # trigger trg_so_shipped_recompute (AFTER UPDATE) menghitung ulang SO faktur ini.
    await conn.execute(
        "UPDATE invoice_fulfillments SET status = 'voided', voided_at = NOW(), voided_reason = $3, updated_at = NOW() "
        "WHERE id = $1 AND tenant_id = $2 AND asal = 'faktur'", f["id"], tid, alasan)
    resi = [x["nomor_resi"] for x in await conn.fetch(
        "SELECT nomor_resi FROM pengiriman_resi WHERE tenant_id = $1 AND fulfillment_id = $2 ORDER BY urutan, created_at", tid, f["id"])]
    serah = await conn.fetchval("SELECT metode_nama FROM pengiriman_serah WHERE tenant_id = $1 AND fulfillment_id = $2", tid, f["id"])
    await catat_riwayat(
        conn, tid, "invoice_fulfillments", f["id"], nomor, "SURAT_JALAN_FAKTUR_DIBATALKAN", uid,
        f"Surat Jalan {nomor} dibatalkan: {alasan}" + (f" (resi {', '.join(resi)} ikut batal)" if resi else ""),
        {"alasan": alasan, "invoice_id": str(f["invoice_id"]), "invoice_number": inv["invoice_number"], "resi": resi,
         "serah": serah, "pendapatan_dibalik": str(r["total_pendapatan"])},
        source="api:sj_so.void")
