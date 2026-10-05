"""Dokumen TERKAIT satu pesanan (GET /sales-orders/{id}/documents?bentuk=terkait) — 5 Okt 2026, kontrak WORKSPACE D0.

Bentuk BARU, aditif: bundel panel lama (susun_dokumen) tidak disentuh. Kelompok berurutan alur:
quote → proforma → deposit → invoice → delivery → receipt → credit_note; kelompok kosong tidak dikirim; SETIAP
kelompok selalu membawa `summary` (FE membaca ketiadaannya sebagai bundel lama).
- Draf TIDAK ikut. Dokumen batal IKUT dengan voided=true dan TIDAK dihitung di summary.
- summary.value = teks tampil dari server (tg.rp / tg.qty) — FE tidak menjumlah/memformat.
- Penerimaan yang membayar faktur >1 SO tampil di tiap SO; amount = alokasi ke faktur SO ini saja.
- Filter tenant eksplisit di SETIAP kueri; jumlah kueri tetap (tanpa N+1) — dikunci tes.
"""
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException

from . import teks_galat as tg
from .status_uang_muka import status_detail_dp
from ..utils.metode_pembayaran import label_metode_layar

NOL = Decimal("0")
URUTAN = ("quote", "order", "proforma", "deposit", "invoice", "delivery", "receipt", "credit_note")
LABEL_KELOMPOK = {"quote": "Penawaran", "order": "Pesanan", "proforma": "Proforma", "deposit": "Uang muka", "invoice": "Faktur",
                  "delivery": "Pengiriman", "receipt": "Penerimaan", "credit_note": "Nota kredit"}
BATAL = {"void", "voided", "cancelled"}
STATUS_KIRIM = {"posted": "Terkirim", "voided": "Batal"}
INFO_PROFORMA = {"DP": "DP", "PELUNASAN": "Pelunasan"}


def _d(x) -> Decimal:
    return Decimal(str(x if x is not None else 0))


def _uang(x):
    return None if x is None else f"{_d(x):.2f}"


def _tgl(v):
    return v.isoformat()[:10] if v is not None else None


def _dok(kind, id_, nomor, tgl, info, jumlah, status, label, batal):
    return {"kind": kind, "id": str(id_), "number": nomor, "date": _tgl(tgl), "info": info or None,
            "amount": _uang(jumlah), "status": status, "status_label": label, "voided": bool(batal)}


def _hidup(docs):
    return [d for d in docs if not d["voided"]]


def _jumlah(docs) -> Decimal:
    return sum((_d(d["amount"]) for d in _hidup(docs)), NOL)


