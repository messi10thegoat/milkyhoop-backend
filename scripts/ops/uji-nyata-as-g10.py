"""UJI NYATA AS-G10 (24 Okt 2026) UM Pelanggan, kaos. Mode: baru [merah_idem|merah_kunci]
BAGIAN A (ROLLBACK, TES E2E): PATCH 2x kunci sama = replay + riwayat DEPOSIT_UPDATED SEKALI; isi beda 409; kunci SAMA lintas aksi (PATCH lalu
posting) = posting JALAN; posting 2x kunci sama = replay + jurnal SEKALI; hapus 2x = replay 200 (bukan 404) + 0 baris; pratinjau ubah draf ==
PATCH (perubahan sama, nol tulis); pratinjau ubah UM terposting = blok == galat PATCH 400 pesan sama; tanpa kunci posting ulang = 400 lama.
BAGIAN B (COMMIT 'TES E2E', aturan tetap pemilik 30 Sep; pola AS-GP4): UM draf -> PATCH nominal || posting BERSAMAAN (2 koneksi), tanpa macet
(30 dtk), nominal dokumen == debit jurnal posting. Bersih lewat APLIKASI (void/hapus); GL per akun + bank_transactions kaos sebelum == sesudah.
Kontrol merah (WAJIB FAIL): merah_idem = kunci dibuang (A gagal); merah_kunci = kode NYATA dari SALINAN pohon (scripts/ops/sabotase-as-g10.py:
PATCH tanpa kunci DEPOSIT_POST/FOR UPDATE/syarat draf + jeda 0,8 dtk) -> B: dokumen != jurnal."""
import asyncio, json, os, sys
from decimal import Decimal as D
from uuid import UUID, uuid4
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

T = "kaos-biru-konveksi"
MERAH = sys.argv[2] if len(sys.argv) > 2 else ""
gagal = []
SQL_GL = """SELECT jl.account_id::text, SUM(jl.debit - jl.credit) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
            WHERE je.tenant_id = $1 AND je.status = 'POSTED' GROUP BY 1"""
SQL_BANK = "SELECT bank_account_id::text, SUM(amount) FROM bank_transactions WHERE tenant_id = $1 GROUP BY 1"
SQL_JURNAL_DP = "SELECT count(*) FROM journal_entries WHERE tenant_id = $1 AND source_id::text = $2 AND status = 'POSTED'"


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:300]}"), flush=True)
    if not ok:
        gagal.append(nama)


