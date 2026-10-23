"""Pembayaran Keluar -- PENENTU (PAY-PREVIEW U1, 23 Okt 2026; pola po_rencana / uang_muka_vendor_rencana).

SATU definisi aturan + RENCANA jurnal untuk DUA jalur bayar yang hidup, dipakai tulis DAN pratinjau:
  * POST /bill-payments (routers/bill_payments)        -> rencana_bayar()          ; pratinjau POST /bill-payments/preview
  * POST /bills/{id}/payments (bills_service lama, BP-) -> rencana_bayar_tagihan()  ; pratinjau POST /bills/{id}/payments/preview
Tulis: dipanggil SESUDAH kunci (advisory + FOR UPDATE bills, kunci=True); blok pertama = galat yang SAMA PERSIS dengan perilaku lama
(HTTPException status+detail untuk /bill-payments; {"success": False, "message"} untuk jalur tagihan lama). Pratinjau: transaksi
READ ONLY tanpa kunci, SEMUA blok dikumpulkan (200), jurnal = rencana TANPA nomor (nomor lahir saat tulis -- pratinjau tak pernah
menyentuh sequence: putusan MASTER 23 Okt, celah nomor = cacat audit).
Baris jurnal = baris_jurnal_bayar / _baris_tagihan_lama: fungsi YANG SAMA dipakai penulis (create_bill_payment_journal, record_payment).
U2 (perilaku berbeda dua jalur: nomor BP/PAY, sisa V440 vs SQL lama + cadangan cache, tagihan draf) = tiket putusan pemilik; U1 TIDAK
mengubah perilaku tulis apa pun."""
import logging
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException

from .resolve_account import resolve_account_id_or_none
from .role_resolver import AccountRole, resolve_account_id_by_role

logger = logging.getLogger(__name__)

NOL = Decimal("0")
NOMOR_NANTI = "(nomor saat disimpan)"


class Blok:
    """Pengumpul blok. Tulis (pratinjau=False): blok pertama dilempar persis seperti kode lama. Pratinjau: dikumpulkan."""

    def __init__(self, pratinjau: bool):
        self.pratinjau = pratinjau
        self.daftar: list = []

    def galat(self, code: str, status: int, detail) -> None:
        if not self.pratinjau:
            raise HTTPException(status_code=status, detail=detail)
        self.daftar.append({"code": code, "message": detail if isinstance(detail, str) else str(detail), "status": status})

    async def coba(self, code: str, aw):
        """Jalankan pemeriksaan lama yang MELEMPAR HTTPException; pratinjau -> blok, tulis -> lempar ulang apa adanya."""
        try:
            return await aw if hasattr(aw, "__await__") else aw
        except HTTPException as e:
            if not self.pratinjau:
                raise
            self.daftar.append({"code": code, "message": e.detail if isinstance(e.detail, str) else str(e.detail), "status": e.status_code})
            return None

    def untuk_respons(self) -> list:
        return [{"code": b["code"], "message": b["message"]} for b in self.daftar]


def _uuid(v):
    return v if isinstance(v, UUID) else UUID(str(v))


def _f(v):
    return float(Decimal(str(v))) if v is not None else None


async def _uuid_sah(bl: Blok, nilai, nama: str) -> Optional[UUID]:
    """== routers/bill_payments.validate_uuid (pesan sama)."""
    try:
        return UUID(nilai)
    except (ValueError, AttributeError, TypeError):
        bl.galat("UUID_TIDAK_SAH", 400, f"{nama} harus berupa UUID yang valid")
        return None


async def periode_terbuka(conn, tid: str, tgl, bl: Blok) -> None:
    """== routers/bill_payments.check_period_is_open (Law 5; status CLOSED/LOCKED -> 403, pesan teks_galat)."""
    from . import teks_galat as tg
    p = await conn.fetchrow(
        """SELECT id, period_name, status FROM fiscal_periods
           WHERE tenant_id = $1 AND $2 BETWEEN start_date AND end_date
           ORDER BY start_date DESC LIMIT 1""", tid, tgl)
    if p and p["status"] in ("CLOSED", "LOCKED"):
        bl.galat("PERIODE_TERTUTUP", 403, tg.periode_tertutup(p["period_name"], p["status"]))


