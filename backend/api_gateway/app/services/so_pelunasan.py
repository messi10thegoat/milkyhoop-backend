"""Pratinjau "Terima pelunasan" dari Pesanan Penjualan (28 Sep 2026, MASTER/WORKSPACE, halaman CW).

POST /api/sales-orders/{id}/receive-payment/preview. NOL tulisan: seluruhnya di transaksi yang SELALU
di-ROLLBACK. Dua lapis:
  1. RENCANA (di sini): faktur TERBIT milik SO, sisa per faktur dari compute_ar_outstanding (Law 1/16/29,
     journal-derived; SAMA dengan yang dipakai validator create), alokasi TERTUA-DULU (tanggal faktur, nomor),
     rekening bawaan, tanggal bawaan. SEMUA penghalang rencana dikumpulkan (tidak berhenti di yang pertama).
  2. TULIS-SUNGGUHAN-LALU-ROLLBACK: bila rencana bersih, `payload` (badan PERSIS untuk POST /api/receive-payments)
     dijalankan lewat receive_payments.buat_penerimaan -- inti yang SAMA dengan rute create -- di savepoint,
     lalu hasilnya dibaca (sisa sesudah dari compute_ar_outstanding, nomor, metode, uang muka kelebihan).
     Penolakan inti -> blok RP_REJECTED berisi pesan inti apa adanya (inti berhenti di galat pertama).
Tulis = FE mengirim `payload` ke POST /api/receive-payments (+ X-Idempotency-Key); tak ada jalur tulis baru.

ATURAN yang diikuti (bukan dibuat di sini):
  * Kelebihan bayar (amount > total sisa) = BUKAN penghalang: jalur create membukukan sisanya ke Uang Muka
    Pelanggan (Cr CUSTOMER_DEPOSIT_LIABILITY) dan membuat uang muka OVP otomatis (tak tertaut SO) -> `notes`.
  * Uang muka milik SO yang belum terpakai TIDAK dipotong di sini (penerimaan kas tak menerapkan uang muka;
    penerapan = alur uang muka) -> `deposits_unapplied` + catatan info.
"""
from datetime import date
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field

_NOL = Decimal("0")


class SOReceivePaymentPreviewRequest(BaseModel):
    payment_date: Optional[date] = None           # kosong -> tanggal bisnis tenant
    bank_account_id: Optional[str] = None         # bank_accounts.id ATAU CoA id; kosong -> bawaan server
    amount: Optional[Decimal] = None              # kosong -> lunasi seluruh sisa faktur terpilih
    invoice_ids: Optional[List[str]] = None       # kosong -> semua faktur TERBIT milik SO
    reference_number: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None


def _f(v):
    return float(v) if v is not None else None


def _rp(x) -> str:
    s = f"{float(x):,.2f}".replace(",", "#").replace(".", ",").replace("#", ".")
    return "Rp" + s.removesuffix(",00")


def alokasi_tertua_dulu(faktur: list, jumlah: Decimal) -> list:
    """faktur: [{invoice_id, remaining}] SUDAH urut tertua-dulu. Kembalikan applied per faktur (Decimal)."""
    sisa, keluar = jumlah, []
    for f in faktur:
        a = min(f["remaining"], sisa) if sisa > 0 and f["remaining"] > 0 else _NOL
        keluar.append(a)
        sisa -= a
    return keluar


