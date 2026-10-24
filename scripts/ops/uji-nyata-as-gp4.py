"""UJI NYATA AS-GP4 (24 Okt 2026) UM Pembelian, kaos. Mode: baru [merah_idem|merah_kunci]
BAGIAN A (ROLLBACK, TES E2E): PATCH/delete/void/unapply 2x kunci sama = replay + DB tertulis SEKALI; isi beda 409; kunci SAMA lintas aksi
(PATCH lalu post dengan kunci sama) = KEDUANYA jalan (awalan beda); pratinjau ubah == tulis (blok BUKAN_DRAF pada UM terposting == galat PATCH).
BAGIAN B (COMMIT 'TES E2E', aturan tetap pemilik 30 Sep; syarat MASTER): UM draf TES E2E -> PATCH nominal || post BERSAMAAN (2 koneksi) ->
tanpa macet (batas 30 dtk), satu menang; bila post menang PATCH dapat blok lama 400 "Can only update draft deposits"; nominal dokumen ==
debit jurnal posting (tak pernah dokumen != jurnal). Bersih: void lewat APLIKASI (finally); GL/AP per akun kaos sebelum == sesudah.
Kontrol merah (WAJIB FAIL): merah_idem = kunci dibuang (A gagal); merah_kunci = kode NYATA dari SALINAN pohon tanpa kunci+FOR UPDATE di PATCH
dan tanpa syarat status di UPDATE + jeda 0,8 dtk (runner memasang salinan) -> B: dokumen != jurnal."""
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


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:300]}"), flush=True)
    if not ok:
        gagal.append(nama)


