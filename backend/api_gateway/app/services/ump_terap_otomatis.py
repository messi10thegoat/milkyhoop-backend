"""Terapkan OTOMATIS uang muka pembelian ber-PO saat posting FPB dari PO (UMP-PO U-B, 2 Nov 2026). Putusan LANGSUNG pemilik 2 Nov:
"Terapkan otomatis" -- simetris dengan Penjualan (sales_invoices._auto_apply_so_deposits :308-336 + customer_deposits.
plan_so_deposit_application :300). Cermin SI APA ADANYA (putusan MASTER):
- (a) GAGAL: satu penerapan galat -> SELURUH posting FPB batal (tanpa try; Law 23). Yang tak layak DILEWATI di perencana
  (planned 0 + skip_reason), bukan ditangkap saat terap.
- (b) TANGGAL penerapan = max(tanggal FPB, tanggal uang muka) -- TGL-UM, putusan LANGSUNG pemilik 2 Nov untuk SI + U-B sekaligus
  (services/tanggal_uang_muka); periode diperiksa inti terapkan.
- (c) vendor uang muka beda -> dilewati ("Vendor uang muka berbeda dengan vendor faktur."); tanpa medan mata uang (IDR).
Tautan PO tagihan = bills.purchase_order_id ATAU PO dari baris (bill_items.purchase_order_item_id) -- predikat SAMA dengan
tagihan_daftar (bundel PO). Uang muka: bertaut salah satu PO itu, status terbit (posted/partial), FIFO tanggal lalu nomor; sisa =
turunan JURNAL (UMV.sisa_jurnal, Law 1/16); jatah = min(sisa, sisa tagihan tersisa). Decimal (Law 9/25).
"""
from decimal import Decimal
from uuid import UUID

from . import tanggal_uang_muka as TUM
from . import uang_muka_vendor as UMV
from .uang_muka_vendor_rencana import STATUS_TERBIT

NOL = Decimal("0")
ALASAN_VENDOR = "Vendor uang muka berbeda dengan vendor faktur."
ALASAN_DILEWATI = "Dilewati pengguna."

SQL_PO_TAGIHAN = """
    SELECT b.purchase_order_id AS po_id FROM bills b WHERE b.id = $1 AND b.tenant_id = $2 AND b.purchase_order_id IS NOT NULL
    UNION
    SELECT i.po_id FROM bill_items bi JOIN purchase_order_items i ON i.id = bi.purchase_order_item_id WHERE bi.bill_id = $1
"""

SQL_UANG_MUKA_PO = """
    SELECT id, deposit_number, deposit_date, vendor_id FROM vendor_deposits
    WHERE tenant_id = $1 AND purchase_order_id = ANY($2::uuid[]) AND status = ANY($3::text[])
    ORDER BY deposit_date, deposit_number, id
"""


async def po_tagihan(conn, tenant_id: str, bill_id) -> list:
    return [r["po_id"] for r in await conn.fetch(SQL_PO_TAGIHAN, bill_id, tenant_id) if r["po_id"]]


async def rencana(conn, tenant_id: str, bill_id, outstanding, skip_ids=()) -> list:
    """[{deposit_id, deposit_number, deposit_date, remaining, planned_amount, skip_reason}] -- SATU turunan untuk pratinjau posting,
    GET /bills/{id}/deposit-plan, dan posting (cermin plan_so_deposit_application). Tanpa tautan PO = []."""
    skip = {str(s) for s in (skip_ids or ())}
    bill = await conn.fetchrow("SELECT id, vendor_id FROM bills WHERE id = $1 AND tenant_id = $2", bill_id, tenant_id)
    if not bill:
        return []
    pos = await po_tagihan(conn, tenant_id, bill_id)
    if not pos:
        return []
    sisa_tagihan = Decimal(str(outstanding or 0))
    out = []
    for d in await conn.fetch(SQL_UANG_MUKA_PO, tenant_id, pos, list(STATUS_TERBIT)):
        remaining = await UMV.sisa_jurnal(conn, tenant_id, d["id"])
        alasan = None
        if str(d["vendor_id"]) != str(bill["vendor_id"]):
            alasan = ALASAN_VENDOR
        elif str(d["id"]) in skip:
            alasan = ALASAN_DILEWATI
        jumlah = NOL if alasan or remaining <= 0 else max(NOL, min(remaining, sisa_tagihan))
        sisa_tagihan -= jumlah
        out.append({"deposit_id": str(d["id"]), "deposit_number": d["deposit_number"],
                    "deposit_date": d["deposit_date"].isoformat() if d["deposit_date"] else None,
                    "remaining": remaining, "planned_amount": jumlah, "skip_reason": alasan})
    return out