async def _rekening(conn, tenant_id: str, diminta: Optional[str], so) -> tuple:
    """(rekening|None, sumber, blok|None). Diminta: bank_accounts.id atau CoA id (inti menerima keduanya).
    Bawaan: rekening yang TERTULIS di SO (nomor rekening sama) -> is_default tunggal -> satu-satunya aktif."""
    kol = """SELECT ba.id, ba.coa_id, ba.account_name, ba.bank_name, ba.account_number, ba.account_type, ba.is_active
             FROM bank_accounts ba WHERE ba.tenant_id = $1"""
    if diminta:
        try:
            u = UUID(str(diminta))
        except (ValueError, TypeError):
            return None, "body", {"code": "RP_ACCOUNT_INVALID", "message": "Rekening penerimaan tidak dikenal."}
        r = await conn.fetchrow(kol + " AND (ba.id = $2 OR ba.coa_id = $2) ORDER BY (ba.id = $2) DESC LIMIT 1", tenant_id, u)
        if r:
            if not r["is_active"]:
                return None, "body", {"code": "RP_ACCOUNT_INACTIVE", "message": f"Rekening {r['account_name']} tidak aktif."}
            return {"id": str(r["id"]), "label": r["account_name"], "type": r["account_type"]}, "body", None
        coa = await conn.fetchrow(
            "SELECT id, name, account_type FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2", u, tenant_id)
        if coa:  # akun Kas tanpa baris bank_accounts: inti yang memutuskan (wajib ASSET)
            return {"id": str(coa["id"]), "label": coa["name"], "type": "coa"}, "body", None
        return None, "body", {"code": "RP_ACCOUNT_INVALID", "message": "Rekening penerimaan tidak dikenal."}
    aktif = await conn.fetch(kol + " AND ba.is_active ORDER BY ba.account_name", tenant_id)
    no_so = "".join(ch for ch in str(so.get("payment_account_number") or "") if ch.isdigit())
    if no_so:
        cocok = [r for r in aktif if "".join(ch for ch in str(r["account_number"] or "") if ch.isdigit()) == no_so]
        if len(cocok) == 1:
            r = cocok[0]
            return {"id": str(r["id"]), "label": r["account_name"], "type": r["account_type"]}, "so", None
    for sumber, calon in (("default", [r for r in aktif if r.get("is_default")]), ("tunggal", aktif)):
        if len(calon) == 1:
            r = calon[0]
            return {"id": str(r["id"]), "label": r["account_name"], "type": r["account_type"]}, sumber, None
    return None, None, {"code": "RP_ACCOUNT_REQUIRED", "message": "Pilih rekening penerimaan."}