async def sisa_tagihan(conn, tid: str, bill_id) -> int:
    """== routers/bill_payments.get_bill_remaining_from_journal (compute_ap_rincian V440/V441; 0 = tidak aktif)."""
    from .tagihan_daftar import sisa_tagihan_jurnal
    v = await sisa_tagihan_jurnal(conn, tid, _uuid(bill_id))
    return int(v) if v is not None else 0


# ------------------------------------------------------------------ jurnal /bill-payments (SATU sumber baris)
async def baris_jurnal_bayar(conn, tid: str, *, payment_number: str, vendor_name: str, bank_account_id, total_amount,
                             allocated_amount, discount_amount=0, discount_account_id=None, bank_fee_amount=0,
                             bank_fee_account_id=None, source_type: str = "cash", source_deposit_id=None, unapplied_amount=0,
                             pph_tax_code_id=None, pph_amount=Decimal("0")) -> tuple:
    """-> ([(account_id, debit, credit, memo)], total_debit). Urutan, akun, nominal dan memo == create_bill_payment_journal lama:
    Dr Hutang (dialokasikan) · Dr Biaya bank · Dr Uang muka vendor (lebih bayar) · Cr Kas/Bank (total + biaya − PPh) atau Cr Uang muka
    (sumber deposit) · Cr Potongan pembelian · Cr Utang PPh. Biaya bank tanpa akun -> 400 (pesan lama). Baris dengan akun kosong DIBUANG
    seperti dulu -- penjaga Law 4 (baris == header) di penulis / blok JURNAL_TIDAK_SEIMBANG di pratinjau yang menangkapnya."""
    bank_coa_id = None
    if bank_account_id:
        bank = await conn.fetchrow("SELECT coa_id, account_name FROM bank_accounts WHERE id = $1 AND tenant_id = $2",
                                   _uuid(bank_account_id), tid)
        if bank:
            bank_coa_id = bank["coa_id"]
    ap_account_id = await resolve_account_id_by_role(conn, tid, AccountRole.AP_TRADE)
    purchase_discount_account_id = discount_account_id
    if discount_amount > 0 and not purchase_discount_account_id:
        purchase_discount_account_id = await resolve_account_id_by_role(conn, tid, AccountRole.PURCHASE_DISCOUNT)
    vendor_deposit_account_id = None
    if unapplied_amount > 0:
        vendor_deposit_account_id = await resolve_account_id_by_role(conn, tid, AccountRole.AP_PREPAID)
    pph_coa_id = None
    if pph_amount > 0 and pph_tax_code_id:
        pph_coa_id = await conn.fetchval("SELECT coa_id FROM tax_codes WHERE id = $1", _uuid(pph_tax_code_id))
        if not pph_coa_id:
            pph_coa_id = await resolve_account_id_by_role(conn, tid, AccountRole.WHT_PPH_PAYABLE)

    total_debit = total_amount + discount_amount + bank_fee_amount
    baris = []
    if allocated_amount > 0:
        baris.append((ap_account_id, allocated_amount, 0, f"Pelunasan hutang usaha - {vendor_name}"))
    if bank_fee_amount > 0 and not bank_fee_account_id:
        for _code in ("5-20850", "5-20800"):
            bank_fee_account_id = await resolve_account_id_or_none(conn, tid, _code)
            if bank_fee_account_id:
                logger.info(f"bank_fee_account_id absent from payload; resolved {_code} for tenant={tid}")
                break
        if not bank_fee_account_id:
            raise HTTPException(status_code=400, detail=(
                "Biaya bank diisi tetapi akun biaya bank tidak ditemukan "
                "(5-20850/5-20800). Tambahkan akun tersebut di Daftar Akun "
                "atau kosongkan biaya bank."))
    if bank_fee_amount > 0 and bank_fee_account_id:
        baris.append((_uuid(bank_fee_account_id), bank_fee_amount, 0, "Biaya administrasi bank"))
    if unapplied_amount > 0 and vendor_deposit_account_id:
        baris.append((vendor_deposit_account_id, unapplied_amount, 0, f"Uang muka vendor - {vendor_name}"))
    cash_out = total_amount + bank_fee_amount - pph_amount
    if cash_out > 0 and bank_coa_id:
        baris.append((bank_coa_id, 0, cash_out, f"Pembayaran dari bank - {payment_number}"))
    elif cash_out > 0 and source_type == "deposit" and source_deposit_id:
        deposit_coa = await resolve_account_id_by_role(conn, tid, AccountRole.AP_PREPAID)
        if deposit_coa:
            baris.append((deposit_coa, 0, cash_out, f"Aplikasi uang muka vendor - {payment_number}"))
    if discount_amount > 0 and purchase_discount_account_id:
        baris.append((_uuid(purchase_discount_account_id), 0, discount_amount, "Potongan pembelian"))
    if pph_amount > 0 and pph_coa_id:
        baris.append((pph_coa_id, 0, pph_amount, "PPh dipotong dari pembayaran vendor"))
    return baris, total_debit


