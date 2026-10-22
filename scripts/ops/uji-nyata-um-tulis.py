"""UJI NYATA P-U3b Uang Muka Pembelian sisi tulis (22 Okt 2026) -- DB SCRATCH (dump terbaru + V437 + V438), SEMUA dalam satu transaksi ROLLBACK.
Kode router ASLI dijalankan (bukan tiruan) dengan pool yang menyerahkan koneksi scratch; pemicu DB nyata (hash chain, period, guard_arap,
update_vendor_deposit_status) ikut berjalan.

Skenario pada satu tagihan terbit bersisa (tenant mana pun) + vendornya:
  buat UM -> post -> apply x -> unapply -> apply x -> unapply kedua kali (idempoten) -> unapply penerapan ke-2 -> void.
  Di tiap langkah: sisa UM JURNAL (UMV.sisa_jurnal), sisa tagihan (compute_ap_outstanding), cache deposit (applied_amount/status),
  status baris penerapan. Wajib: sisa sesudah unapply == sebelum apply (UM dan tagihan), apply ulang == apply pertama.
  Juga: replay X-Idempotency-Key apply = respons sama tanpa jurnal baru; V438 -> 5 fungsi hilang."""
import asyncio, os, sys, uuid
sys.path.insert(0, "/repo/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
from datetime import date
from decimal import Decimal
import asyncpg
from fastapi import HTTPException
from starlette.requests import Request

from app.routers import vendor_deposits as VD
from app.schemas import vendor_deposits as VS
from app.services import uang_muka_vendor as UMV
from app.services.role_resolver import AccountRole

gagal = []


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:240]}"))
    if not ok: gagal.append(nama)


class _Batal(Exception):
    pass


def req(tid, uid, kunci=None):
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": h, "query_string": b""}, receive)
    r.state.user = {"tenant_id": tid, "user_id": uid}
    return r