async def rencana_pelunasan_so(conn, ctx: dict, so_id: UUID, body: SOReceivePaymentPreviewRequest) -> dict:
    """Lapis 1: rencana + SEMUA penghalang rencana. Nol tulisan."""
    tid = ctx["tenant_id"]
    so = await conn.fetchrow(
        """SELECT id, order_number, status, customer_id, customer_name, payment_account_number
           FROM sales_orders WHERE id = $1 AND tenant_id = $2""",
        so_id, tid,
    )
    if not so:
        raise HTTPException(status_code=404, detail="Sales order not found")
    so = dict(so)
    blocks, notes = [], []

    semua = await conn.fetch(
        """SELECT id, invoice_number, invoice_date, due_date, status FROM sales_invoices
           WHERE tenant_id = $1 AND sales_order_id = $2 AND status <> 'void'
           ORDER BY invoice_date, invoice_number, id""",
        tid, so_id,
    )
    draf = [r["invoice_number"] for r in semua if r["status"] == "draft"]
    terbit = [r for r in semua if r["status"] != "draft"]
    if draf:
        notes.append({"code": "RP_DRAFT_INVOICES",
                      "message": f"Faktur draf {', '.join(draf)} belum terbit — tidak ikut dilunasi."})
    if body.invoice_ids:
        ada = {str(r["id"]) for r in terbit}
        luar = [i for i in body.invoice_ids if str(i).lower() not in ada]
        if luar:
            blocks.append({"code": "RP_INVOICE_NOT_IN_SO",
                           "message": f"{len(luar)} faktur yang dipilih bukan faktur terbit pesanan {so['order_number']}.",
                           "invoice_ids": luar})
        pilih = {str(i).lower() for i in body.invoice_ids}
        terbit = [r for r in terbit if str(r["id"]) in pilih]

    sisa = {}
    if terbit:
        for r in await conn.fetch(
            """SELECT invoice_id, SUM(outstanding) AS outstanding FROM compute_ar_outstanding($1)
               WHERE invoice_id = ANY($2::uuid[]) GROUP BY invoice_id""",
            tid, [r["id"] for r in terbit],
        ):
            sisa[str(r["invoice_id"])] = Decimal(str(r["outstanding"] or 0))
    faktur = [{"invoice_id": r["id"], "invoice_number": r["invoice_number"], "invoice_date": r["invoice_date"],
               "due_date": r["due_date"], "status": r["status"],
               "remaining": max(sisa.get(str(r["id"]), _NOL), _NOL)} for r in terbit]
    total_sisa = sum((f["remaining"] for f in faktur), _NOL)
    if not faktur and not body.invoice_ids:
        blocks.append({"code": "RP_NO_OPEN_INVOICES",
                       "message": f"Pesanan {so['order_number']} belum punya faktur terbit untuk dilunasi."})
    elif faktur and total_sisa <= 0:
        blocks.append({"code": "RP_NOTHING_DUE", "message": "Semua faktur pesanan ini sudah lunas."})

    jumlah = body.amount if body.amount is not None else total_sisa
    if body.amount is not None and body.amount <= 0:
        blocks.append({"code": "RP_AMOUNT_INVALID", "message": "Jumlah diterima harus lebih dari 0."})
    applied = alokasi_tertua_dulu(faktur, jumlah if jumlah > 0 else _NOL)
    for f, a in zip(faktur, applied):
        f["applied"] = a
        f["remaining_after"] = f["remaining"] - a
    total_applied = sum(applied, _NOL)
    kelebihan = max(jumlah - total_applied, _NOL) if total_sisa > 0 else _NOL
    if kelebihan > 0:
        notes.append({"code": "RP_OVERPAYMENT_DEPOSIT",
                      "message": f"Kelebihan {_rp(kelebihan)} dicatat sebagai uang muka pelanggan (bisa dipakai "
                                 "untuk faktur berikutnya atau dikembalikan)."})

    if not so["customer_id"]:
        blocks.append({"code": "RP_CUSTOMER_MISSING", "message": "Pesanan tidak tertaut ke pelanggan."})

    rekening, sumber_rek, blok_rek = await _rekening(conn, tid, body.bank_account_id, so)
    if blok_rek:
        blocks.append(blok_rek)

    from ..utils.tanggal_tenant import tanggal_dokumen
    from ..routers.receive_payments import check_period_is_open
    tgl = body.payment_date or await tanggal_dokumen(conn, tid)
    try:
        await check_period_is_open(conn, tid, tgl)
    except HTTPException as e:
        blocks.append({"code": "RP_PERIOD_CLOSED", "message": str(e.detail)})

    from ..routers.customer_deposits import compute_deposit_remaining, linked_so_deposits
    dp = []
    for d in await linked_so_deposits(conn, tid, so["id"]):
        rem = await compute_deposit_remaining(conn, tid, d["id"])
        if rem > 0:
            dp.append({"deposit_id": str(d["id"]), "deposit_number": d["deposit_number"], "remaining": rem})
    if dp:
        notes.append({"code": "RP_DEPOSIT_AVAILABLE",
                      "message": "Uang muka " + ", ".join(f"{x['deposit_number']} (sisa {_rp(x['remaining'])})" for x in dp)
                                 + " belum dipotong dari faktur — penerimaan ini tidak memakainya."})

    payload = None
    if not blocks:
        payload = {
            "customer_id": str(so["customer_id"]),
            "payment_date": tgl.isoformat(),
            "bank_account_id": rekening["id"],
            "total_amount": str(jumlah),
            "allocations": [{"invoice_id": str(f["invoice_id"]), "amount_applied": str(f["applied"])}
                            for f in faktur if f["applied"] > 0],
            "reference_number": body.reference_number,
            "notes": body.notes,
            "save_as_draft": False,
        }
    return {"so": so, "blocks": blocks, "notes": notes, "invoices": faktur, "total_remaining": total_sisa,
            "total_applied": total_applied, "amount": jumlah, "overpayment": kelebihan, "bank_account": rekening,
            "bank_account_source": sumber_rek, "payment_date": tgl, "deposits_unapplied": dp, "payload": payload}