async def cermin_bank(conn, tid: str, bank_account_id, total_amount, pph_amount) -> Optional[dict]:
    """Cermin bank_transactions /bill-payments (BankSync R1) == blok ARTIFACT 4 lama: kartu kredit -> +transfer 'charge', lainnya
    -> −transfer 'payment_made'; transfer = total − PPh. None bila rekening tak ada."""
    from ..routers.bill_payments import validate_uuid
    b = await conn.fetchrow("SELECT id, account_name, account_type FROM bank_accounts WHERE id = $1 AND tenant_id = $2",
                            validate_uuid(str(bank_account_id), "bank_account_id"), tid)
    if not b:
        return None
    transfer = total_amount - (pph_amount or 0)
    kk = b["account_type"] == "credit_card"
    return {"bank_account_id": b["id"], "bank_account": b["account_name"], "amount": transfer if kk else -transfer,
            "transaction_type": "charge" if kk else "payment_made"}


async def _tampil_baris(conn, tid: str, baris: list) -> list:
    out = []
    for akun, d, k, memo in baris:
        r = await conn.fetchrow("SELECT account_code, name FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2", akun, tid) if akun else None
        out.append({"account_code": r["account_code"] if r else None, "account_name": r["name"] if r else None,
                    "debit": _f(d), "credit": _f(k), "memo": memo})
    return out


async def _rencana_jurnal(conn, tid: str, bl: Blok, deskripsi: str, tgl, baris: list, total_debit) -> dict:
    sd = sum((Decimal(str(b[1])) for b in baris), NOL)
    sk = sum((Decimal(str(b[2])) for b in baris), NOL)
    if sd != sk or sd != Decimal(str(total_debit)):
        bl.galat("JURNAL_TIDAK_SEIMBANG", 500, f"Law 4: jurnal pembayaran tidak seimbang — baris D={sd} K={sk}, header={Decimal(str(total_debit))}.")
    return {"journal_number": None, "source_type": "BILL_PAYMENT", "description": deskripsi,
            "journal_date": tgl.isoformat() if isinstance(tgl, date) else tgl, "lines": await _tampil_baris(conn, tid, baris)}


