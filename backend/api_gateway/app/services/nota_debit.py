"""NOTA DEBIT (vendor credits) unit (3a) -- kebenaran uang (22 Okt 2026, putusan pemilik LANGSUNG; kontrak KONTRAK-nota-debit-u0-20261022.md).

  terapkan  = ATRIBUSI tanpa jurnal, SELURUH nilai ke SATU tagihan (kembar credit_notes.apply_nota_kredit_core; pola NetSuite apply vendor
              credit): aplikasi aktif ditulis DULU, lalu original_bill_id (pagar DB V443 trg_vendor_credit_kaitan_tagihan_beku).
              Hutang sudah didebit saat nota debit dibukukan; compute_ap_outstanding cabang 4 (V441) mengatribusikannya ke tagihan.
  lepas     = aplikasi -> 'reversed' (riwayat append-only, V443) + original_bill_id -> NULL di transaksi yang SAMA. Tanpa jurnal.
  sisa_nd   = nota debit BEBAS terbit: Σ debit AP − Σ kredit AP jurnal efektif VENDOR_CREDIT/VENDOR_CREDIT_REFUND bersumber nota debit ini
              (predikat SAMA dgn cabang 5 vc_unapplied V441); draf/void/bertaut = 0. Pagar refund membaca ini, bukan cache.
  sisa tagihan = compute_ap_rincian (V440/V441) -- satu sumber; None = tagihan tidak aktif (draf/void/jurnal dibalik).
  void      = cermin jurnal VENDOR_CREDIT (router) + cermin SETIAP jurnal VENDOR_CREDIT_COGS + pembalikan kartu stok per jurnal HPP
              (record_inventory_reversal, journal_id) + hapus document_tax_lines (pola void tagihan/faktur) -- Law 31 gerbang 5.
"""
import uuid as uuid_module
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException

from . import teks_galat as tg
from .tagihan_daftar import sisa_tagihan_jurnal  # satu sumber (compute_ap_rincian); dipakai di modul ini + router

SUMBER_ND = ("VENDOR_CREDIT", "VENDOR_CREDIT_REFUND")


def sql_sisa_nd(a: str = "vendor_credits") -> str:
    """Ungkapan SQL sisa jurnal satu nota debit ($1 = tenant)."""
    return (f"(CASE WHEN {a}.status IN ('draft', 'void') OR {a}.original_bill_id IS NOT NULL THEN 0 ELSE ("
            "SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) FROM journal_entries je "
            "JOIN journal_lines jl ON jl.journal_id = je.id "
            "JOIN chart_of_accounts coa ON coa.id = jl.account_id AND coa.account_type = 'PAYABLE' "
            f"WHERE je.tenant_id = $1 AND je.source_id = {a}.id AND je.source_type IN ('VENDOR_CREDIT', 'VENDOR_CREDIT_REFUND') "
            "AND je.status = 'POSTED' AND je.reversed_by_id IS NULL AND je.reversal_of_id IS NULL) END)")


async def sisa_nd(conn, tenant_id: str, vc_id) -> Decimal:
    v = await conn.fetchval(f"SELECT {sql_sisa_nd()} FROM vendor_credits WHERE vendor_credits.tenant_id = $1 AND vendor_credits.id = $2",
                            tenant_id, vc_id)
    return Decimal(str(v or 0))


