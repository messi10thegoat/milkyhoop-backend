"""LEPAS penerapan uang muka pembelian -- SATU penulis (22 Okt 2026). Dipakai rute POST /vendor-deposits/{id}/applications/{app}/unapply
DAN void faktur pembelian (bills_service.void_bill, BACKEND; putusan pemilik LANGSUNG 22 Okt: penerapan aktif dilepas OTOMATIS di transaksi
void, sejajar SI Unit 6). Berjalan di transaksi PEMANGGIL (tanpa acquire/transaction sendiri).

Urutan kunci GLOBAL (Law 13): baris TAGIHAN (FOR UPDATE) -> advisory VENDOR_DEPOSIT_SALDO:{deposit} -> vendor_deposits FOR UPDATE ->
penerapan FOR UPDATE. Sama dgn /apply (tagihan dulu) dan void faktur (BILL_VOID -> tagihan -> SALDO per deposit menaik) -> tanpa deadlock silang.
Jurnal pembalik DRAFT->POSTED (Law 20), baris ditukar: Dr Uang Muka Pembelian / Cr Utang Usaha; source_type DEPOSIT_APPLICATION, source_id =
TAGIHAN (opsi B; guard_arap_requires_obligation); jurnal asli tetap POSTED + reversed_by_id (Law 2/26); penerapan -> status 'reversed' (V437);
segarkan cache tagihan; audit VD_UNAPPLIED (+ alasan bila ada). Penentu blok = uang_muka_vendor_rencana.rencana_lepas (SAMA dgn pratinjau).
Idempoten ALAMI: penerapan sudah dilepas -> jawaban "sudah dilepas" + jurnal pembalik yang sama, nol tulisan (Law 14).
Blok -> HTTPException (pemanggil void faktur: seluruh void batal, mis. periode tertutup)."""
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException

from . import po_daftar as PD
from . import uang_muka_vendor as UMV
from . import uang_muka_vendor_rencana as R
from .pihak_helpers import segarkan_cache_hutang_tagihan
from .so_riwayat import catat_riwayat


def _f(v) -> float:
    return float(PD.r2(v or 0))


def _tolak(bloks: list) -> None:
    if not bloks:
        return
    b = bloks[0]
    if b["status"] == 400:
        raise HTTPException(status_code=400, detail=b["message"])
    raise HTTPException(status_code=b["status"], detail={"code": b["code"], "message": b["message"]})


def jawab_lepas(vd, a, rev_id, rev_no, sisa_um, sisa_tagihan, sudah: bool) -> dict:
    return {
        "success": True,
        "message": "Penerapan uang muka sudah dilepas sebelumnya." if sudah else "Penerapan uang muka dilepas.",
        "data": {
            "deposit_id": str(vd["id"]), "deposit_number": vd["deposit_number"], "application_id": str(a["id"]),
            "bill_id": str(a["bill_id"]), "bill_number": a["bill_number"], "amount": _f(a["amount"]),
            "reversal_journal_id": str(rev_id) if rev_id else None, "reversal_journal_number": rev_no,
            "deposit_remaining": _f(sisa_um), "bill_remaining": _f(sisa_tagihan), "status": "reversed",
        },
    }


