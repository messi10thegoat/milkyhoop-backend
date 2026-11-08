"""Riwayat FAKTUR PEMBELIAN (tagihan) -- kontrak seragam 22 Okt 2026 (celah WORKSPACE): bentuk SAMA dengan GET /sales-orders/{id}/history
(services/so_riwayat) dan /purchase-orders/{id}/history (services/po_riwayat), supaya petaRiwayatPolish FE dipakai apa adanya.
Ekor bersama so_riwayat._selesaikan (gabung audit_logs, nama aktor satu kueri, zona tenant, terbaru dulu).

Sumber kejadian (kolom beraktor; tak ditebak):
  tagihan     : dibuat (created_by) · diposting (posted_by) · dibatalkan (voided_at; kolom aktor TIDAK ada -> null + alasan)
  bayar keluar: Pembayaran Keluar beralokasi ke tagihan ini -- dibayar (posted_by) · dibatalkan (voided_by + alasan)   [izin send_payment]
  uang muka   : penerapan UM vendor ke tagihan ini (vendor_deposit_applications.created_by)                         [izin vendor_deposit]
  nota debit  : diterapkan (vendor_credit_applications.created_by) · dilepas (reversed_by + alasan, V443)            [izin debit_note]
  nota debit ATAS faktur ini (vendor_credits.original_bill_id; BUG-052, cermin so_riwayat nota kredit original_invoice_id):
                dibuat · diposting · dibatalkan                                                                [izin debit_note]
  audit_logs  : entitas bills (ENTITAS_AUDIT)
Dokumen terkait tanpa izin baca -> `omitted` (FE jujur, bukan diam). None = tagihan tak ada di tenant ini (-> 404)."""
from typing import Awaitable, Callable, Optional

from .audit_dokumen import nama as nama_event
from .so_riwayat import _Kumpul, _rp, _selesaikan

MODUL_TAGIHAN = {"bill": "purchase_invoice", "payment_out": "send_payment", "vendor_deposit": "vendor_deposit", "debit_note": "debit_note"}


# Kolom tagihan untuk bagian_tagihan (pemanggil menambah WHERE): satu definisi untuk riwayat tagihan DAN riwayat PO (BUG-112 d).
SQL_TAGIHAN = """SELECT b.id, b.invoice_number, b.amount, b.created_at, b.created_by, b.posted_at, b.posted_by, b.voided_at, b.voided_reason,
                        po.po_number
                 FROM bills b LEFT JOIN purchase_orders po ON po.id = b.purchase_order_id AND po.tenant_id = b.tenant_id"""