# ------------------------------------------------------------------ POST /bill-payments
async def rencana_bayar(conn, ctx, payload, pratinjau: bool = False) -> dict:
    """Aturan POST /bill-payments, URUTAN == _op lama: periode (bukan draf) · UUID opsional · vendor · rekening · tagihan milik tenant &
    vendor sama · alokasi ≤ total + potongan · per alokasi (FOR UPDATE saat tulis) sisa jurnal ≥ dialokasikan. Pratinjau menambah rencana
    jurnal + cermin bank (bila posting). -> {blocks, vendor_name, bank_account_name, allocated_amount, unapplied_amount, alokasi[...],
    journals, bank_transactions, bills, before, after}."""
    from .pihak_helpers import normalisasi_pihak, pastikan_pihak_sama
    tid = ctx["tenant_id"]
    bl = Blok(pratinjau)
    if not payload.save_as_draft:
        await periode_terbuka(conn, tid, payload.payment_date, bl)
    for nama, nilai in (("discount_account_id", payload.discount_account_id), ("bank_fee_account_id", payload.bank_fee_account_id),
                        ("source_deposit_id", payload.source_deposit_id)):
        if nilai:
            await _uuid_sah(bl, nilai, nama)

    vendor_name = payload.vendor_name
    if not vendor_name:
        vid = await _uuid_sah(bl, payload.vendor_id, "vendor_id")
        v = await conn.fetchrow("SELECT name FROM vendors WHERE id = $1 AND tenant_id = $2", vid, tid) if vid else None
        if vid and not v:
            bl.galat("VENDOR_TIDAK_DITEMUKAN", 400, "Vendor not found")
        vendor_name = v["name"] if v else None
    bank_account_name = payload.bank_account_name
    if not bank_account_name:
        bid = await _uuid_sah(bl, payload.bank_account_id, "bank_account_id")
        b = await conn.fetchrow("SELECT account_name FROM bank_accounts WHERE id = $1 AND tenant_id = $2", bid, tid) if bid else None
        if bid and not b:
            bl.galat("REKENING_TIDAK_DITEMUKAN", 400, "Bank account not found")
        bank_account_name = b["account_name"] if b else None

    vendor_bayar = await bl.coba("VENDOR_TIDAK_SAH", _sinkron(normalisasi_pihak, payload.vendor_id, "Vendor pembayaran"))
    nomor_tagihan = {}
    for a in payload.allocations:
        t = await conn.fetchrow("SELECT invoice_number, vendor_id FROM bills WHERE id = $1::uuid AND tenant_id = $2", a.bill_id, tid)
        if not t:
            bl.galat("TAGIHAN_TIDAK_DITEMUKAN", 400, f"Bill {a.bill_id} not found")
            continue
        nomor_tagihan[str(a.bill_id)] = t["invoice_number"]
        if vendor_bayar is not None:
            await bl.coba("TAGIHAN_VENDOR_LAIN", _sinkron(pastikan_pihak_sama, vendor_bayar, t["vendor_id"],
                                                          f"Tagihan {t['invoice_number']}", "vendor"))

    allocated_amount = sum(a.amount_applied for a in payload.allocations)
    unapplied_amount = payload.total_amount + payload.discount_amount - allocated_amount
    if allocated_amount > payload.total_amount + payload.discount_amount:
        bl.galat("ALOKASI_MELEBIHI_PEMBAYARAN", 400, "Allocations exceed payment amount plus discount")

    alokasi = []
    for a in payload.allocations:
        t = await conn.fetchrow("SELECT id, amount as total_amount FROM bills WHERE id = $1::uuid AND tenant_id = $2"
                                + ("" if pratinjau else " FOR UPDATE"), a.bill_id, tid)
        if not t:
            if not pratinjau:
                bl.galat("TAGIHAN_TIDAK_DITEMUKAN", 400, f"Bill {a.bill_id} not found")
            continue
        sebelum = await sisa_tagihan(conn, tid, a.bill_id)
        if a.amount_applied > sebelum:
            bl.galat("MELEBIHI_SISA_TAGIHAN", 400, "Amount exceeds remaining")
        alokasi.append({"bill_id": a.bill_id, "remaining_before": sebelum, "amount_applied": a.amount_applied,
                        "remaining_after": sebelum - a.amount_applied})

    hasil = {"blocks": bl.daftar, "vendor_name": vendor_name, "bank_account_name": bank_account_name,
             "allocated_amount": allocated_amount, "unapplied_amount": unapplied_amount, "alokasi": alokasi}
    if not pratinjau:
        return hasil

    status = "draft" if payload.save_as_draft else "posted"
    jurnal, bank = [], []
    if status == "posted":
        try:
            baris, total_debit = await baris_jurnal_bayar(
                conn, tid, payment_number=NOMOR_NANTI, vendor_name=vendor_name, bank_account_id=payload.bank_account_id,
                total_amount=payload.total_amount, allocated_amount=allocated_amount, discount_amount=payload.discount_amount,
                discount_account_id=payload.discount_account_id, bank_fee_amount=payload.bank_fee_amount,
                bank_fee_account_id=payload.bank_fee_account_id, source_type=payload.source_type,
                source_deposit_id=payload.source_deposit_id, unapplied_amount=unapplied_amount,
                pph_tax_code_id=payload.pph_tax_code_id, pph_amount=payload.pph_amount or 0)
            jurnal = [await _rencana_jurnal(conn, tid, bl, f"Pembayaran ke {vendor_name} - {NOMOR_NANTI}", payload.payment_date,
                                            baris, total_debit)]
        except HTTPException as e:
            bl.galat("AKUN_JURNAL", e.status_code, e.detail)
        except (ValueError, TypeError) as e:
            bl.galat("AKUN_JURNAL", 400, str(e))
        if payload.bank_account_id:
            cb = await bl.coba("REKENING_TIDAK_SAH", cermin_bank(conn, tid, payload.bank_account_id, payload.total_amount,
                                                                 payload.pph_amount or 0))
            if cb:
                bank = [{"bank_account": cb["bank_account"], "amount": _f(cb["amount"]), "transaction_type": cb["transaction_type"]}]
    hasil.update({
        "journals": jurnal, "bank_transactions": bank,
        "bills": [{"bill_id": str(a["bill_id"]), "bill_number": nomor_tagihan.get(str(a["bill_id"])),
                   "remaining_before": _f(a["remaining_before"]), "amount_applied": _f(a["amount_applied"]),
                   "remaining_after": _f(a["remaining_after"])} for a in alokasi],
        "before": {"exists": False},
        "after": {"exists": True, "status": status, "payment_number": None, "total_amount": _f(payload.total_amount),
                  "allocated_amount": _f(allocated_amount), "unapplied_amount": _f(unapplied_amount),
                  "discount_amount": _f(payload.discount_amount), "bank_fee_amount": _f(payload.bank_fee_amount),
                  "pph_amount": _f(payload.pph_amount or 0)},
    })
    return hasil