async def tagihan_milik(conn, tenant_id: str, bill_id, vendor_id, kunci: bool = False):
    """Tagihan milik tenant ini (dan vendor yang sama bila vendor_id diisi). 400 berpesan bila tidak."""
    try:
        bid = bill_id if isinstance(bill_id, uuid_module.UUID) else uuid_module.UUID(str(bill_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="ID tagihan tidak valid.")
    b = await conn.fetchrow(
        "SELECT id, invoice_number, vendor_id, status, status_v2 FROM bills WHERE id = $1 AND tenant_id = $2" + (" FOR UPDATE" if kunci else ""),
        bid, tenant_id)
    if not b:
        raise HTTPException(status_code=400, detail="Tagihan tidak ditemukan.")
    if vendor_id and b["vendor_id"] and b["vendor_id"] != vendor_id:
        raise HTTPException(status_code=400, detail=f"Tagihan {b['invoice_number']} milik vendor lain.")
    return b


async def terapkan_nota_debit(conn, ctx: dict, vc_id, bill_id, jumlah, application_date) -> dict:
    """Inti POST /vendor-credits/{id}/apply, di transaksi PEMANGGIL."""
    from .pihak_helpers import segarkan_cache_hutang_tagihan
    tid = ctx["tenant_id"]
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"VENDOR_CREDIT_APPLY:{vc_id}")
    vc = await conn.fetchrow("SELECT * FROM vendor_credits WHERE id = $1 AND tenant_id = $2 FOR UPDATE", vc_id, tid)
    if not vc:
        raise HTTPException(status_code=404, detail="Nota debit tidak ditemukan.")
    # urutan = kembar NK (tolak_nk_bertaut DULU): sesudah diterapkan status jadi 'applied' -> cek status lebih dulu salah memberi 400
    if vc["original_bill_id"] is not None:
        raise HTTPException(status_code=409, detail={"code": "ND_SUDAH_BERTAUT",
                                                     "message": f"Nota debit ini sudah terkait ke tagihan {vc['original_bill_number'] or ''}".strip() + "."})
    if vc["status"] not in ("posted", "partial") or vc["journal_id"] is None:
        raise HTTPException(status_code=400, detail="Hanya nota debit yang sudah dibukukan yang bisa diterapkan ke tagihan.")
    if (vc["amount_received"] or 0) > 0:
        raise HTTPException(status_code=400, detail="Nota debit yang dananya sudah dikembalikan vendor tidak bisa diterapkan ke tagihan.")
    total = Decimal(str(vc["total_amount"]))
    if Decimal(str(jumlah)) != total:
        raise HTTPException(status_code=400, detail=f"Nota debit harus diterapkan seluruhnya ({tg.rp(total)}) ke satu tagihan.")
    bill = await tagihan_milik(conn, tid, bill_id, vc["vendor_id"], kunci=True)
    sisa = await sisa_tagihan_jurnal(conn, tid, bill["id"])
    if sisa is None:
        raise HTTPException(status_code=400, detail=f"Tagihan {bill['invoice_number']} belum dibukukan atau sudah batal.")
    if total > sisa:
        raise HTTPException(status_code=400, detail=f"Melebihi sisa tagihan {tg.rp(sisa)}: nota debit {tg.rp(total)} atas {bill['invoice_number']}.")
    app_id = uuid_module.uuid4()
    await conn.execute(
        """INSERT INTO vendor_credit_applications (id, tenant_id, vendor_credit_id, bill_id, bill_number, amount_applied, application_date,
                                                   created_by, status)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'active')""",
        app_id, tid, vc_id, bill["id"], bill["invoice_number"], total, application_date, ctx["user_id"])
    # V443: aplikasi aktif DULU, lalu kaitan (compare-and-set; pagar DB menolak tanpa aplikasi aktif)
    r = await conn.execute(
        """UPDATE vendor_credits SET original_bill_id = $1, original_bill_number = $2, updated_at = NOW()
           WHERE id = $3 AND tenant_id = $4 AND original_bill_id IS NULL""",
        bill["id"], bill["invoice_number"], vc_id, tid)
    if r != "UPDATE 1":
        raise HTTPException(status_code=409, detail={"code": "ND_SUDAH_BERTAUT", "message": "Nota debit ini sudah terkait ke tagihan."})
    await segarkan_cache_hutang_tagihan(conn, tid, bill["id"])
    return {"application_id": str(app_id), "bill_id": str(bill["id"]), "bill_number": bill["invoice_number"], "amount": float(total)}


