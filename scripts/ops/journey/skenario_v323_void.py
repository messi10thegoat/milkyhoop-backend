"""V323 (Law 2 + Law 22) di salinan kaos: pagar DB 'status POSTED tak berubah' + UNIQUE chain_sequence POSTED.
Urutan: pasang V323 -> uji merah langsung (POSTED->VOID ditolak; nomor ganda ditolak) -> jalur void NYATA lewat API
(tagihan, pembayaran tagihan, beban, transfer bank, vendor credit, jurnal manual dibalik; gaji bila ada karyawan) ->
10 posting jurnal PARALEL -> rantai: tak ada pecahan baru, nol nomor ganda POSTED, nol jurnal VOID baru.
Skenario lama (faktur/RCV/DP/NK/to-invoice/PDF) dijalankan SESUDAH ini di salinan yang sama (V323 terpasang)."""
import asyncio
import os
import uuid
from datetime import timedelta

from journey_lib import OWNER, T

MIG = "backend/migrations/V323__jurnal_posted_tak_berubah_status.sql"
VENDOR, VENDOR_NAMA = "2a18aff0-f65a-472c-b835-762410f4c771", "PT Grosir Kaos"
BEBAN, MODAL = "18e8f8fb-4984-40ab-9f31-30e3fe82e407", "af9cdeb6-6187-4eb9-9ea4-d80bb012f647"
BANK1, BANK1_COA = "56842900-0ae4-4c25-8ad5-d0726a36e21b", "15105bf6-183e-4f93-abcc-5689f22fa394"
BANK2 = "ee1899ea-7875-4d1b-ba51-453ec7214597"


def _id(r):
    if not isinstance(r, dict):
        return None
    d = r.get("data") if isinstance(r.get("data"), dict) else r
    return d.get("id") or d.get("bill_id") or d.get("expense_id") or d.get("transfer_id") or d.get("journal_id") \
        or d.get("payment_id") or d.get("vendor_credit_id")


async def _keadaan(J):
    async with J.pool.acquire() as c:
        return dict(await c.fetchrow("""
            SELECT (SELECT count(*) FROM journal_entries WHERE tenant_id=$1 AND status='VOID') void,
                   (SELECT count(*) FROM (SELECT chain_sequence FROM journal_entries WHERE tenant_id=$1 AND status='POSTED'
                     AND chain_sequence IS NOT NULL GROUP BY 1 HAVING count(*)>1) x) ganda,
                   (SELECT verdict FROM verify_chain_integrity_all() WHERE tenant_id=$1) verdik,
                   (SELECT count(*) FILTER (WHERE NOT is_valid) FROM verify_chain_integrity($1)) pecah""", T))


async def _pasang(J):
    for akar in ("/wt", "/app"):
        p = os.path.join(akar, MIG)
        if os.path.exists(p):
            async with J.pool.acquire() as c:
                await c.execute(open(p).read())
            return True
    J.gagal("v323_ada", "berkas migrasi tak ditemukan")
    return False


async def _merah_langsung(J):
    async with J.pool.acquire() as c:
        jid = await c.fetchval("SELECT id FROM journal_entries WHERE tenant_id=$1 AND status='POSTED' ORDER BY chain_sequence DESC LIMIT 1", T)
        for nama, sql in (("posted_ke_void", "UPDATE journal_entries SET status='VOID' WHERE id=$1"),
                          ("posted_ke_draft", "UPDATE journal_entries SET status='DRAFT' WHERE id=$1"),
                          ("nomor_ganda", "UPDATE journal_entries SET chain_sequence = chain_sequence - 1 WHERE id=$1")):
            class _Lolos(Exception):
                pass
            try:
                async with c.transaction():
                    await c.execute(sql, jid)
                    raise _Lolos()
            except _Lolos:
                J.gagal(f"merah_{nama}", "LOLOS padahal harus ditolak V323")
            except Exception as e:
                print(f"merah {nama} ditolak: {type(e).__name__}: {str(e)[:90]}", flush=True)


async def _void(J, nama, buat_jalur, body, post=True, void_jalur="void", void_body=None):
    _, r = await J.langkah(f"{nama}_buat", "POST", buat_jalur, body, harap=(200, 201))
    did = _id(r)
    if not did:
        return J.gagal(f"{nama}_id", str(r)[:300])
    if post:
        _, rp = await J.langkah(f"{nama}_post", "POST", f"{buat_jalur}/{did}/post", {}, harap=(200, 201, 400))
    await J.langkah(f"{nama}_{void_jalur}", "POST", f"{buat_jalur}/{did}/{void_jalur}", void_body or {"reason": "TES E2E V323"},
                    harap=(200, 201))
    return did