async def main():
    from app.routers import vendor_deposits as RV
    from app.schemas.vendor_deposits import VendorDepositCreate, VendorDepositUpdate, VoidDepositRequest
    from app.services import idem_buat
    from app.utils.tanggal_tenant import tanggal_dokumen
    pool = await asyncpg.create_pool(os.environ["URL"], min_size=2, max_size=6)
    c = await asyncpg.connect(os.environ["URL"])
    if not await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T):
        sys.exit(f"TENANT TAK ADA: {T}")
    if MERAH == "merah_idem":
        RV._kunci_idem = lambda request: None
    uid = await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", T)

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

    def dt(x): return x.model_dump(mode="json") if hasattr(x, "model_dump") else x
    hari = await tanggal_dokumen(c, T)
    vendor = await c.fetchval("SELECT id FROM vendors WHERE tenant_id = $1 ORDER BY name LIMIT 1", T)
    bank = await c.fetchval("SELECT id FROM bank_accounts WHERE tenant_id = $1 AND is_active ORDER BY is_default DESC NULLS LAST, created_at LIMIT 1", T)

    # ───────── BAGIAN A: ROLLBACK (satu koneksi, transaksi luar) ─────────
    class Kon:
        def __getattr__(s, n): return getattr(c, n)
        def transaction(s, **kw): return c.transaction()

    class CMA:
        async def __aenter__(s): return Kon()
        async def __aexit__(s, *a): return False

    class PA:
        def acquire(s): return CMA()
    async def gpa(): return PA()
    RV.get_pool = gpa
    luar = c.transaction()
    await luar.start()
    try:
        async def ump(n):
            r = dt(await RV.create_vendor_deposit(req(), VendorDepositCreate(deposit_date=hari, vendor_id=vendor, amount=D(n), bank_account_id=bank,
                                                                            reference="TES E2E AS-GP4", notes="TES E2E AS-GP4")))
            return UUID(str(r.get("id") or r["data"]["id"]))
        u1 = await ump("10000")
        k = f"TES-E2E-{uuid4()}"
        h = Response()
        e1, r1 = await galat(RV.update_vendor_deposit(req(k), u1, VendorDepositUpdate(notes="TES E2E ubah"), Response()))
        e2, r2 = await galat(RV.update_vendor_deposit(req(k), u1, VendorDepositUpdate(notes="TES E2E ubah"), h))
        eb, _ = await galat(RV.update_vendor_deposit(req(k), u1, VendorDepositUpdate(notes="TES E2E lain"), Response()))
        cek("A PATCH: kunci sama 2x = replay; isi beda 409", r1 is not None and dt(r2) == dt(r1) and h.headers.get("X-Idempotent-Replay") == "true"
            and eb is not None and eb.status_code == 409, (e1 and e1.detail, e2 and e2.detail, eb and eb.detail))
        # kunci SAMA lintas aksi: post dengan kunci k (awalan VD_POST beda dari UMV_UPDATE) -> JALAN
        ep, rp = await galat(RV.post_vendor_deposit(req(k), u1, Response()))
        st = await c.fetchval("SELECT status FROM vendor_deposits WHERE id = $1", u1)
        cek("A kunci SAMA lintas aksi (PATCH lalu post): post JALAN, status posted", rp is not None and st == "posted", (ep and ep.detail, st))
        pv = (await RV.preview_edit_vendor_deposit(req(), u1, VendorDepositUpdate(notes="x")))["data"]
        ew, _ = await galat(RV.update_vendor_deposit(req(), u1, VendorDepositUpdate(notes="x"), Response()))
        cek("A pratinjau ubah UM terposting = blok BUKAN_DRAF == galat PATCH 400 pesan sama", [b["code"] for b in pv["blocks"]] == ["BUKAN_DRAF"]
            and ew is not None and ew.status_code == 400 and ew.detail == pv["blocks"][0]["message"], (pv["blocks"], ew and ew.detail))
        # void 2x
        k2, hv = f"TES-E2E-{uuid4()}", Response()
        n_rev0 = await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1 AND reversal_of_id = (SELECT journal_id FROM vendor_deposits WHERE id = $2)", T, u1)
        e1, v1 = await galat(RV.void_vendor_deposit(req(k2), u1, VoidDepositRequest(reason="TES E2E batal"), Response()))
        e2, v2 = await galat(RV.void_vendor_deposit(req(k2), u1, VoidDepositRequest(reason="TES E2E batal"), hv))
        n_rev = await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1 AND reversal_of_id = (SELECT journal_id FROM vendor_deposits WHERE id = $2)", T, u1)
        cek(f"A void: kunci sama 2x = replay, pembalik lahir SEKALI ({n_rev0}->{n_rev})", v1 is not None and dt(v2) == dt(v1)
            and hv.headers.get("X-Idempotent-Replay") == "true" and n_rev == n_rev0 + 1, (e1 and e1.detail, e2 and e2.detail, n_rev))
        # delete 2x
        u2 = await ump("5000")
        k3, hd = f"TES-E2E-{uuid4()}", Response()
        e1, d1 = await galat(RV.delete_vendor_deposit(req(k3), u2, Response()))
        e2, d2 = await galat(RV.delete_vendor_deposit(req(k3), u2, hd))
        ada = await c.fetchval("SELECT count(*) FROM vendor_deposits WHERE id = $1", u2)
        cek("A delete: kunci sama 2x = replay 200 (bukan 404), 0 baris", d1 is not None and d2 == d1 and hd.headers.get("X-Idempotent-Replay") == "true"
            and ada == 0, (e1 and e1.detail, e2 and e2.detail, ada))
        # apply -> unapply 2x (syarat MASTER: lepas NYATA, tercatat SEKALI, sisa tagihan/AP sebelum == sesudah)
        tg = await c.fetchrow("""SELECT bill_id, vendor_id, outstanding FROM compute_ap_outstanding($1)
                                 WHERE bill_id IS NOT NULL AND outstanding >= 2000 ORDER BY outstanding DESC LIMIT 1""", T)
        cek("A ada tagihan kaos terbuka untuk diterapkan", tg is not None, tg)
        if tg:
            sisa_tg = lambda: c.fetchval("SELECT COALESCE(SUM(outstanding), 0) FROM compute_ap_outstanding($1) WHERE bill_id = $2", T, tg["bill_id"])  # noqa: E731
            ap = lambda: c.fetchval("SELECT COALESCE(SUM(outstanding), 0) FROM compute_ap_outstanding($1)", T)  # noqa: E731
            r = dt(await RV.create_vendor_deposit(req(), VendorDepositCreate(deposit_date=hari, vendor_id=tg["vendor_id"], amount=D("1500"),
                                                                            bank_account_id=bank, reference="TES E2E AS-GP4", notes="TES E2E AS-GP4 terap")))
            u3 = UUID(str(r.get("id") or r["data"]["id"]))
            await RV.post_vendor_deposit(req(), u3, Response())
            s0, ap0 = D(str(await sisa_tg())), D(str(await ap()))
            from app.schemas.vendor_deposits import ApplyDepositRequest
            ra = dt(await RV.apply_vendor_deposit(req(), u3, ApplyDepositRequest(bill_id=tg["bill_id"], amount=D("1000")), Response()))
            app_id = UUID(str(ra.get("application_id") or ra["data"]["application_id"]))
            s1 = D(str(await sisa_tg()))
            k4, hu = f"TES-E2E-{uuid4()}", Response()
            e1, w1 = await galat(RV.unapply_vendor_deposit(req(k4), u3, app_id, Response()))
            e2, w2 = await galat(RV.unapply_vendor_deposit(req(k4), u3, app_id, hu))
            n_rev = await c.fetchval("SELECT count(*) FROM vendor_deposit_applications WHERE id = $1 AND status = 'reversed'", app_id)
            n_jur = await c.fetchval("""SELECT count(*) FROM journal_entries WHERE tenant_id = $1 AND reversal_of_id =
                                        (SELECT journal_id FROM vendor_deposit_applications WHERE id = $2)""", T, app_id)
            s2, ap2 = D(str(await sisa_tg())), D(str(await ap()))
            cek(f"A unapply 2x kunci sama = replay; penerapan dilepas SEKALI (status reversed {n_rev}, jurnal pembalik {n_jur}); sisa tagihan "
                f"{s0}->{s1}->{s2} & AP sebelum == sesudah", w1 is not None and dt(w2) == dt(w1) and hu.headers.get("X-Idempotent-Replay") == "true"
                and n_rev == 1 and n_jur <= 1 and s1 == s0 - 1000 and s2 == s0 and ap2 == ap0,
                (e1 and e1.detail, e2 and e2.detail, n_rev, n_jur, s0, s1, s2, ap0, ap2))
    finally:
        await luar.rollback()
        sisa_a = await c.fetchval("SELECT count(*) FROM vendor_deposits WHERE tenant_id = $1 AND reference = 'TES E2E AS-GP4' AND notes LIKE 'TES E2E%'", T)
        cek("A rollback: nol UM TES E2E dari bagian A", sisa_a == 0, sisa_a)

    # ───────── BAGIAN B: COMMIT 'TES E2E' -- PATCH || post BERSAMAAN ─────────
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
    RV.get_pool = gpb
    gl0 = dict(await c.fetch(SQL_GL, T))
    ub = None
    try:
        r = dt(await RV.create_vendor_deposit(req(), VendorDepositCreate(deposit_date=hari, vendor_id=vendor, amount=D("10000"), bank_account_id=bank,
                                                                        reference="TES E2E AS-GP4 B", notes="TES E2E AS-GP4 B")))
        ub = UUID(str(r.get("id") or r["data"]["id"]))
        hasil = await asyncio.wait_for(asyncio.gather(
            galat(RV.update_vendor_deposit(req(), ub, VendorDepositUpdate(amount=D("12345")), Response())),
            galat(RV.post_vendor_deposit(req(), ub, Response()))), timeout=30)
        (ep, rpch), (eo, rpost) = hasil
        vd = await c.fetchrow("SELECT status, amount, journal_id FROM vendor_deposits WHERE id = $1", ub)
        debit = await c.fetchval("SELECT COALESCE(SUM(debit), 0) FROM journal_lines WHERE journal_id = $1", vd["journal_id"]) if vd["journal_id"] else None
        print(f"INFO B: PATCH {'OK' if rpch is not None else ep.status_code} post {'OK' if rpost is not None else eo.status_code} "
              f"status {vd['status']} nominal {vd['amount']} debit jurnal {debit}", flush=True)
        cek("B PATCH || post BERSAMAAN: tanpa macet, post jalan, nominal dokumen == debit jurnal posting (tak pernah dokumen != jurnal)",
            rpost is not None and vd["status"] == "posted" and debit is not None and D(str(debit)) == D(str(vd["amount"]))
            and (rpch is not None or (ep.status_code == 400 and ep.detail == "Can only update draft deposits")),
            (ep and ep.detail, eo and eo.detail, dict(vd), debit))
    except asyncio.TimeoutError:
        cek("B PATCH || post BERSAMAAN: tanpa macet", False, "MACET > 30 dtk")
    finally:
        sisa = []
        if ub is not None:
            st = await c.fetchval("SELECT status FROM vendor_deposits WHERE id = $1", ub)
            if st == "draft":
                e, _ = await galat(RV.delete_vendor_deposit(req(), ub, Response()))
            elif st in ("posted", "partial"):
                e, _ = await galat(RV.void_vendor_deposit(req(), ub, VoidDepositRequest(reason="TES E2E AS-GP4 bersih"), Response()))
            else:
                e = None
            if e is not None:
                sisa.append(f"UM {ub}: {e.detail}")
        gl1 = dict(await c.fetch(SQL_GL, T))
        beda = {k_: (gl0.get(k_), gl1.get(k_)) for k_ in set(gl0) | set(gl1) if D(str(gl0.get(k_) or 0)) != D(str(gl1.get(k_) or 0))}
        cek("B bersih lewat aplikasi: GL/AP per akun kaos sebelum == sesudah (posting + pembalik = net 0)", not beda and not sisa, (beda, sisa))
        print("INFO sisa_manual B: UM TES E2E AS-GP4 B berstatus void (dokumen uji, net 0): " + json.dumps(str(ub)))
        await c.close()
        await pool.close()
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