async def _sinkron(fn, *a):
    return fn(*a)


# ------------------------------------------------------------------ POST /bills/{id}/payments (jalur lama, nomor BP-)
SQL_SISA_LAMA = """
    WITH bill_obligation AS (
        SELECT COALESCE(SUM(jl.credit), 0) AS total_credit
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.tenant_id = $1 AND je.status = 'POSTED'
          AND je.reversed_by_id IS NULL
          AND coa.account_type = 'PAYABLE' AND jl.credit > 0
          AND je.source_type = 'BILL' AND je.source_id = $2
    ),
    bill_payments_settled AS (
        SELECT COALESCE(SUM(jl.debit), 0) AS total_debit
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.tenant_id = $1 AND je.status = 'POSTED'
          AND je.reversed_by_id IS NULL
          AND coa.account_type = 'PAYABLE' AND jl.debit > 0
          AND (
              -- Via bill_payment_allocations
              EXISTS (
                  SELECT 1 FROM bill_payment_allocations bpa
                  JOIN bill_payments_v2 bp ON bp.id = bpa.payment_id
                  WHERE bpa.bill_id = $3 AND bp.journal_id = je.id
              )

              -- Via source_id match for PAYMENT_BILL or BILL_PAYMENT
              -- source_type fallback removed: covered by bill_payments + bill_payment_allocations
          )
    )
    SELECT GREATEST(0, bo.total_credit - bps.total_debit)
    FROM bill_obligation bo, bill_payments_settled bps
"""


