"""BUG-069 uji DB di SALINAN (bukan prod): V485 + kode cabang (rute ASLI) atas klon milkydb, tenant nyawit.
T1 V485 · T2 kontrol: atribusi lama di faktur lunas DIBLOK · T3 pratinjau kredit vendor · T4 buat+posting (tagihan tetap 0, kredit vendor = total,
stok keluar dari sumber retur, jurnal seimbang) · T5 terapkan ke tagihan terbuka · T6 ND kedua refund tunai (+cermin bank) · T7 sebagian = blok
· T8 drift AP (Rule 8) & R9 bank = tak berubah."""
import asyncio
import sys
import uuid
from datetime import date
from decimal import Decimal as D

import asyncpg
from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import items as RI  # noqa: E402
from app.routers import vendor_credits as RVC  # noqa: E402
from app.schemas.vendor_credits import ApplyVendorCreditRequest, CreateVendorCreditRequest, ReceiveRefundRequest  # noqa: E402

TID = "pt-kita-nyawit-bersama"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(ok)
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


async def sambung():
    return await asyncpg.connect(**RI.settings.get_db_config())


def req(uid, kunci=None):
    r = Request({"type": "http", "method": "POST", "path": "/api/vendor-credits",
                 "headers": [(b"x-idempotency-key", kunci.encode())] if kunci else []})
    r.state.user = {"tenant_id": TID, "user_id": str(uid), "username": "uji-salinan"}
    return r


async def ap(conn):
    rows = await conn.fetch("SELECT bill_id, bill_number, bill_status, outstanding FROM compute_ap_outstanding($1)", TID)
    return {(str(r["bill_id"]) if r["bill_id"] else f"{r['bill_status']}"): r for r in rows}


async def drift(conn):
    raw = await conn.fetchval("""SELECT COALESCE(SUM(jl.credit) - SUM(jl.debit), 0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
        JOIN chart_of_accounts c ON c.id = jl.account_id WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND is_effective_journal(je.id)
        AND c.account_type = 'PAYABLE'""", TID)
    sub = await conn.fetchval("SELECT COALESCE(SUM(outstanding), 0) FROM compute_ap_outstanding($1)", TID)
    adj = await conn.fetchval("SELECT COALESCE(SUM(net), 0) FROM compute_ap_adjustments($1)", TID)
    return D(str(raw)) - D(str(sub)) - D(str(adj))


async def r9(conn):
    await conn.execute(f"SET app.tenant_id = '{TID}'")
    return len(await conn.fetch("SELECT * FROM check_bank_sync_health($1)", TID))


async def sisa(conn, bill_id):
    v = await conn.fetchval("SELECT outstanding FROM compute_ap_rincian($1) WHERE bill_id = $2", TID, bill_id)
    return D(str(v)) if v is not None else None