async def lepas_nota_debit(conn, ctx: dict, vc_id, alasan: Optional[str]) -> dict:
    """Inti POST /vendor-credits/{id}/unapply, di transaksi PEMANGGIL. Tanpa jurnal."""
    from .pihak_helpers import segarkan_cache_hutang_tagihan
    tid = ctx["tenant_id"]
    if not (alasan or "").strip():
        raise HTTPException(status_code=422, detail="Alasan melepas penerapan wajib diisi.")
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"VENDOR_CREDIT_APPLY:{vc_id}")
    vc = await conn.fetchrow("SELECT * FROM vendor_credits WHERE id = $1 AND tenant_id = $2 FOR UPDATE", vc_id, tid)
    if not vc:
        raise HTTPException(status_code=404, detail="Nota debit tidak ditemukan.")
    app = await conn.fetchrow(
        """SELECT * FROM vendor_credit_applications WHERE vendor_credit_id = $1 AND tenant_id = $2 AND status = 'active'
           ORDER BY created_at DESC LIMIT 1 FOR UPDATE""", vc_id, tid)
    if not app:
        raise HTTPException(status_code=400, detail="Nota debit ini tidak sedang diterapkan ke tagihan.")
    await conn.execute(
        """UPDATE vendor_credit_applications SET status = 'reversed', reversed_at = now(), reversed_by = $1, reversal_reason = $2
           WHERE id = $3""", ctx["user_id"], alasan.strip(), app["id"])
    await conn.execute(
        """UPDATE vendor_credits SET original_bill_id = NULL, original_bill_number = NULL, updated_at = NOW()
           WHERE id = $1 AND tenant_id = $2 AND original_bill_id = $3""", vc_id, tid, app["bill_id"])
    await segarkan_cache_hutang_tagihan(conn, tid, app["bill_id"])
    return {"application_id": str(app["id"]), "bill_id": str(app["bill_id"])}


async def balik_hpp_stok_pajak(conn, ctx: dict, vc, tanggal) -> int:
    """Void: cermin SETIAP jurnal VENDOR_CREDIT_COGS efektif + balik kartu stok per jurnal HPP + hapus DTL. Kembali: jumlah jurnal HPP dibalik."""
    from .inventory_helpers import record_inventory_reversal
    tid = ctx["tenant_id"]
    n = 0
    for j in await conn.fetch(
            """SELECT id, journal_number, total_debit FROM journal_entries
               WHERE tenant_id = $1 AND source_type = 'VENDOR_CREDIT_COGS' AND source_id = $2 AND status = 'POSTED'
                 AND reversed_by_id IS NULL AND reversal_of_id IS NULL ORDER BY created_at""", tid, vc["id"]):
        rev = uuid_module.uuid4()
        nomor = await conn.fetchval("SELECT get_next_journal_number($1, 'RV-COGS-ND')", tid) or f"RV-COGS-ND-{vc['credit_number']}-{n + 1}"
        await conn.execute(
            """INSERT INTO journal_entries (id, tenant_id, journal_number, journal_date, description, source_type, source_id, reversal_of_id,
                                            status, total_debit, total_credit, created_by)
               VALUES ($1, $2, $3, $4, $5, 'VENDOR_CREDIT_COGS', $6, $7, 'DRAFT', $8, $8, $9)""",
            rev, tid, nomor, tanggal, f"Void HPP nota debit {vc['credit_number']}", vc["id"], j["id"], j["total_debit"], ctx["user_id"])
        for idx, ln in enumerate(await conn.fetch("SELECT * FROM journal_lines WHERE journal_id = $1 ORDER BY line_number", j["id"]), 1):
            await conn.execute(
                "INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo) VALUES ($1, $2, $3, $4, $5, $6, $7)",
                uuid_module.uuid4(), rev, idx, ln["account_id"], ln["credit"], ln["debit"], f"Pembalik - {ln['memo'] or ''}")
        await conn.execute("UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", rev)
        # Law 2: asli TETAP POSTED + reversed_by_id
        await conn.execute("UPDATE journal_entries SET reversed_by_id = $2, reversed_at = NOW() WHERE id = $1", j["id"], rev)
        await record_inventory_reversal(conn=conn, tenant_id=tid, source_type="VENDOR_CREDIT", source_id=vc["id"],
                                        reversal_journal_id=rev, created_by=ctx["user_id"], reversal_date=tanggal,
                                        notes_prefix="VOID_ND", journal_id=j["id"])
        n += 1
    await conn.execute("DELETE FROM document_tax_lines WHERE tenant_id = $1 AND document_id = $2", tid, vc["id"])
    return n