async def pratinjau_pelunasan_so(conn, ctx: dict, so_id: UUID, body: SOReceivePaymentPreviewRequest) -> dict:
    """Lapis 1 + lapis 2 di transaksi PEMANGGIL (pemanggil WAJIB me-ROLLBACK)."""
    from ..routers.receive_payments import buat_penerimaan
    from ..schemas.receive_payments import CreateReceivePaymentRequest

    r = await rencana_pelunasan_so(conn, ctx, so_id, body)
    tid = ctx["tenant_id"]
    hasil = None
    if r["payload"] is not None:
        # lock domain yang SAMA dengan rute create (serialkan dengan penerimaan nyata pelanggan ini)
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))",
                           f"RECEIVE_PAYMENT_CREATE:{tid}:{r['payload']['customer_id']}")
        try:
            async with conn.transaction():  # savepoint: galat SQL inti tak meracuni transaksi luar
                hasil = await buat_penerimaan(conn, ctx, CreateReceivePaymentRequest(**r["payload"]))
                rp = await conn.fetchrow(
                    """SELECT rp.payment_method, rp.created_deposit_id, cd.deposit_number
                       FROM receive_payments rp LEFT JOIN customer_deposits cd ON cd.id = rp.created_deposit_id
                       WHERE rp.id = $1 AND rp.tenant_id = $2""",
                    UUID(hasil["data"]["id"]), tid,
                )
                sesudah = {}
                ids = [f["invoice_id"] for f in r["invoices"]]
                if ids:
                    for x in await conn.fetch(
                        """SELECT invoice_id, SUM(outstanding) AS outstanding FROM compute_ar_outstanding($1)
                           WHERE invoice_id = ANY($2::uuid[]) GROUP BY invoice_id""", tid, ids,
                    ):
                        sesudah[str(x["invoice_id"])] = Decimal(str(x["outstanding"] or 0))
                for f in r["invoices"]:
                    f["remaining_after"] = sesudah.get(str(f["invoice_id"]), _NOL)  # tak ada baris = lunas
                hasil = {**hasil["data"], "payment_method": rp["payment_method"],
                         "overpayment_deposit_number": rp["deposit_number"]}
        except HTTPException as e:
            d = e.detail
            r["blocks"].append({"code": "RP_REJECTED",
                                "message": d if isinstance(d, str) else (d.get("message") if isinstance(d, dict) else str(d))})
            r["payload"] = None
            hasil = None
    so = r["so"]
    return {
        "order_number": so["order_number"],
        "can_save": not r["blocks"],
        "blocks": r["blocks"],
        "notes": r["notes"],
        "invoices": [{"invoice_id": str(f["invoice_id"]), "invoice_number": f["invoice_number"],
                      "invoice_date": f["invoice_date"].isoformat() if f["invoice_date"] else None,
                      "due_date": f["due_date"].isoformat() if f["due_date"] else None,
                      "status": f["status"], "remaining": _f(f["remaining"]), "applied": _f(f["applied"]),
                      "remaining_after": _f(f["remaining_after"])} for f in r["invoices"]],
        "total_remaining": _f(r["total_remaining"]),
        "total_applied": _f(r["total_applied"]),
        "total_remaining_after": _f(sum((f["remaining_after"] for f in r["invoices"]), _NOL)),
        "amount": _f(r["amount"]),
        "overpayment": _f(r["overpayment"]),
        "overpayment_deposit_number": hasil["overpayment_deposit_number"] if hasil else None,
        "bank_account": ({**r["bank_account"], "source": r["bank_account_source"]} if r["bank_account"] else None),
        "payment_method": hasil["payment_method"] if hasil else None,
        "payment_date": r["payment_date"].isoformat(),
        "payment_number_preview": hasil["payment_number"] if hasil else None,
        "deposits_unapplied": [{"deposit_id": x["deposit_id"], "deposit_number": x["deposit_number"],
                                "remaining": _f(x["remaining"])} for x in r["deposits_unapplied"]],
        "payload": r["payload"],
    }