async def main():
    c = await sambung()
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY (role::text = \'OWNER\') DESC LIMIT 1', TID)
    b8 = await c.fetchrow("SELECT id, vendor_id FROM bills WHERE tenant_id = $1 AND invoice_number = 'BILL-2611-0008'", TID)
    b7 = await c.fetchval("SELECT id FROM bills WHERE tenant_id = $1 AND invoice_number = 'BILL-2611-0007'", TID)
    baris = await c.fetch("""SELECT bi.id, bi.unit, bi.quantity FROM bill_items bi JOIN products p ON p.id = bi.product_id
                             WHERE bi.bill_id = $1 AND p.track_inventory ORDER BY bi.line_number""", b8["id"])
    d0, r90, ap0 = await drift(c), await r9(c), await ap(c)

    # T1 V485 di salinan
    sql = open("/app/backend/migrations/V485__vendor_credits_return_bill_id.sql").read()
    async with c.transaction():
        await c.execute(sql)
    n = await c.fetchval("SELECT count(*) FROM vendor_credits WHERE return_bill_id IS NOT NULL")
    harap = await c.fetchval("""SELECT count(*) FROM vendor_credits WHERE original_bill_id IS NOT NULL AND COALESCE(reason,'') NOT IN ('discount','pricing_error')""")
    ap1 = await ap(c)
    cek("T1 V485: kolom + backfill = ND retur bertaut, angka AP tak berubah", n == harap and n > 0 and
        {k: v["outstanding"] for k, v in ap0.items()} == {k: v["outstanding"] for k, v in ap1.items()}, f"backfill {n}/{harap}")
    sb8 = await sisa(c, b8["id"])
    cek("T1b BILL-0008 lunas (sisa jurnal 0)", sb8 == 0, f"sisa {sb8}")

    lines = [{"original_bill_item_id": str(baris[0]["id"]), "quantity": "1"}]
    # T2 KONTROL: jalur atribusi lama (original_bill_id = faktur lunas) DITOLAK saat posting -> uji ini memang membedakan perilaku
    async with c.transaction():
        try:
            pv = await RVC.preview_create_vendor_credit(req(uid), RVC.VendorCreditPreviewRequest(original_bill_id=str(b8["id"]), reason="return", lines=lines))
            pay = pv["data"]["payload"]
            pay = {**pay, "original_bill_id": str(b8["id"])}
            pay.pop("return_bill_id", None)
            r = await RVC.create_vendor_credit(req(uid, f"k069-{uuid.uuid4()}"), CreateVendorCreditRequest(**pay))
            vid = (r["data"] if isinstance(r, dict) else r.data)["id"]
            try:
                await RVC.post_vendor_credit(req(uid, f"k069p-{uuid.uuid4()}"), UUID_(vid))
                cek("T2 kontrol: ND teratribusi ke faktur lunas DIBLOK", False, "posting lolos")
            except HTTPException as e:
                cek("T2 kontrol: ND teratribusi ke faktur lunas DIBLOK", "Melebihi sisa tagihan" in str(e.detail), str(e.detail)[:120])
        finally:
            pass
        raise_rollback = True
    # (transaksi kontrol dibatalkan lewat koneksi rute sendiri: hapus draf kontrol bila ada)
    await c.execute("DELETE FROM vendor_credit_items WHERE vendor_credit_id IN (SELECT id FROM vendor_credits WHERE tenant_id=$1 AND status='draft' AND original_bill_id=$2 AND created_at > now() - interval '10 minutes')", TID, b8["id"])
    await c.execute("DELETE FROM vendor_credits WHERE tenant_id=$1 AND status='draft' AND original_bill_id=$2 AND created_at > now() - interval '10 minutes'", TID, b8["id"])

    # T3 pratinjau
    pv = await RVC.preview_create_vendor_credit(req(uid), RVC.VendorCreditPreviewRequest(original_bill_id=str(b8["id"]), reason="return", lines=lines))
    d = pv["data"]
    kode = [x["code"] for x in d.get("notes", [])]
    cek("T3 pratinjau: bisa, catatan ND_JADI_KREDIT_VENDOR, payload tanpa atribusi + return_bill",
        d["can_proceed"] and "ND_JADI_KREDIT_VENDOR" in kode and d["payload"]["original_bill_id"] is None
        and d["payload"].get("return_bill_id") == str(b8["id"]), f"blok {[b.get('code') for b in d['blocks']]} catatan {kode}")

    # T4 buat + posting
    r = await RVC.create_vendor_credit(req(uid, f"k069-{uuid.uuid4()}"), CreateVendorCreditRequest(**d["payload"]))
    vid = (r["data"] if isinstance(r, dict) else r.data)["id"]
    await RVC.post_vendor_credit(req(uid, f"k069p-{uuid.uuid4()}"), UUID_(vid))
    vc = await c.fetchrow("SELECT * FROM vendor_credits WHERE id = $1", UUID_(vid))
    tot = D(str(vc["total_amount"]))
    j = await c.fetchrow("""SELECT SUM(jl.debit) d, SUM(jl.credit) k FROM journal_entries je JOIN journal_lines jl ON jl.journal_id = je.id
                            WHERE je.source_id::text = $1 AND je.source_type = 'VENDOR_CREDIT' AND je.status = 'POSTED'""", vid)
    out = await c.fetchval("SELECT COALESCE(SUM(quantity_out), 0) FROM inventory_ledger WHERE source_id::text = $1 AND source_type = 'VENDOR_CREDIT'", vid)
    ap2 = await ap(c)
    kred = [v for k, v in ap2.items() if k == "vendor_credit"]
    sb8b = await sisa(c, b8["id"])
    cek("T4 posting: return_bill=BILL-0008, original NULL, tagihan tetap 0, jurnal seimbang, stok keluar",
        vc["status"] == "posted" and vc["original_bill_id"] is None and vc["return_bill_id"] == b8["id"] and sb8b == 0
        and j["d"] == j["k"] and D(str(j["d"])) == tot and D(str(out)) > 0, f"total {tot} jurnal {j['d']}/{j['k']} keluar {out} sisa8 {sb8b}")
    sisa_nd = await RVC.ND.sisa_nd(c, TID, UUID_(vid))
    cek("T4b saldo kredit vendor = total ND (sql_sisa_nd)", D(str(sisa_nd)) == tot, f"sisa_nd {sisa_nd} kredit_rows {len(kred)}")

    # T5 terapkan ke BILL-0007 (terbuka, vendor sama)
    s7a = await sisa(c, b7)
    await RVC.apply_vendor_credit(req(uid, f"k069a-{uuid.uuid4()}"), UUID_(vid),
                                  ApplyVendorCreditRequest(applications=[{"bill_id": str(b7), "amount": int(tot)}]))
    s7b = await sisa(c, b7)
    cek("T5 terapkan ke BILL-0007: sisa turun sebesar total, kredit ND jadi 0", s7a - s7b == tot and D(str(await RVC.ND.sisa_nd(c, TID, UUID_(vid)))) == 0,
        f"BILL-0007 {s7a} -> {s7b}")

    # T6 ND kedua -> refund tunai ke Mandiri (bank) -> cermin bank
    lines2 = [{"original_bill_item_id": str(baris[-1]["id"]), "quantity": "1"}]
    pv2 = await RVC.preview_create_vendor_credit(req(uid), RVC.VendorCreditPreviewRequest(original_bill_id=str(b8["id"]), reason="return", lines=lines2))
    r2 = await RVC.create_vendor_credit(req(uid, f"k069-{uuid.uuid4()}"), CreateVendorCreditRequest(**pv2["data"]["payload"]))
    v2 = (r2["data"] if isinstance(r2, dict) else r2.data)["id"]
    await RVC.post_vendor_credit(req(uid, f"k069p-{uuid.uuid4()}"), UUID_(v2))
    t2 = int(D(str(await c.fetchval("SELECT total_amount FROM vendor_credits WHERE id = $1", UUID_(v2)))))
    bank = await c.fetchval("SELECT id FROM bank_accounts WHERE tenant_id = $1 AND account_name = 'Mandiri Operasional'", TID)
    await RVC.receive_refund(req(uid, f"k069r-{uuid.uuid4()}"), UUID_(v2),
                             ReceiveRefundRequest(amount=t2, refund_date=date.today(), payment_method="transfer", bank_account_id=str(bank)))
    bt = await c.fetchval("SELECT count(*) FROM bank_transactions bt JOIN journal_entries je ON je.id = bt.journal_id WHERE je.source_id::text = $1 AND je.source_type = 'VENDOR_CREDIT_REFUND'", v2)
    cek("T6 ND kedua refund tunai: kredit 0 + cermin bank_transactions", D(str(await RVC.ND.sisa_nd(c, TID, UUID_(v2)))) == 0 and bt >= 1, f"refund {t2} bank_txn {bt}")

    # T7 sebagian lunas -> blok berkalimat (BILL-0007 sisa kini lebih kecil dari ND semua baris)
    pv3 = await RVC.preview_create_vendor_credit(req(uid), RVC.VendorCreditPreviewRequest(original_bill_id=str(b7), reason="return"))
    bl = [b for b in pv3["data"]["blocks"] if "Melebihi sisa tagihan" in b.get("message", "")]
    cek("T7 sebagian lunas: blok + jalan keluar", bool(bl) and "Kurangi qty retur" in bl[0]["message"] and not pv3["data"]["can_proceed"],
        bl[0]["message"][:140] if bl else str(pv3["data"]["blocks"])[:140])

    # T8 Rule 8 + R9 tak berubah
    d1, r91 = await drift(c), await r9(c)
    cek("T8 drift AP Rule 8 tak berubah", d1 == d0, f"{d0} -> {d1}")
    cek("T8b R9 bank tak berubah", r91 == r90, f"{r90} -> {r91} baris")
    await c.close()
    print(f"RINGKAS {sum(HASIL)}/{len(HASIL)} PASS", flush=True)


def UUID_(x):
    return x if isinstance(x, uuid.UUID) else uuid.UUID(str(x))


asyncio.run(main())