async def lepas_penerapan(conn, ctx: dict, deposit_id, application_id, alasan: Optional[str] = None,
                          sumber: str = "api:vendor_deposits.unapply") -> dict:
    from ..utils.tanggal_tenant import tanggal_dokumen
    tid = ctx["tenant_id"]
    pra = await conn.fetchrow(
        """SELECT a.bill_id FROM vendor_deposit_applications a JOIN vendor_deposits d ON d.id = a.vendor_deposit_id
           WHERE a.id = $1 AND a.vendor_deposit_id = $2 AND d.tenant_id = $3""", application_id, deposit_id, tid)
    if pra and pra["bill_id"] is not None:
        await conn.fetchrow("SELECT id FROM bills WHERE id = $1 AND tenant_id = $2 FOR UPDATE", pra["bill_id"], tid)
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"VENDOR_DEPOSIT_SALDO:{deposit_id}")
    vd = await conn.fetchrow("SELECT id, deposit_number, status FROM vendor_deposits WHERE id = $1 AND tenant_id = $2 FOR UPDATE", deposit_id, tid)
    if not vd:
        raise HTTPException(status_code=404, detail="Uang muka vendor tidak ditemukan.")
    a = await conn.fetchrow(
        """SELECT a.id, a.bill_id, a.amount, a.journal_id, a.status, a.reversed_by_id, b.invoice_number AS bill_number
           FROM vendor_deposit_applications a LEFT JOIN bills b ON b.id = a.bill_id AND b.tenant_id = $3
           WHERE a.id = $1 AND a.vendor_deposit_id = $2 FOR UPDATE OF a""", application_id, deposit_id, tid)
    if not a:
        raise HTTPException(status_code=404, detail="Penerapan uang muka tidak ditemukan.")
    if a["status"] == "reversed":
        rev_no = await conn.fetchval("SELECT journal_number FROM journal_entries WHERE id = $1 AND tenant_id = $2", a["reversed_by_id"], tid)
        return jawab_lepas(vd, a, a["reversed_by_id"], rev_no, await UMV.sisa_jurnal(conn, tid, deposit_id),
                           await R.sisa_tagihan(conn, tid, a["bill_id"]), sudah=True)
    hari_ini = await tanggal_dokumen(conn, tid)  # t10-tanggal-bisnis: tanggal jurnal pembalik
    rl = await R.rencana_lepas(conn, ctx, vd, application_id, hari_ini, kunci=True)  # = /unapply/preview
    _tolak(rl["blocks"])
    asli, baris = rl["jurnal_asli"], rl["baris_asli"]
    total_d = sum((Decimal(str(b["debit"] or 0)) for b in baris), Decimal("0"))
    total_k = sum((Decimal(str(b["credit"] or 0)) for b in baris), Decimal("0"))
    if not baris or total_d != total_k or total_d != Decimal(str(a["amount"])):  # Law 4: baris == kepala == penerapan
        raise HTTPException(status_code=500, detail="Jurnal penerapan tidak seimbang dengan nilai penerapan; lepas dibatalkan.")
    rev_no = await conn.fetchval("SELECT get_next_journal_number($1, $2, $3)", tid, "JV", hari_ini)
    rev_id = await conn.fetchval(
        """INSERT INTO journal_entries (tenant_id, journal_number, journal_date, description, source_type, source_id,
                                        reversal_of_id, total_debit, total_credit, status, created_by)
           VALUES ($1, $2, $3, $4, 'DEPOSIT_APPLICATION', $5, $6, $7, $7, 'DRAFT', $8) RETURNING id""",
        tid, rev_no, hari_ini, f"Lepas penerapan {vd['deposit_number']} dari tagihan {a['bill_number'] or ''}".strip(),
        a["bill_id"], asli["id"], total_d, ctx.get("user_id"))
    for i, b in enumerate(baris, 1):
        await conn.execute(
            "INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo) VALUES ($1, $2, $3, $4, $5, $6)",
            rev_id, i, b["account_id"], b["credit"], b["debit"], f"Lepas - {b['memo'] or ''}")
    await conn.execute("UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", rev_id)
    ditandai = await conn.execute("UPDATE journal_entries SET reversed_by_id = $1 WHERE id = $2 AND reversed_by_id IS NULL", rev_id, asli["id"])
    if ditandai != "UPDATE 1":  # Law 26: satu pembalik per jurnal
        raise HTTPException(status_code=409, detail={"code": "JURNAL_PENERAPAN_SUDAH_DIBALIK",
                                                     "message": "Jurnal penerapan ini sudah dibalik oleh proses lain."})
    await conn.execute(
        """UPDATE vendor_deposit_applications SET status = 'reversed', reversed_by_id = $2, reversed_at = NOW(), reversed_by = $3
           WHERE id = $1 AND status = 'active'""", application_id, rev_id, ctx.get("user_id"))
    if a["bill_id"] is not None:
        await segarkan_cache_hutang_tagihan(conn, tid, a["bill_id"])
    meta = {"application_id": str(application_id), "bill_id": str(a["bill_id"]), "amount": f"{PD.r2(a['amount']):.2f}",
            "reversal_journal_id": str(rev_id)}
    if alasan:
        meta["reason"] = alasan
    await catat_riwayat(conn, tid, "vendor_deposit", deposit_id, vd["deposit_number"], "VD_UNAPPLIED", ctx.get("user_id"),
                        f"Penerapan uang muka {vd['deposit_number']} ke tagihan {a['bill_number'] or '-'} dilepas" + (f": {alasan}" if alasan else ""),
                        meta=meta, source=sumber)
    return jawab_lepas(vd, a, rev_id, rev_no, await UMV.sisa_jurnal(conn, tid, deposit_id), await R.sisa_tagihan(conn, tid, a["bill_id"]), sudah=False)