async def main():
    c = await asyncpg.connect(os.environ["URL"])
    n_fn = await c.fetchval("""SELECT count(*) FROM pg_proc WHERE proname IN ('get_vendor_deposit_applications','get_vendor_deposit_refunds',
                               'get_vendor_deposits','get_available_vendor_deposits','get_vendor_deposit_summary')""")
    cek("V438: lima fungsi rusak hilang", n_fn == 0, n_fn)
    kol = {r["column_name"] for r in await c.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'vendor_deposit_applications'")}
    cek("V437: kolom status/reversed_by_id/reversed_at/reversed_by", {"status", "reversed_by_id", "reversed_at", "reversed_by"} <= kol, kol)

    calon = []
    for t in await c.fetch("SELECT DISTINCT tenant_id FROM bills WHERE vendor_id IS NOT NULL ORDER BY tenant_id"):
        for o in await c.fetch("""SELECT bill_id, vendor_id, outstanding FROM compute_ap_outstanding($1)
                                  WHERE outstanding >= 2 AND vendor_id IS NOT NULL ORDER BY bill_date DESC LIMIT 3""", t["tenant_id"]):
            calon.append({**dict(o), "tenant_id": t["tenant_id"]})
    cek("ada tagihan terbit bersisa di scratch", bool(calon), "tak ada calon")
    if not calon:
        return await selesai(c)
    uid = await c.fetchval('SELECT id FROM "User" LIMIT 1')
    uid = str(uid) if uid else str(uuid.uuid4())

    class Pool:
        def acquire(s):
            class CM:
                async def __aenter__(x): return c
                async def __aexit__(x, *a): return False
            return CM()
    async def gp(): return Pool()
    VD.get_pool = gp

    pilihan = None
    tr = c.transaction(); await tr.start()
    try:
        for cal in calon:
            try:
                for peran in (AccountRole.AP_PREPAID, AccountRole.AP_TRADE, AccountRole.CASH_GENERAL):
                    await UMV.akun_peran(c, cal["tenant_id"], peran)
                pilihan = cal; break
            except HTTPException:
                continue
        cek("tenant berperan akun lengkap", pilihan is not None)
        if pilihan:
            await skenario(c, pilihan, uid)
    finally:
        await tr.rollback()
    await selesai(c)


async def skenario(c, cal, uid):
    tid, bill, vendor = cal["tenant_id"], cal["bill_id"], cal["vendor_id"]
    x = min(Decimal("1000.50"), (Decimal(str(cal["outstanding"])) / 2).quantize(Decimal("0.01")))
    A = x * 3
    print(f"INFO tenant={tid} tagihan={bill} x={x} A={A}")
    sisa_tagihan = lambda: VD.get_bill_remaining_from_journal(c, tid, bill)
    sisa_um = lambda: UMV.sisa_jurnal(c, tid, did)
    async def cache():
        r = await c.fetchrow("SELECT applied_amount, status FROM vendor_deposits WHERE id = $1", did)
        return Decimal(str(r["applied_amount"])), r["status"]
    async def n_jurnal():
        return await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1", tid)

    B0 = await sisa_tagihan()
    vd = await VD.create_vendor_deposit(req(tid, uid), VS.VendorDepositCreate(
        deposit_date=date(2026, 10, 22), vendor_id=vendor, amount=str(A), reference="TES E2E P-U3b"))
    did = uuid.UUID(str(vd["id"]))
    cek("buat: nominal 2dp tersimpan", Decimal(str(vd["amount"])) == A, vd["amount"])
    await VD.post_vendor_deposit(req(tid, uid), did)
    S0 = await sisa_um()
    cek("post: sisa UM jurnal == A", S0 == A, S0)

    k = f"harness-{uuid.uuid4()}"
    r1 = await VD.apply_vendor_deposit(req(tid, uid, k), did, VS.ApplyDepositRequest(bill_id=bill, amount=str(x)))
    nj = await n_jurnal()
    r1b = await VD.apply_vendor_deposit(req(tid, uid, k), did, VS.ApplyDepositRequest(bill_id=bill, amount=str(x)))
    cek("apply replay kunci sama = respons sama, nol jurnal baru", r1b == r1 and await n_jurnal() == nj, (r1b, r1))
    S1, B1 = await sisa_um(), await sisa_tagihan()
    cek("apply: sisa UM = A - x", S1 == A - x, S1)
    cek("apply: sisa tagihan = B0 - x", B1 == B0 - x, (B0, B1))
    cek("apply: respons sisa == jurnal", Decimal(str(r1["deposit_remaining"])) == S1 and Decimal(str(r1["bill_remaining"])) == B1, r1)
    cek("apply: cache deposit partial", await cache() == (x, "partial"), await cache())

    app1 = r1["application_id"]
    u1 = await VD.unapply_vendor_deposit(req(tid, uid), did, uuid.UUID(str(app1)))
    S2, B2 = await sisa_um(), await sisa_tagihan()
    cek("unapply: sisa UM kembali == sebelum apply", S2 == S0, (S0, S2))
    cek("unapply: sisa tagihan kembali == B0", B2 == B0, (B0, B2))
    cek("unapply: respons sisa == jurnal", Decimal(str(u1["data"]["deposit_remaining"])) == S2 and Decimal(str(u1["data"]["bill_remaining"])) == B2, u1)
    cek("unapply: cache deposit kembali posted, terpakai 0", await cache() == (Decimal("0"), "posted"), await cache())
    a1 = await c.fetchrow("SELECT status, reversed_by_id FROM vendor_deposit_applications WHERE id = $1", uuid.UUID(str(app1)))
    rv = await c.fetchrow("SELECT source_type, source_id, reversal_of_id, status, total_debit FROM journal_entries WHERE id = $1",
                          a1["reversed_by_id"])
    asli = await c.fetchrow("SELECT reversed_by_id, status FROM journal_entries WHERE id = (SELECT journal_id FROM vendor_deposit_applications WHERE id = $1)",
                            uuid.UUID(str(app1)))
    cek("unapply: penerapan reversed + jurnal pembalik POSTED bersumber tagihan",
        a1["status"] == "reversed" and rv["status"] == "POSTED" and rv["source_id"] == bill and rv["source_type"] == "DEPOSIT_APPLICATION"
        and Decimal(str(rv["total_debit"])) == x, (dict(a1), dict(rv)))
    cek("unapply: jurnal asli tetap POSTED + reversed_by_id (Law 2/26)", asli["status"] == "POSTED" and asli["reversed_by_id"] == a1["reversed_by_id"], dict(asli))
    seimbang = await c.fetchval("SELECT SUM(debit) = SUM(credit) FROM journal_lines WHERE journal_id = $1", a1["reversed_by_id"])
    cek("unapply: baris pembalik seimbang (Law 4)", seimbang is True)

    nj = await n_jurnal()
    u1b = await VD.unapply_vendor_deposit(req(tid, uid), did, uuid.UUID(str(app1)))
    cek("unapply kedua: idempoten, nol jurnal baru", u1b["data"]["reversal_journal_id"] == u1["data"]["reversal_journal_id"]
        and await n_jurnal() == nj and "sebelumnya" in u1b["message"], u1b)

    r2 = await VD.apply_vendor_deposit(req(tid, uid), did, VS.ApplyDepositRequest(bill_id=bill, amount=str(x)))
    S3, B3 = await sisa_um(), await sisa_tagihan()
    cek("apply ulang: sisa UM == apply pertama", S3 == S1, (S1, S3))
    cek("apply ulang: sisa tagihan == apply pertama", B3 == B1, (B1, B3))
    try:
        await VD.void_vendor_deposit(req(tid, uid), did)
        cek("void ditahan penerapan aktif", False, "void lolos")
    except HTTPException as e:
        cek("void ditahan penerapan aktif", e.status_code == 409 and e.detail["code"] == "UANG_MUKA_MASIH_DIPAKAI", e.detail)
    await VD.unapply_vendor_deposit(req(tid, uid), did, uuid.UUID(str(r2["application_id"])))
    v = await VD.void_vendor_deposit(req(tid, uid), did)
    S4, B4 = await sisa_um(), await sisa_tagihan()
    cek("void sesudah semua dilepas: boleh; sisa UM 0; tagihan kembali B0", v.status == "void" and S4 == 0 and B4 == B0, (v.status, S4, B4))


async def selesai(c):
    await c.close()
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