def urai_skip(nilai) -> list:
    """skip_deposit_ids: berulang ATAU dipisah koma (cermin GET /sales-invoices/{id}/deposit-plan). Dipanggil langsung (bukan HTTP) ->
    nilai bawaan objek Query, bukan list -> kosong."""
    mentah = nilai if isinstance(nilai, (list, tuple)) else []
    return [x.strip() for v in mentah for x in str(v).split(",") if x.strip()]


async def sisa_untuk_rencana(conn, tenant_id: str, bill_id):
    """Cermin SI deposit-plan: DRAF -> terhadap total tagihan (grand_total); terposting -> sisa dari JURNAL."""
    b = await conn.fetchrow("SELECT status_v2, grand_total FROM bills WHERE id = $1 AND tenant_id = $2", bill_id, tenant_id)
    if not b:
        return NOL
    if (b["status_v2"] or "draft") == "draft":
        return Decimal(str(b["grand_total"] or 0))
    from ..routers.vendor_deposits import get_bill_remaining_from_journal
    return Decimal(str(await get_bill_remaining_from_journal(conn, tenant_id, bill_id)))


async def baris_jurnal_rencana(conn, tenant_id: str, rencana_: list, bloks: list) -> list:
    """Baris jurnal DEPOSIT_APPLICATION yang AKAN dibuat per uang muka terencana (== terapkan_uang_muka_inti: Dr Hutang Usaha
    peran AP_TRADE / Cr Uang Muka Pembelian peran AP_PREPAID, Law 27). Peran belum dipetakan -> blok (posting akan menolak)."""
    from fastapi import HTTPException
    from .role_resolver import AccountRole
    from .tagihan_aksi import _bernama
    aktif = [p for p in rencana_ if p["planned_amount"] > 0]
    if not aktif:
        return []
    try:
        ap = await UMV.akun_peran(conn, tenant_id, AccountRole.AP_TRADE)
        um = await UMV.akun_peran(conn, tenant_id, AccountRole.AP_PREPAID)
    except HTTPException as e:
        d = e.detail if isinstance(e.detail, dict) else {"code": "PERAN_AKUN_BELUM_DIPETAKAN", "message": str(e.detail)}
        bloks.append({"code": d.get("code", "PERAN_AKUN_BELUM_DIPETAKAN"), "message": d.get("message", ""), "status": e.status_code})
        return []
    out = []
    for p in aktif:
        baris = await _bernama(conn, tenant_id, [
            {"account_id": ap, "debit": p["planned_amount"], "credit": NOL, "memo": "Terapkan uang muka ke tagihan"},
            {"account_id": um, "debit": NOL, "credit": p["planned_amount"], "memo": f"Terapkan uang muka {p['deposit_number']}"}])
        out.append({"deposit_id": p["deposit_id"], "deposit_number": p["deposit_number"], "source_type": "DEPOSIT_APPLICATION", "lines": baris})
    return out


def untuk_respons(rencana_: list) -> list:
    """Decimal -> float (Law 25: Decimal di dalam, float di respons)."""
    return [{**p, "remaining": float(p["remaining"]), "planned_amount": float(p["planned_amount"])} for p in rencana_]


async def terapkan_otomatis(conn, ctx: dict, bill_id, tanggal=None, skip_ids=()) -> list:
    """KAIT posting FPB: dipanggil SETIAP jalur yang memposting tagihan, di transaksi posting YANG SAMA (Law 23: posting + penerapan
    commit/rollback bersama). Tanpa tautan PO = no-op []. Penulis = routers.vendor_deposits.terapkan_uang_muka_inti (SATU penulis,
    sama dengan rute /apply). -> [{deposit_id, deposit_number, amount, application_id, journal_id}]."""
    from ..routers.vendor_deposits import terapkan_uang_muka_inti, get_bill_remaining_from_journal
    from ..schemas.vendor_deposits import ApplyDepositRequest
    tid = ctx["tenant_id"]
    if not await po_tagihan(conn, tid, bill_id):
        return []
    if tanggal is None:  # (b) tanggal faktur; per uang muka = max(tanggal faktur, tanggal uang muka) (TGL-UM, putusan pemilik 2 Nov)
        tanggal = await conn.fetchval("SELECT issue_date FROM bills WHERE id = $1 AND tenant_id = $2", bill_id, tid)
    outstanding = await get_bill_remaining_from_journal(conn, tid, bill_id)
    diterapkan = []
    for p in await rencana(conn, tid, bill_id, outstanding, skip_ids):
        if p["planned_amount"] <= 0:
            continue
        res = await terapkan_uang_muka_inti(conn, ctx, UUID(p["deposit_id"]),
                                            ApplyDepositRequest(bill_id=bill_id, amount=p["planned_amount"],
                                                                applied_date=TUM.tanggal_terap_otomatis(tanggal, p["deposit_date"])))
        diterapkan.append({"deposit_id": p["deposit_id"], "deposit_number": p["deposit_number"], "amount": p["planned_amount"],
                           "application_id": str(res.application_id), "journal_id": str(res.journal_id)})
    return diterapkan
