"""UJI NYATA ALUR pratinjau -> tulis Uang Muka Pembelian (CW penuh, 22 Okt 2026) -- DB SCRATCH (dump terbaru; dibuang sesudahnya).
Kode router ASLI; pemicu DB nyata. Tiap aksi: pratinjau (READ ONLY), payload dikirim APA ADANYA ke tulis, lalu:
  baris jurnal TERTULIS (account_code, debit, kredit) == rencana pratinjau; before/after (sisa/terpakai/dikembalikan JURNAL) == keadaan
  sesudah tulis; sisa tagihan sesudah == pratinjau. Juga: blok pratinjau == galat tulis (void masih dipakai), history berisi semua
  kejadian, applicable-bills memuat tagihan dgn sisa == compute_ap_outstanding."""
import asyncio, os, sys, uuid
sys.path.insert(0, "/repo/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
from decimal import Decimal
import asyncpg
from fastapi import HTTPException
from starlette.requests import Request

from app.routers import vendor_deposits as VD
from app.schemas import vendor_deposits as VS
from app.services import uang_muka_vendor_rencana as R
from app.services.role_resolver import AccountRole
from app.services import uang_muka_vendor as UMV

gagal = []


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:300]}"))
    if not ok: gagal.append(nama)


def req(tid, uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": tid, "user_id": uid, "role": "OWNER"}
    return r


async def baris(c, tid, jid):
    return [(r["account_code"], float(r["debit"]), float(r["credit"])) for r in await c.fetch(
        """SELECT coa.account_code, jl.debit, jl.credit FROM journal_lines jl JOIN chart_of_accounts coa ON coa.id = jl.account_id AND coa.tenant_id = $2
           WHERE jl.journal_id = $1 ORDER BY jl.line_number""", jid, tid)]


def rencana(p):
    return [(l["account_code"], l["debit"], l["credit"]) for l in p["journals"][0]["lines"]] if p["journals"] else []


async def main():
    c = await asyncpg.connect(os.environ["URL"])
    class Pool:
        def acquire(s):
            class CM:
                async def __aenter__(x): return c
                async def __aexit__(x, *a): return False
            return CM()
    async def gp(): return Pool()
    VD.get_pool = gp
    import app.services.dashboard_izin as DI
    async def boleh(request, m): return True
    DI.boleh_baca = boleh

    pilih = None
    for t in await c.fetch("SELECT DISTINCT tenant_id FROM bills WHERE vendor_id IS NOT NULL ORDER BY tenant_id"):
        tid = t["tenant_id"]
        try:
            for peran in (AccountRole.AP_PREPAID, AccountRole.AP_TRADE, AccountRole.CASH_GENERAL):
                await UMV.akun_peran(c, tid, peran)
        except HTTPException:
            continue
        o = await c.fetchrow("""SELECT o.bill_id, o.vendor_id, o.outstanding FROM compute_ap_outstanding($1) o JOIN bills b ON b.id = o.bill_id
                                WHERE o.outstanding >= 2 AND b.status IN ('posted', 'partial') ORDER BY o.bill_date DESC LIMIT 1""", tid)
        ba = await c.fetchval("SELECT id FROM bank_accounts WHERE tenant_id = $1 AND coa_id IS NOT NULL ORDER BY created_at LIMIT 1", tid)
        if o and ba:
            pilih = (tid, o, ba); break
    cek("ada tenant + tagihan bersisa + rekening di scratch", pilih is not None)
    if not pilih:
        return await selesai(c)
    tid, o, ba = pilih
    uid = str(await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", tid) or uuid.uuid4())
    r = req(tid, uid)
    bill, vendor = o["bill_id"], o["vendor_id"]
    x = min(Decimal("1000.50"), (Decimal(str(o["outstanding"])) / 2).quantize(Decimal("0.01")))
    A = x * 3
    print(f"INFO tenant={tid} tagihan={bill} x={x} A={A}")

    # 1. buat + simpan & posting
    body = VS.VendorDepositCreate(deposit_date=await VD.tanggal_dokumen(c, tid), vendor_id=vendor, amount=str(A), bank_account_id=ba,
                                  reference="TES E2E pratinjau UM", simpan_dan_posting=True)
    p = (await VD.preview_create_vendor_deposit(r, body))["data"]
    cek("buat/preview ok", p["can_proceed"], p["blocks"])
    v = await VD.create_vendor_deposit(r, VS.VendorDepositCreate(**p["payload"]))
    did = uuid.UUID(str(v["id"]))
    vd = await c.fetchrow("SELECT status, journal_id FROM vendor_deposits WHERE id = $1", did)
    cek("simpan & posting: status posted + jurnal", vd["status"] == "posted" and vd["journal_id"] is not None, dict(vd))
    cek("buat: baris jurnal tertulis == rencana", await baris(c, tid, vd["journal_id"]) == rencana(p), (await baris(c, tid, vd["journal_id"]), rencana(p)))
    k = await R.keadaan(c, tid, did)
    cek("buat: after.remaining == sisa jurnal", k["remaining"] == p["after"]["remaining"], (k, p["after"]))

    # 2. terapkan
    p = (await VD.preview_apply_vendor_deposit(r, did, VS.ApplyDepositRequest(bill_id=bill, amount=str(x))))["data"]
    cek("apply/preview ok", p["can_proceed"], p["blocks"])
    t = await VD.apply_vendor_deposit(r, did, VS.ApplyDepositRequest(**p["payload"]))
    app = uuid.UUID(str(t["application_id"]))
    jid = await c.fetchval("SELECT journal_id FROM vendor_deposit_applications WHERE id = $1", app)
    cek("apply: baris jurnal == rencana", await baris(c, tid, jid) == rencana(p), (await baris(c, tid, jid), rencana(p)))
    k = await R.keadaan(c, tid, did)
    st = float(await R.sisa_tagihan(c, tid, bill))
    cek("apply: after == keadaan nyata; sisa tagihan == pratinjau",
        (k["remaining"], k["amount_applied"]) == (p["after"]["remaining"], p["after"]["amount_applied"]) and st == p["bills"][0]["remaining_after"],
        (k, p["after"], st, p["bills"]))

    # 3. lepas
    p = (await VD.preview_unapply_vendor_deposit(r, did, app))["data"]
    cek("unapply/preview ok", p["can_proceed"], p["blocks"])
    await VD.unapply_vendor_deposit(r, did, app)
    rev = await c.fetchval("SELECT reversed_by_id FROM vendor_deposit_applications WHERE id = $1", app)
    cek("unapply: baris jurnal pembalik == rencana", await baris(c, tid, rev) == rencana(p), (await baris(c, tid, rev), rencana(p)))
    k = await R.keadaan(c, tid, did)
    cek("unapply: after == nyata; sisa tagihan pulih", k["remaining"] == p["after"]["remaining"] and float(await R.sisa_tagihan(c, tid, bill)) == p["bills"][0]["remaining_after"])
    p2 = (await VD.preview_unapply_vendor_deposit(r, did, app))["data"]
    cek("unapply kedua: pratinjau SUDAH_DILEPAS", [b["code"] for b in p2["blocks"]] == ["SUDAH_DILEPAS"], p2["blocks"])

    # 4. kembalikan
    p = (await VD.preview_refund_vendor_deposit(r, did, VS.VendorDepositRefundCreate(refund_date=await VD.tanggal_dokumen(c, tid), amount=str(x), bank_account_id=ba)))["data"]
    cek("refund/preview ok", p["can_proceed"], p["blocks"])
    f = await VD.refund_vendor_deposit(r, did, VS.VendorDepositRefundCreate(**p["payload"]))
    jr = await c.fetchval("SELECT journal_id FROM vendor_deposit_refunds WHERE id = $1", uuid.UUID(str(f["id"])))
    cek("refund: baris jurnal == rencana", await baris(c, tid, jr) == rencana(p), (await baris(c, tid, jr), rencana(p)))
    nb = await c.fetchval("SELECT count(*) FROM bank_transactions WHERE journal_id = $1", jr)
    cek("refund: cermin bank sesuai pratinjau", nb == len(p["bank_transactions"]), (nb, p["bank_transactions"]))
    k = await R.keadaan(c, tid, did)
    cek("refund: after == nyata", (k["remaining"], k["amount_refunded"]) == (p["after"]["remaining"], p["after"]["amount_refunded"]), (k, p["after"]))

    # 5. batal: diblok (refund ada) -> pratinjau & tulis sama
    p = (await VD.preview_void_vendor_deposit(r, did, VS.VoidDepositRequest(reason="TES E2E")))["data"]
    try:
        await VD.void_vendor_deposit(r, did, VS.VoidDepositRequest(reason="TES E2E"))
        cek("void ditahan refund: tulis menolak", False, "lolos")
    except HTTPException as e:
        msg = e.detail if isinstance(e.detail, str) else e.detail["message"]
        cek("void ditahan: tulis menolak dgn pesan blok pertama pratinjau", not p["can_proceed"] and msg == p["blocks"][0]["message"], (msg, p["blocks"]))

    # 6. uang muka kedua: buat draf -> posting (pratinjau) -> batal dgn alasan; draf ketiga -> hapus
    v2 = await VD.create_vendor_deposit(r, VS.VendorDepositCreate(deposit_date=await VD.tanggal_dokumen(c, tid), vendor_id=vendor, amount="250.25", bank_account_id=ba))
    d2 = uuid.UUID(str(v2["id"]))
    p = (await VD.preview_post_vendor_deposit(r, d2))["data"]
    await VD.post_vendor_deposit(r, d2)
    j2 = await c.fetchval("SELECT journal_id FROM vendor_deposits WHERE id = $1", d2)
    cek("post: baris jurnal == rencana", await baris(c, tid, j2) == rencana(p), (await baris(c, tid, j2), rencana(p)))
    pv = (await VD.preview_void_vendor_deposit(r, d2, None))["data"]
    cek("void tanpa alasan: blok VOID_REASON_REQUIRED", [b["code"] for b in pv["blocks"]] == ["VOID_REASON_REQUIRED"], pv["blocks"])
    p = (await VD.preview_void_vendor_deposit(r, d2, VS.VoidDepositRequest(reason="TES E2E batal")))["data"]
    await VD.void_vendor_deposit(r, d2, VS.VoidDepositRequest(**p["payload"]))
    jv = await c.fetchval("SELECT id FROM journal_entries WHERE reversal_of_id = $1", j2)
    cek("void: baris jurnal pembalik == rencana", await baris(c, tid, jv) == rencana(p), (await baris(c, tid, jv), rencana(p)))
    v3 = await VD.create_vendor_deposit(r, VS.VendorDepositCreate(deposit_date=await VD.tanggal_dokumen(c, tid), vendor_id=vendor, amount="10"))
    d3 = uuid.UUID(str(v3["id"]))
    p = (await VD.preview_delete_vendor_deposit(r, d3))["data"]
    await VD.delete_vendor_deposit(r, d3)
    cek("delete: pratinjau ok + draf hilang", p["can_proceed"] and await c.fetchval("SELECT count(*) FROM vendor_deposits WHERE id = $1", d3) == 0)

    # 7. history + calon faktur
    h = (await VD.get_vendor_deposit_history(r, did))["data"]
    jenis = {e["jenis"] for e in h["events"]}
    cek("history: dibuat/diposting/diterapkan/dilepas/dikembalikan", {"UM_DIBUAT", "UM_DIPOSTING", "UM_DITERAPKAN", "UM_DILEPAS", "UM_DIKEMBALIKAN"} <= jenis, jenis)
    h2 = (await VD.get_vendor_deposit_history(r, d2))["data"]
    cek("history: batal tercatat dgn alasan (audit VD_VOIDED)", any("TES E2E batal" in (e.get("ringkas") or "") for e in h2["events"]), [e.get("ringkas") for e in h2["events"]])
    ab = (await VD.get_applicable_bills(r, did))["data"]
    row = next((b for b in ab["bills"] if b["bill_id"] == str(bill)), None)
    cek("applicable-bills: tagihan ada, sisa string 2dp == compute_ap", row is not None and row["remaining"] == f"{await R.sisa_tagihan(c, tid, bill):.2f}", (row, ab["bills"][:3]))
    await selesai(c)


async def selesai(c):
    await c.close()
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