async def susun_terkait(conn, tid: str, so_id: UUID, sertakan_order: bool = False) -> dict:
    """sertakan_order (5 Okt 2026, OPT-IN `&sertakan=order`): kelompok 'order' = SO induk sendiri di posisi alur
    sesudah penawaran. Tanpa opt-in keluaran IDENTIK dengan sebelumnya (pembaca ketat FE r217 menolak kunci asing)."""
    from .kode_order import muat_setelan
    from .proforma_terbayar import terbayar_proforma
    from .so_kirim import _AKTIF
    from .teks_galat import ALASAN_NK

    so = await conn.fetchrow(
        """SELECT id, order_number, order_code, order_title, quote_id, order_date, total_amount, status FROM sales_orders
           WHERE id = $1 AND tenant_id = $2""", so_id, tid)
    if not so:
        raise HTTPException(status_code=404, detail="Pesanan tidak ditemukan.")
    sid = so["id"]
    st = await muat_setelan(conn, tid)
    g = {}

    # Penawaran: sumber SO (quote_id) atau penawaran yang dikonversi ke SO ini.
    g["quote"] = [_dok("quote", q["id"], q["quote_number"], q["quote_date"], None, q["total_amount"], q["status"],
                       tg.status_id("quote", q["status"]), q["status"] in BATAL)
                  for q in await conn.fetch(
                      """SELECT id, quote_number, quote_date, total_amount, status FROM quotes
                         WHERE tenant_id = $1 AND status <> 'draft'
                           AND (id = $3 OR (converted_to_type = 'sales_order' AND converted_to_id = $2))
                         ORDER BY quote_date, quote_number""", tid, sid, so["quote_id"])]

    g["order"] = [_dok("order", so["id"], so["order_number"], so["order_date"],
                       " · ".join(x for x in (so["order_code"], so["order_title"]) if x) or None, so["total_amount"],
                       so["status"], tg.status_id("so", so["status"]), so["status"] == "cancelled")] if sertakan_order else []

    pros = await conn.fetch(
        """SELECT id, proforma_number, proforma_date, purpose, amount, status FROM proformas
           WHERE tenant_id = $1 AND sales_order_id = $2 AND status <> 'draft'
           ORDER BY proforma_date, proforma_number""", tid, sid)
    terbayar = await terbayar_proforma(conn, tid, [sid]) if pros else {}
    termin = [p["id"] for p in pros if p["purpose"] == "TERMIN" and p["status"] not in BATAL]
    g["proforma"] = [_dok("proforma", p["id"], p["proforma_number"], p["proforma_date"],
                          f"Termin {termin.index(p['id']) + 1}" if p["id"] in termin else INFO_PROFORMA.get(p["purpose"]),
                          p["amount"], p["status"], tg.status_id("proforma", p["status"]), p["status"] in BATAL)
                     for p in pros]
    bayar_pro = sum((terbayar.get(p["id"], {}).get("paid", NOL) for p in pros if p["status"] not in BATAL), NOL)

    # Uang muka: tertaut SO (langsung / lewat proforma) atau diterapkan ke faktur SO (himpunan = bundel panel).
    dep = await conn.fetch(
        """SELECT cd.id, cd.deposit_number, cd.deposit_date, cd.amount, cd.amount_applied, cd.amount_refunded,
                  cd.status, cd.payment_method, p.proforma_number
           FROM customer_deposits cd
           LEFT JOIN proformas p ON p.id = cd.proforma_id AND p.tenant_id = cd.tenant_id
           WHERE cd.tenant_id = $1 AND cd.status <> 'draft'
             AND (COALESCE(cd.sales_order_id, p.sales_order_id) = $2
                  OR cd.id IN (SELECT cda.deposit_id FROM customer_deposit_applications cda
                               JOIN sales_invoices si ON si.id = cda.invoice_id AND si.tenant_id = $1
                               WHERE cda.tenant_id = $1 AND cda.status = 'active' AND si.sales_order_id = $2))
           ORDER BY cd.deposit_date, cd.deposit_number""", tid, sid)
    g["deposit"] = []
    sisa_dp = NOL
    for d in dep:
        sd = status_detail_dp(d["status"], d["amount_applied"], d["amount_refunded"])
        info = " · ".join(x for x in (label_metode_layar(d["payment_method"]),
                                      d["proforma_number"] and f"Proforma {d['proforma_number']}") if x)
        g["deposit"].append(_dok("deposit", d["id"], d["deposit_number"], d["deposit_date"], info, d["amount"],
                                 sd, tg.status_id("dp", sd), d["status"] in BATAL))
        if d["status"] not in BATAL:
            sisa_dp += max(_d(d["amount"]) - _d(d["amount_applied"]) - _d(d["amount_refunded"]), NOL)

    fak = await conn.fetch(
        """SELECT id, invoice_number, invoice_date, total_amount, status FROM sales_invoices
           WHERE tenant_id = $1 AND sales_order_id = $2 AND status <> 'draft'
           ORDER BY invoice_date, invoice_number""", tid, sid)
    hidup_f = [f["id"] for f in fak if f["status"] not in BATAL]
    sisa_f = {x["invoice_id"]: _d(x["outstanding"]) for x in await conn.fetch(
        "SELECT invoice_id, outstanding FROM compute_ar_outstanding($1) WHERE invoice_id = ANY($2::uuid[])",
        tid, hidup_f)} if hidup_f else {}
    g["invoice"] = [_dok("invoice", f["id"], f["invoice_number"], f["invoice_date"],
                         # lunas -> None: status_label sudah "Lunas" (WORKSPACE 5 Okt: info ganda)
                         None if f["status"] in BATAL or sisa_f.get(f["id"], NOL) <= NOL
                         else f"Sisa {tg.rp(sisa_f[f['id']])}",
                         f["total_amount"], f["status"], tg.status_id("si", f["status"]), f["status"] in BATAL)
                    for f in fak]

    # Pengiriman: SEMUA surat jalan atas faktur SO (yang batal ditandai); qty per dokumen.
    satuan = await conn.fetch(
        """SELECT DISTINCT COALESCE(NULLIF(trim(unit), ''), 'pcs') AS u FROM sales_order_items
           WHERE sales_order_id = $1 AND sales_order_id IN (SELECT id FROM sales_orders WHERE tenant_id = $2)""",
        sid, tid)
    sat = f" {satuan[0]['u']}" if len(satuan) == 1 else ""
    kir = await conn.fetch(
        f"""SELECT f.id, f.fulfillment_number, f.fulfillment_date, f.status, si.invoice_number,
                   COALESCE(SUM(ifi.quantity), 0) AS qty, ({_AKTIF}) AS aktif
            FROM invoice_fulfillments f
            JOIN sales_invoices si ON si.id = f.invoice_id AND si.tenant_id = f.tenant_id
            LEFT JOIN invoice_fulfillment_items ifi ON ifi.fulfillment_id = f.id
            WHERE f.tenant_id = $1 AND si.sales_order_id = $2
            GROUP BY f.id, f.fulfillment_number, f.fulfillment_date, f.status, si.invoice_number, f.voided_at
            ORDER BY f.fulfillment_date, f.fulfillment_number""", tid, sid)
    g["delivery"] = [_dok("delivery", k["id"], k["fulfillment_number"], k["fulfillment_date"],
                          f"{tg.qty(k['qty'])}{sat} · Faktur {k['invoice_number']}", None,
                          k["status"] if k["aktif"] else "voided",
                          STATUS_KIRIM.get(k["status"] if k["aktif"] else "voided", k["status"]), not k["aktif"])
                     for k in kir]
    dipesan = await conn.fetchval(
        "SELECT COALESCE(SUM(quantity), 0) FROM sales_order_items WHERE sales_order_id = $1 "
        "AND sales_order_id IN (SELECT id FROM sales_orders WHERE tenant_id = $2)", sid, tid)
    terkirim = sum((_d(k["qty"]) for k in kir if k["aktif"]), NOL)

    # Penerimaan atas faktur SO: posted = alokasi aktif; voided = alokasinya (tetap 'active' di DB) ditandai batal.
    rcv = await conn.fetch(
        """SELECT rp.id, rp.payment_number, rp.payment_date, rp.payment_method, rp.status,
                  SUM(rpa.amount_applied) FILTER (WHERE si.sales_order_id = $2) AS jumlah,
                  array_remove(array_agg(DISTINCT so2.order_number) FILTER (WHERE si.sales_order_id <> $2), NULL) AS so_lain
           FROM receive_payments rp
           JOIN receive_payment_allocations rpa ON rpa.payment_id = rp.id AND rpa.tenant_id = $1 AND rpa.status = 'active'
           JOIN sales_invoices si ON si.id = rpa.invoice_id AND si.tenant_id = $1
           LEFT JOIN sales_orders so2 ON so2.id = si.sales_order_id AND so2.tenant_id = $1
           WHERE rp.tenant_id = $1 AND rp.status <> 'draft'
             AND rp.id IN (SELECT a.payment_id FROM receive_payment_allocations a
                           JOIN sales_invoices s ON s.id = a.invoice_id AND s.tenant_id = $1
                           WHERE a.tenant_id = $1 AND a.status = 'active' AND s.sales_order_id = $2)
           GROUP BY rp.id, rp.payment_number, rp.payment_date, rp.payment_method, rp.status
           ORDER BY rp.payment_date, rp.payment_number""", tid, sid)
    g["receipt"] = [_dok("receipt", r["id"], r["payment_number"], r["payment_date"],
                         " · ".join(x for x in (label_metode_layar(r["payment_method"]),
                                                r["so_lain"] and "juga " + ", ".join(sorted(r["so_lain"]))) if x),
                         r["jumlah"], r["status"], tg.status_id("rp", r["status"]), r["status"] in BATAL)
                    for r in rcv]

    g["credit_note"] = [_dok("credit_note", c["id"], c["credit_note_number"], c["credit_note_date"],
                             ALASAN_NK.get(c["reason"], c["reason"]), c["total_amount"], c["status"],
                             tg.status_id("cn", c["status"]), c["status"] in BATAL)
                        for c in await conn.fetch(
                            """SELECT cn.id, cn.credit_note_number, cn.credit_note_date, cn.total_amount, cn.status,
                                      cn.reason
                               FROM credit_notes cn
                               JOIN sales_invoices si ON si.id = cn.original_invoice_id AND si.tenant_id = cn.tenant_id
                               WHERE cn.tenant_id = $1 AND si.sales_order_id = $2 AND cn.status <> 'draft'
                               ORDER BY cn.credit_note_date, cn.credit_note_number""", tid, sid)]

    ringkas = {
        "quote": [{"label": "Nilai", "value": tg.rp(_jumlah(g["quote"]))}],
        "order": [{"label": "Total", "value": tg.rp(_d(so["total_amount"]))},
                  {"label": "Status", "value": tg.status_id("so", so["status"])}],
        "proforma": [{"label": "Ditagihkan", "value": tg.rp(_jumlah(g["proforma"]))},
                     {"label": "Terbayar", "value": tg.rp(bayar_pro)}],
        "deposit": [{"label": "Diterima", "value": tg.rp(_jumlah(g["deposit"]))},
                    {"label": "Sisa", "value": tg.rp(sisa_dp)}],
        "invoice": [{"label": "Total", "value": tg.rp(_jumlah(g["invoice"]))},
                    {"label": "Sisa tagihan", "value": tg.rp(sum(sisa_f.values(), NOL))}],
        "delivery": [{"label": "Terkirim", "value": f"{tg.qty(terkirim)}/{tg.qty(dipesan)}{sat}"}],
        "receipt": [{"label": "Diterima", "value": tg.rp(_jumlah(g["receipt"]))}],
        "credit_note": [{"label": "Total", "value": tg.rp(_jumlah(g["credit_note"]))}],
    }
    return {"so": {"id": str(sid), "number": so["order_number"], "order_code": so["order_code"],
                   "order_title": so["order_title"], "order_code_label": st["label"],
                   "order_title_label": st["title_label"]},
            "groups": [{"key": k, "label": LABEL_KELOMPOK[k], "summary": ringkas[k], "docs": g[k]}
                       for k in URUTAN if g[k]]}