async def main():
    from app.routers import customer_deposits as CD
    from app.schemas.customer_deposits import CreateCustomerDepositRequest, UpdateCustomerDepositRequest, VoidCustomerDepositRequest
    from app.services import idem_buat
    from app.utils.tanggal_tenant import tanggal_dokumen
    pool = await asyncpg.create_pool(os.environ["URL"], min_size=2, max_size=6)
    c = await asyncpg.connect(os.environ["URL"])
    if not await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T):
        sys.exit(f"TENANT TAK ADA: {T}")
    if MERAH == "merah_idem":
        idem_buat.kunci_dari = lambda request, kunci_body=None: None
    uid = await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", T)
    # pelanggan + rekening Kas/Bank + cara bayar dari UM kaos yang SUDAH ada (bukan dipatok)
    acu = await c.fetchrow("""SELECT cd.customer_id, cu.nama, cd.account_id::text AS account_id, cd.bank_account_id::text AS bank_account_id,
                                     cd.payment_method
                              FROM customer_deposits cd JOIN customers cu ON cu.id = cd.customer_id AND cu.tenant_id = cd.tenant_id  -- keduanya uuid (skema 24 Okt)
                              WHERE cd.tenant_id = $1 AND cd.status <> 'void' AND cd.account_id IS NOT NULL ORDER BY cd.created_at DESC LIMIT 1""", T)
    if acu is None:
        sys.exit("TAK ADA UM kaos acuan (pelanggan + rekening)")

    def req(kunci=None):
        async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
        hdr = [(b"x-idempotency-key", kunci.encode())] if kunci else []
        r = Request({"type": "http", "method": "POST", "path": "/", "headers": hdr, "query_string": b""}, receive)
        r.state.user = {"tenant_id": T, "user_id": str(uid), "role": "OWNER", "username": "TES E2E"}
        return r

    async def galat(coro):
        try:
            return None, await coro
        except HTTPException as e:
            return e, None

    hari = await tanggal_dokumen(c, T)

    async def um(n, ref):
        r = await CD.create_customer_deposit(req(), CreateCustomerDepositRequest(
            customer_id=str(acu["customer_id"]), customer_name=acu["nama"], amount=n, deposit_date=hari, payment_method=acu["payment_method"],
            account_id=acu["account_id"], bank_account_id=acu["bank_account_id"], reference=ref, notes=ref))
        return UUID(str(r["data"]["id"]))

    # ───────── BAGIAN A: ROLLBACK (satu koneksi, transaksi luar) ─────────
    class Kon:
        def __getattr__(s, n): return getattr(c, n)
        def transaction(s, **kw): return c.transaction()  # readonly pratinjau -> savepoint biasa di dalam transaksi luar

    class CMA:
        async def __aenter__(s): return Kon()
        async def __aexit__(s, *a): return False

    class PA:
        def acquire(s): return CMA()
    async def gpa(): return PA()
    CD.get_pool = gpa
    luar = c.transaction()
    await luar.start()
    try:
        u1 = await um(10000, "TES E2E AS-G10")
        k, h = f"TES-E2E-{uuid4()}", Response()
        e1, r1 = await galat(CD.update_customer_deposit(req(k), u1, UpdateCustomerDepositRequest(notes="TES E2E AS-G10 ubah"), Response()))
        e2, r2 = await galat(CD.update_customer_deposit(req(k), u1, UpdateCustomerDepositRequest(notes="TES E2E AS-G10 ubah"), h))
        eb, _ = await galat(CD.update_customer_deposit(req(k), u1, UpdateCustomerDepositRequest(notes="TES E2E AS-G10 lain"), Response()))
        n_upd = await c.fetchval("""SELECT count(*) FROM audit_logs WHERE tenant_id = $1 AND entity_id = $2 AND "eventType" = 'DEPOSIT_UPDATED'""", T, u1)
        cek(f"A PATCH: kunci sama 2x = replay, riwayat DEPOSIT_UPDATED SEKALI ({n_upd}); isi beda 409",
            r1 is not None and r2 == r1 and h.headers.get("X-Idempotent-Replay") == "true" and n_upd == 1
            and eb is not None and eb.status_code == 409, (e1 and e1.detail, e2 and e2.detail, n_upd, eb and eb.detail))
        # pratinjau ubah draf == PATCH (nol tulis)
        pv = (await CD.preview_edit_customer_deposit(req(), u1, UpdateCustomerDepositRequest(amount=11000)))["data"]
        am0 = await c.fetchval("SELECT amount FROM customer_deposits WHERE id = $1", u1)
        cek("A pratinjau ubah draf: can_proceed, changes == isi, after.amount 11000, DB tak berubah",
            pv["can_proceed"] and pv["changes"] == {"amount": 11000} and pv["after"].get("amount") == 11000 and D(str(am0)) == D("10000"),
            (pv, am0))
        # kunci SAMA lintas aksi: posting dengan kunci k (awalan DEPOSIT_POST beda dari DEPOSIT_UPDATE) -> JALAN; ulang = replay, jurnal SEKALI
        hp = Response()
        ep1, p1 = await galat(CD.post_customer_deposit(req(k), u1, Response()))
        ep2, p2 = await galat(CD.post_customer_deposit(req(k), u1, hp))
        nj = await c.fetchval(SQL_JURNAL_DP, T, str(u1))
        st = await c.fetchval("SELECT status FROM customer_deposits WHERE id = $1", u1)
        cek(f"A kunci SAMA lintas aksi (PATCH lalu posting) = posting JALAN; posting 2x = replay, jurnal SEKALI ({nj})",
            p1 is not None and p2 == p1 and hp.headers.get("X-Idempotent-Replay") == "true" and st == "posted" and nj == 1,
            (ep1 and ep1.detail, ep2 and ep2.detail, st, nj))
        e0, _ = await galat(CD.post_customer_deposit(req(), u1, Response()))
        cek("A tanpa kunci: posting ulang ditolak 400 seperti dulu", e0 is not None and e0.status_code == 400, e0 and e0.detail)
        # pratinjau ubah UM terposting = blok == galat PATCH
        pv2 = (await CD.preview_edit_customer_deposit(req(), u1, UpdateCustomerDepositRequest(notes="x")))["data"]
        ew, _ = await galat(CD.update_customer_deposit(req(), u1, UpdateCustomerDepositRequest(notes="x"), Response()))
        cek("A pratinjau ubah UM terposting = blok EDIT_REJECTED == galat PATCH 400 pesan sama",
            [b["code"] for b in pv2["blocks"]] == ["EDIT_REJECTED"] and not pv2["can_proceed"]
            and ew is not None and ew.status_code == 400 and ew.detail == pv2["blocks"][0]["message"], (pv2["blocks"], ew and ew.detail))
        # hapus 2x
        u2 = await um(5000, "TES E2E AS-G10")
        k3, hd = f"TES-E2E-{uuid4()}", Response()
        e1, d1 = await galat(CD.delete_customer_deposit(req(k3), u2, Response()))
        e2, d2 = await galat(CD.delete_customer_deposit(req(k3), u2, hd))
        ada = await c.fetchval("SELECT count(*) FROM customer_deposits WHERE id = $1", u2)
        cek("A hapus: kunci sama 2x = replay 200 (bukan 404), 0 baris", d1 is not None and d2 == d1 and hd.headers.get("X-Idempotent-Replay") == "true"
            and ada == 0, (e1 and e1.detail, e2 and e2.detail, ada))
    finally:
        await luar.rollback()
        sisa_a = await c.fetchval("SELECT count(*) FROM customer_deposits WHERE tenant_id = $1 AND reference = 'TES E2E AS-G10'", T)
        cek("A rollback: nol UM TES E2E dari bagian A", sisa_a == 0, sisa_a)

    # ───────── BAGIAN B: COMMIT 'TES E2E' -- PATCH || posting BERSAMAAN ─────────
    class CMB:
        async def __aenter__(s):
            s.k = await pool.acquire()
            return s.k
        async def __aexit__(s, *a):
            await pool.release(s.k)
            return False

    class PB:
        def acquire(s): return CMB()
    async def gpb(): return PB()
    CD.get_pool = gpb
    gl0, bk0 = dict(await c.fetch(SQL_GL, T)), dict(await c.fetch(SQL_BANK, T))
    ub = None
    try:
        ub = await um(10000, "TES E2E AS-G10 B")
        hasil = await asyncio.wait_for(asyncio.gather(
            galat(CD.update_customer_deposit(req(), ub, UpdateCustomerDepositRequest(amount=12345), Response())),
            galat(CD.post_customer_deposit(req(), ub, Response()))), timeout=30)
        (ep, rpch), (eo, rpost) = hasil
        dp = await c.fetchrow("SELECT status, amount, journal_id FROM customer_deposits WHERE id = $1", ub)
        debit = await c.fetchval("SELECT COALESCE(SUM(debit), 0) FROM journal_lines WHERE journal_id = $1", dp["journal_id"]) if dp["journal_id"] else None
        print(f"INFO B: PATCH {'OK' if rpch is not None else ep.status_code} posting {'OK' if rpost is not None else eo.status_code} "
              f"status {dp['status']} nominal {dp['amount']} debit jurnal {debit}", flush=True)
        cek("B PATCH || posting BERSAMAAN: tanpa macet, posting jalan, nominal dokumen == debit jurnal posting (tak pernah dokumen != jurnal)",
            rpost is not None and dp["status"] == "posted" and debit is not None and D(str(debit)) == D(str(dp["amount"]))
            and (rpch is not None or (ep.status_code == 400 and ep.detail == "Hanya uang muka berstatus Draf yang bisa diubah.")),
            (ep and ep.detail, eo and eo.detail, dict(dp), debit))
    except asyncio.TimeoutError:
        cek("B PATCH || posting BERSAMAAN: tanpa macet", False, "MACET > 30 dtk")
    finally:
        sisa = []
        if ub is not None:
            st = await c.fetchval("SELECT status FROM customer_deposits WHERE id = $1", ub)
            if st == "draft":
                e, _ = await galat(CD.delete_customer_deposit(req(), ub, Response()))
            elif st in ("posted", "partial"):
                e, _ = await galat(CD.void_customer_deposit(req(), ub, VoidCustomerDepositRequest(reason="TES E2E AS-G10 bersih"), Response()))
            else:
                e = None
            if e is not None:
                sisa.append(f"UM {ub}: {e.detail}")
        gl1, bk1 = dict(await c.fetch(SQL_GL, T)), dict(await c.fetch(SQL_BANK, T))
        beda = {k_: (gl0.get(k_), gl1.get(k_)) for k_ in set(gl0) | set(gl1) if D(str(gl0.get(k_) or 0)) != D(str(gl1.get(k_) or 0))}
        bedab = {k_: (bk0.get(k_), bk1.get(k_)) for k_ in set(bk0) | set(bk1) if D(str(bk0.get(k_) or 0)) != D(str(bk1.get(k_) or 0))}
        cek("B bersih lewat aplikasi: GL per akun + bank_transactions kaos sebelum == sesudah (posting + pembalik = net 0)",
            not beda and not bedab and not sisa, (beda, bedab, sisa))
        print("INFO sisa_manual B: UM TES E2E AS-G10 B berstatus void (dokumen uji, net 0): " + json.dumps(str(ub)))
        await c.close()
        await pool.close()
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