async def jalankan(J):
    if not await _pasang(J):
        return
    awal = await _keadaan(J)
    print("awal", awal, flush=True)
    await _merah_langsung(J)
    h = J.hari
    # tagihan pembelian
    await _void(J, "tagihan", "/api/bills", {
        "vendor_id": VENDOR, "vendor_name": VENDOR_NAMA, "issue_date": h.isoformat(), "due_date": (h + timedelta(days=14)).isoformat(),
        "items": [{"description": "TES E2E V323 tagihan", "quantity": "2", "unit_price": 15000}]})
    # tagihan + pembayaran tagihan (void pembayaran lalu tagihan)
    _, rb = await J.langkah("tagihan2_buat", "POST", "/api/bills", {
        "vendor_id": VENDOR, "vendor_name": VENDOR_NAMA, "issue_date": h.isoformat(), "due_date": (h + timedelta(days=14)).isoformat(),
        "items": [{"description": "TES E2E V323 tagihan2", "quantity": "1", "unit_price": 20000}]})
    b2 = _id(rb)
    if b2:
        await J.langkah("tagihan2_post", "POST", f"/api/bills/{b2}/post", {}, harap=(200, 201, 400))
        await _void(J, "bayar_tagihan", "/api/bill-payments", {
            "vendor_id": VENDOR, "payment_date": h.isoformat(), "bank_account_id": BANK1, "total_amount": 20000,
            "payment_method": "bank_transfer", "allocations": [{"bill_id": b2, "amount_applied": 20000}],
            "idempotency_key": f"journey-bp-{uuid.uuid4()}"}, void_body={"reason": "TES E2E V323", "void_reason": "TES E2E V323"})
        await J.langkah("tagihan2_void", "POST", f"/api/bills/{b2}/void", {"reason": "TES E2E V323"})
    # beban
    await _void(J, "beban", "/api/expenses", {
        "expense_date": h.isoformat(), "paid_through_id": BANK1, "account_id": BEBAN, "amount": 12345,
        "notes": "TES E2E V323 beban"})
    # transfer bank
    await _void(J, "transfer", "/api/bank-transfers", {
        "from_bank_id": BANK1, "to_bank_id": BANK2, "amount": 5000, "transfer_date": h.isoformat(),
        "description": "TES E2E V323 transfer"})
    # vendor credit
    await _void(J, "vendor_credit", "/api/vendor-credits", {
        "vendor_id": VENDOR, "vendor_name": VENDOR_NAMA, "vendor_credit_date": h.isoformat(), "reason": "other",
        "items": [{"description": "TES E2E V323 VC", "quantity": "1", "unit_price": 7000}]})
    # jurnal manual -> dibalik
    await _void(J, "jurnal_manual", "/api/journals", {
        "entry_date": h.isoformat(), "description": "TES E2E V323 jurnal manual",
        "lines": [{"account_id": BEBAN, "debit": "1000"}, {"account_id": MODAL, "credit": "1000"}]},
        post=False, void_jalur="reverse", void_body={"reason": "TES E2E V323", "reversal_date": h.isoformat()})
    # gaji: bila ada karyawan
    async with J.pool.acquire() as c:
        emp = await c.fetchval("SELECT id FROM employees WHERE tenant_id=$1 LIMIT 1", T)
    if emp:
        _, rg = await J.langkah("gaji_buat", "POST", "/api/payroll", {"period_start": h.replace(day=1).isoformat(),
                                "period_end": h.isoformat(), "employee_ids": [str(emp)]})
        gid = _id(rg)
        if gid:
            await J.langkah("gaji_hitung", "POST", f"/api/payroll/{gid}/calculate", {}, harap=(200, 201, 400, 422))
            await J.langkah("gaji_submit", "POST", f"/api/payroll/{gid}/submit", {}, harap=(200, 201, 400, 422))
            await J.langkah("gaji_approve", "POST", f"/api/payroll/{gid}/approve", {}, harap=(200, 201, 400, 422))
            _, rp = await J.langkah("gaji_post", "POST", f"/api/payroll/{gid}/post", {}, harap=(200, 201, 400, 422))
            if isinstance(rp, dict) and rp.get("detail"):
                print(f"gaji: post ditolak aplikasi ({str(rp.get('detail'))[:120]}) -> void gaji TAK teruji di sini", flush=True)
            else:
                await J.langkah("gaji_void", "POST", f"/api/payroll/{gid}/void", {"reason": "TES E2E V323"})
    else:
        print("gaji: kaos tanpa karyawan -> tak diuji di sini", flush=True)
    # 10 posting jurnal paralel (satu tenant)
    async def satu(i):
        r = await J.cl.post("/api/journals", headers=J._hdr(OWNER), json={
            "entry_date": h.isoformat(), "description": f"TES E2E V323 paralel {i}",
            "lines": [{"account_id": BEBAN, "debit": "100"}, {"account_id": MODAL, "credit": "100"}]})
        jid = _id(r.json()) if r.status_code in (200, 201) else None
        # POST /api/journals langsung POSTED (terukur 28 Sep) -> sukses = jurnal POSTED berurutan
        return ("buat", r.status_code, "" if jid else r.text[:150])
    try:   # batas waktu: deadlock pool (Law 32) harus MERAH, bukan menggantung (28 Sep: journey macet 10 menit)
        hasil = await asyncio.wait_for(asyncio.gather(*[satu(i) for i in range(10)]), timeout=120)
    except asyncio.TimeoutError:
        return J.gagal("paralel_10", "10 posting paralel TAK SELESAI dalam 120 dtk (deadlock pool?)")
    buruk = [x for x in hasil if x[1] >= 400]
    print("paralel", [x[:2] for x in hasil], flush=True)
    if buruk:
        J.gagal("paralel_10", str(buruk)[:400])
    akhir = await _keadaan(J)
    print("akhir", akhir, flush=True)
    if akhir["void"] != awal["void"]:
        J.gagal("nol_void_baru", f"jurnal VOID {awal['void']} -> {akhir['void']} (ada jalur POSTED->VOID?)")
    if akhir["ganda"] != 0:
        J.gagal("nol_ganda_posted", f"{akhir['ganda']} nomor ganda di antara POSTED")
    if akhir["verdik"] not in ("PASS", "PASS_EXEMPT") or akhir["pecah"] != awal["pecah"]:
        J.gagal("rantai", f"{awal} -> {akhir}")