async def bagian_tagihan(conn, tenant_id: str, k: _Kumpul, bills: list, lihat: dict, entitas_audit: dict) -> None:
    """Kejadian tagihan + anak-anaknya (bayar keluar, penerapan UM vendor, nota debit) untuk SATU ATAU BANYAK tagihan (baris SQL_TAGIHAN).
    Bersama riwayat tagihan dan riwayat PO (BUG-112 d, pola so_riwayat._bagian_faktur). Anak disaring `lihat`; tagihan sendiri selalu.
    id ND atas tagihan ditambahkan ke `entitas_audit` (gabungan audit_logs, pola so_riwayat._bagian_faktur)."""
    if not bills:
        return
    nomor = {b["id"]: b["invoice_number"] for b in bills}
    ids = list(nomor)
    for b in bills:
        n = b["invoice_number"]
        asal = f" dari pesanan {b['po_number']}" if b["po_number"] else ""
        # BUG-058 (Law 12): nominal SAAT DIBUAT, bukan nilai sekarang -- total_sebelum ubah PERTAMA (BILL_UPDATED); belum pernah diubah = sama
        awal = await conn.fetchval(
            """SELECT metadata->>'total_sebelum' FROM audit_logs WHERE tenant_id = $1 AND entity_type = 'bills' AND entity_id = $2::uuid
               AND "eventType" = $3 AND metadata ? 'total_sebelum' ORDER BY "createdAt", id LIMIT 1""", tenant_id, str(b["id"]),
            nama_event("bill", "updated"))
        k.tambah(b["created_at"], "TAGIHAN_DIBUAT", f"Faktur pembelian {n} {_rp(awal if awal is not None else b['amount'])} dibuat{asal}",
                 b["created_by"], "bill", b["id"], n)
        k.tambah(b["posted_at"], "TAGIHAN_DIPOSTING", f"Faktur pembelian {n} diposting", b["posted_by"], "bill", b["id"], n)
        alasan = f": {b['voided_reason']}" if b["voided_reason"] else ""
        k.tambah(b["voided_at"], "TAGIHAN_DIBATALKAN", f"Faktur pembelian {n} dibatalkan (void){alasan}", None, "bill", b["id"], n)

    if lihat["payment_out"]:
        batal = set()  # satu pembayaran beralokasi ke beberapa tagihan PO -> "dibatalkan" sekali
        for p in await conn.fetch(
            """SELECT bp.id, bp.payment_number, bp.posted_at, bp.posted_by, bp.voided_at, bp.voided_by, bp.void_reason, bpa.bill_id,
                      SUM(bpa.amount_applied) AS jumlah
               FROM bill_payment_allocations bpa JOIN bill_payments_v2 bp ON bp.id = bpa.payment_id AND bp.tenant_id = $1
               WHERE bpa.bill_id = ANY($2::uuid[])
               GROUP BY bp.id, bp.payment_number, bp.posted_at, bp.posted_by, bp.voided_at, bp.voided_by, bp.void_reason, bpa.bill_id""",
                tenant_id, ids):
            pn = p["payment_number"]
            k.tambah(p["posted_at"], "PEMBAYARAN_DIBAYAR",
                     f"Pembayaran {pn} {_rp(p['jumlah'])} dialokasikan ke faktur pembelian {nomor[p['bill_id']]}",
                     p["posted_by"], "payment_out", p["id"], pn)
            if p["id"] not in batal:
                batal.add(p["id"])
                al = f": {p['void_reason']}" if p["void_reason"] else ""
                k.tambah(p["voided_at"], "PEMBAYARAN_DIBATALKAN", f"Pembayaran {pn} dibatalkan{al}", p["voided_by"], "payment_out", p["id"], pn)

    if lihat["vendor_deposit"]:
        for a in await conn.fetch(
            """SELECT vda.id AS kunci, vda.bill_id, vda.amount, vda.created_at, vda.created_by, vd.id, vd.deposit_number
               FROM vendor_deposit_applications vda JOIN vendor_deposits vd ON vd.id = vda.vendor_deposit_id AND vd.tenant_id = $1
               WHERE vda.bill_id = ANY($2::uuid[])""", tenant_id, ids):
            dn = a["deposit_number"]
            k.tambah(a["created_at"], "UANG_MUKA_DITERAPKAN",
                     f"Uang muka {dn} {_rp(a['amount'])} diterapkan ke faktur pembelian {nomor[a['bill_id']]}",
                     a["created_by"], "vendor_deposit", a["id"], dn, kunci=a["kunci"])

    if lihat["debit_note"]:
        for a in await conn.fetch(
            """SELECT vca.id AS kunci, vca.bill_id, vca.amount_applied, vca.created_at, vca.created_by, vca.reversed_at, vca.reversed_by,
                      vca.reversal_reason, vc.id, vc.credit_number
               FROM vendor_credit_applications vca JOIN vendor_credits vc ON vc.id = vca.vendor_credit_id AND vc.tenant_id = $1
               WHERE vca.tenant_id = $1 AND vca.bill_id = ANY($2::uuid[])""", tenant_id, ids):
            cn, n = a["credit_number"], nomor[a["bill_id"]]
            k.tambah(a["created_at"], "NOTA_DEBIT_DITERAPKAN", f"Nota debit {cn} {_rp(a['amount_applied'])} diterapkan ke faktur pembelian {n}",
                     a["created_by"], "debit_note", a["id"], cn, kunci=a["kunci"])
            al = f": {a['reversal_reason']}" if a["reversal_reason"] else ""
            k.tambah(a["reversed_at"], "NOTA_DEBIT_DILEPAS", f"Nota debit {cn} dilepas dari faktur pembelian {n}{al}",
                     a["reversed_by"], "debit_note", a["id"], cn, kunci=a["kunci"])

    # ---- nota debit ATAS tagihan ini (BUG-052): retur dari faktur memotong hutang langsung saat posting, tanpa baris applications ----
    # BUG-069 (V485): retur dari tagihan LUNAS = ND bebas (original_bill_id NULL) bersumber return_bill_id -> ikut dibaca
    if lihat["debit_note"]:
        vcs = await conn.fetch(
            """SELECT id, credit_number, total_amount, created_at, created_by, posted_at, posted_by, voided_at, voided_by, voided_reason
               FROM vendor_credits WHERE tenant_id = $1
                AND (original_bill_id = ANY($2::uuid[]) OR return_bill_id = ANY($2::uuid[]))""", tenant_id, ids)
        if vcs:
            entitas_audit["debit_note"] = [v["id"] for v in vcs]
        for v in vcs:
            cn = v["credit_number"]
            k.tambah(v["created_at"], "NOTA_DEBIT_DIBUAT", f"Nota debit {cn} {_rp(v['total_amount'])} dibuat", v["created_by"], "debit_note", v["id"], cn)
            k.tambah(v["posted_at"], "NOTA_DEBIT_DIPOSTING", f"Nota debit {cn} diposting", v["posted_by"], "debit_note", v["id"], cn)
            al = f": {v['voided_reason']}" if v["voided_reason"] else ""
            k.tambah(v["voided_at"], "NOTA_DEBIT_DIBATALKAN", f"Nota debit {cn} dibatalkan{al}", v["voided_by"], "debit_note", v["id"], cn)


async def riwayat_tagihan(conn, tenant_id: str, bill_id, boleh: Callable[[str], Awaitable[bool]], limit: int = 200) -> Optional[dict]:
    b = await conn.fetchrow(f"{SQL_TAGIHAN} WHERE b.id = $1 AND b.tenant_id = $2", bill_id, tenant_id)
    if not b:
        return None
    izin = {}
    for modul in MODUL_TAGIHAN.values():
        if modul not in izin:
            izin[modul] = bool(await boleh(modul))
    lihat = {j: izin[m] for j, m in MODUL_TAGIHAN.items()}
    omitted = sorted({m for m in izin if not izin[m]})

    k = _Kumpul()
    entitas_audit = {"bill": [b["id"]]}
    await bagian_tagihan(conn, tenant_id, k, [b], lihat, entitas_audit)
    keluar, total = await _selesaikan(conn, tenant_id, k, entitas_audit, lihat, limit)
    return {"bill_id": str(b["id"]), "invoice_number": b["invoice_number"], "events": keluar, "total": total, "omitted": omitted}