def baris_tagihan_lama(ap_account_id, bank_coa_id, pph_coa_id, payment_amount, pph_amount, vendor_name: str, payment_number: str) -> list:
    """Baris jurnal jalur lama == record_payment: Dr Hutang (bayar) · Cr Kas/Bank (bayar − PPh) · Cr Utang PPh (bila ada akun)."""
    baris = [(ap_account_id, payment_amount, 0, f"Pelunasan hutang - {vendor_name}"),
             (bank_coa_id, 0, payment_amount - pph_amount, f"Pembayaran dari bank - {payment_number}")]
    if pph_amount > 0 and pph_coa_id:
        baris.append((pph_coa_id, 0, pph_amount, "PPh dipotong dari pembayaran vendor"))
    return baris


async def rencana_bayar_tagihan(conn, tid: str, bill_id: UUID, request: dict, pratinjau: bool = False) -> dict:
    """Aturan record_payment, URUTAN + PESAN == kode lama (tulis: blok pertama -> {"success": False, "message"} oleh pemanggil):
    tagihan ada (FOR UPDATE saat tulis) · bukan void · bukan DRAF (U2a 23 Okt: 422 TAGIHAN_DRAF) · sisa = SQL lama + cadangan cache
    bila tanpa jurnal (U2b) · nominal > 0 · nominal ≤ sisa · rekening aktif & bukan akun induk / akun CoA lama / salah satu wajib.
    -> {blocks, bill, remaining, payment_amount, payment_date, pph_*, bank_account, bank_coa_id, ap_account_id, pph_coa_id, ...}."""
    bloks = []

    def blok(code, pesan):
        bloks.append({"code": code, "message": pesan, "status": 400})

    bill = await conn.fetchrow(
        """
                    SELECT id, amount, amount_paid, status, status_v2, ap_id, vendor_id, vendor_name, invoice_number
                    FROM bills
                    WHERE id = $1 AND tenant_id = $2
                    """ + ("" if pratinjau else "FOR UPDATE"), bill_id, tid)
    out = {"blocks": bloks, "bill": bill}
    if not bill:
        blok("TAGIHAN_TIDAK_DITEMUKAN", "Bill not found")
        return out
    if bill["status"] in ("void",) or (bill.get("status_v2") or "") == "void":
        blok("TAGIHAN_DIBATALKAN", "Cannot pay voided bill")
        if not pratinjau:
            return out
    if bill["status_v2"] == "draft" or (bill["status_v2"] is None and bill["status"] == "draft"):
        # U2a (putusan pemilik LANGSUNG 23 Okt 2026; pola Xero/QBO): tagihan draf belum di buku -> tak bisa dibayar (dulu sisa jatuh ke
        # cache amount − amount_paid dan pembayaran tercatat atas hutang yang belum ada). 422 = permintaan sah, keadaan dokumen menolak.
        bloks.append({"code": "TAGIHAN_DRAF", "message": "Tagihan masih draf. Posting tagihan dulu sebelum dibayar.", "status": 422})
        if not pratinjau:
            return out

    remaining = int(await conn.fetchval(SQL_SISA_LAMA, tid, str(bill_id), bill_id) or 0)
    if remaining == 0 and bill["amount_paid"] == 0 and bill["amount"] > 0:
        remaining = int(bill["amount"] - bill["amount_paid"])
    payment_amount = Decimal(str(request["amount"]))
    if payment_amount <= 0:
        blok("NOMINAL_TIDAK_POSITIF", "Payment amount must be positive")
        if not pratinjau:
            return out
    if payment_amount > remaining:
        blok("MELEBIHI_SISA_TAGIHAN", f"Jumlah pembayaran ({payment_amount:,}) melebihi sisa tagihan ({remaining:,})")
        if not pratinjau:
            return out

    from ..utils.tanggal_tenant import tanggal_dokumen
    payment_date = request.get("payment_date") or await tanggal_dokumen(conn, tid)
    if isinstance(payment_date, str):
        payment_date = date.fromisoformat(payment_date)
    pph_tax_code_id = request.get("pph_tax_code_id")
    pph_amount = Decimal(str(request.get("pph_amount") or 0))

    bank_account_id = request.get("bank_account_id")
    coa_id = request.get("account_id")
    bank_coa_id = None
    bank_account = None
    if bank_account_id:
        if isinstance(bank_account_id, str):
            bank_account_id = UUID(bank_account_id)
        bank_account = await conn.fetchrow(
            """
                        SELECT ba.id, ba.coa_id, ba.account_type, ba.account_name,
                               coa.is_header, coa.name as coa_name
                        FROM bank_accounts ba
                        JOIN chart_of_accounts coa ON coa.id = ba.coa_id
                        WHERE ba.id = $1 AND ba.tenant_id = $2 AND ba.is_active = true
                        """, bank_account_id, tid)
        if not bank_account:
            blok("REKENING_TIDAK_DITEMUKAN", "Bank account not found or inactive")
            if not pratinjau:
                return out
        elif bank_account["is_header"]:
            blok("AKUN_INDUK", f"Akun '{bank_account['coa_name']}' adalah akun induk. Pilih akun anak (leaf).")
            if not pratinjau:
                return out
        else:
            bank_coa_id = bank_account["coa_id"]
    elif coa_id:
        if isinstance(coa_id, str):
            coa_id = UUID(coa_id)
        bank_coa_id = coa_id
    else:
        blok("REKENING_WAJIB", "bank_account_id atau account_id wajib diisi")
        if not pratinjau:
            return out

    ap_account_id = await resolve_account_id_by_role(conn, tid, AccountRole.AP_TRADE)
    pph_coa_id = None
    if pph_amount > 0 and pph_tax_code_id:
        pph_coa_id = await conn.fetchval("SELECT coa_id FROM tax_codes WHERE id = $1",
                                         UUID(pph_tax_code_id) if isinstance(pph_tax_code_id, str) else pph_tax_code_id)
        if not pph_coa_id:
            pph_coa_id = await resolve_account_id_by_role(conn, tid, AccountRole.WHT_PPH_PAYABLE)
    out.update({"remaining": remaining, "payment_amount": payment_amount, "payment_date": payment_date,
                "pph_tax_code_id": pph_tax_code_id, "pph_amount": pph_amount, "bank_account_id": bank_account_id,
                "bank_account": bank_account, "bank_coa_id": bank_coa_id, "ap_account_id": ap_account_id, "pph_coa_id": pph_coa_id})
    return out


async def pratinjau_bayar_tagihan(conn, tid: str, bill_id: UUID, request: dict) -> dict:
    """Pratinjau POST /bills/{id}/payments (mode bills_lama): rencana yang SAMA dengan tulis + jurnal tanpa nomor + cermin bank."""
    r = await rencana_bayar_tagihan(conn, tid, bill_id, request, pratinjau=True)
    bill = r["bill"]
    jurnal, bank, tag = [], [], []
    if bill and "payment_amount" in r:
        bl = Blok(True)
        bl.daftar = r["blocks"]
        amt, pph = r["payment_amount"], r["pph_amount"]
        if r["bank_coa_id"]:
            baris = baris_tagihan_lama(r["ap_account_id"], r["bank_coa_id"], r["pph_coa_id"], amt, pph, bill["vendor_name"], NOMOR_NANTI)
            jurnal = [await _rencana_jurnal(conn, tid, bl, f"Pembayaran ke {bill['vendor_name']}", r["payment_date"], baris, amt)]
        ba = r["bank_account"]
        if ba and not ba["is_header"]:
            transfer = amt - pph
            kk = ba["account_type"] == "credit_card"
            bank = [{"bank_account": ba["account_name"], "amount": _f(transfer if kk else -transfer),
                     "transaction_type": "charge" if kk else "payment_made"}]
        tag = [{"bill_id": str(bill["id"]), "bill_number": bill["invoice_number"], "remaining_before": _f(r["remaining"]),
                "amount_applied": _f(amt), "remaining_after": _f(r["remaining"] - amt)}]
    return {"blocks": r["blocks"], "journals": jurnal, "bank_transactions": bank, "bills": tag,
            "before": {"exists": False},
            "after": {"exists": True, "status": "posted", "payment_number": None,
                      "total_amount": _f(r.get("payment_amount")), "pph_amount": _f(r.get("pph_amount"))} if bill else None}
