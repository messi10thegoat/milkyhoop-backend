"""UJI NYATA SALDO-AWAL-VENDOR-A (25 Okt 2026), ROLLBACK-ONLY di kaos (TES E2E). Mode: baru [merah_debit]
V458 (DDL aditif) dijalankan DI DALAM transaksi rollback bila kolom belum ada (lock_timeout 3 dtk; transaksi singkat). Rute NYATA
set_vendor_opening_balance atas vendor kaos tanpa saldo awal, nominal 7.000, kunci K:
(1) tagihan pembuka: is_opening_balance, posted, ref SALDO AWAL, jatuh tempo == tanggal terbit == tanggal saldo awal, tanpa PPN;
(2) jurnal source BILL: Dr Modal Saldo Awal (peran EQUITY_OPENING_BALANCE) 7.000 / Cr Hutang Usaha (AP_TRADE) 7.000, seimbang, satu jurnal;
(3) compute_ap: baris tagihan itu outstanding +7.000 jatuh tempo = tanggal; saldo vendor (Σ, = daftar UI) naik TEPAT +7.000 (POSITIF);
(4) kunci K diulang = jawaban PERTAMA + X-Idempotent-Replay, tetap satu tagihan;  (5) kunci baru = 409 SALDO_AWAL_VENDOR_SUDAH_ADA;
(6) pratinjau batal (rencana_void, penentu void_bill): tanpa blok, pembalik Dr Hutang / Cr Modal;  (7) vendor ber-jurnal OB LAMA = 409;
rollback: nol tagihan pembuka TES E2E.
Kontrol merah (WAJIB FAIL): merah_debit = kode NYATA dari SALINAN pohon tanpa cabang tagihan pembuka di hitung_debit -> debit ke beban (2 gagal)."""
import asyncio, json, os, sys, time
from decimal import Decimal as D
from pathlib import Path
from uuid import uuid4
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

T = "kaos-biru-konveksi"
V458 = Path("/app/backend/migrations/V458__bill_saldo_awal_vendor.sql")
gagal = []


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:300]}"), flush=True)
    if not ok:
        gagal.append(nama)


async def main():
    from app.routers import vendors as RV, bills as RB
    from app.services import tagihan_aksi as TA
    from app.services.bills_service import BillsService
    from app.services.role_resolver import AccountRole
    from app.utils.tanggal_tenant import tanggal_dokumen
    c = await asyncpg.connect(os.environ["URL"])
    if not await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T):
        sys.exit(f"TENANT TAK ADA: {T}")
    uid = await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", T)

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
    import app.services.db_pool as DBP
    DBP.get_db_pool = gpa

    async def svc(): return BillsService(PA())
    RB.get_bills_service = svc

    def req(badan, kunci=None):
        b = json.dumps(badan).encode()
        async def receive(): return {"type": "http.request", "body": b, "more_body": False}
        hdr = [(b"content-type", b"application/json")] + ([(b"x-idempotency-key", kunci.encode())] if kunci else [])
        r = Request({"type": "http", "method": "POST", "path": "/", "headers": hdr, "query_string": b""}, receive)
        r.state.user = {"tenant_id": T, "user_id": str(uid), "role": "OWNER", "username": "TES E2E"}
        return r

    async def galat(coro):
        try:
            return None, await coro
        except HTTPException as e:
            return e, None

    saldo = lambda vid: c.fetchval("SELECT COALESCE(SUM(outstanding), 0) FROM compute_ap_outstanding($1) WHERE vendor_id = $2", T, vid)  # noqa: E731
    t_ddl = None
    luar = c.transaction()
    await luar.start()
    try:
        # Syarat MASTER 25 Okt: macet TAK PERNAH menahan kunci bills lama -- lock/statement/idle-in-transaction timeout di koneksi harness.
        await c.execute("SET LOCAL lock_timeout = '3s'")
        await c.execute("SET LOCAL statement_timeout = '30s'")
        await c.execute("SET LOCAL idle_in_transaction_session_timeout = '30s'")
        if not await c.fetchval("SELECT 1 FROM information_schema.columns WHERE table_name = 'bills' AND column_name = 'is_opening_balance'"):
            t_ddl = time.monotonic()
            await c.execute(V458.read_text())
            print("INFO V458 diterapkan DALAM rollback (kunci ACCESS EXCLUSIVE bills dipegang sampai rollback)", flush=True)
        hari = await tanggal_dokumen(c, T)
        vid = await c.fetchval("""SELECT v.id FROM vendors v WHERE v.tenant_id = $1 AND NOT EXISTS (SELECT 1 FROM journal_entries je
                                    WHERE je.tenant_id = v.tenant_id AND je.source_type = 'VENDOR_OPENING_BALANCE' AND je.source_id = v.id)
                                  ORDER BY v.created_at DESC LIMIT 1""", T)
        s0 = D(str(await saldo(vid)))
        k, h = f"TES-E2E-{uuid4()}", Response()
        badan = {"amount": 7000, "as_of_date": hari.isoformat(), "description": "TES E2E SALDO-AWAL-VENDOR-A"}
        e1, r1 = await galat(RV.set_vendor_opening_balance(req(badan, k), str(vid), Response()))
        cek("rute: saldo awal tercatat", r1 is not None and r1.get("success"), e1 and (e1.status_code, e1.detail))
        if r1 is None:
            return
        bid = r1["data"]["bill_id"]
        b = await c.fetchrow("SELECT * FROM bills WHERE id = $1::uuid", bid)
        cek("(1) tagihan pembuka posted, ref SALDO AWAL, jatuh tempo == terbit == tanggal, tanpa PPN",
            b["is_opening_balance"] and b["status_v2"] == "posted" and b["ref_no"] == "SALDO AWAL" and b["due_date"] == b["issue_date"] == hari
            and D(str(b["tax_amount"])) == 0 and D(str(b["grand_total"])) == D("7000"), dict(b))
        modal = await c.fetchval("SELECT account_id FROM account_roles WHERE tenant_id = $1 AND role_key = $2", T, AccountRole.EQUITY_OPENING_BALANCE)
        hutang = await c.fetchval("SELECT account_id FROM account_roles WHERE tenant_id = $1 AND role_key = $2", T, AccountRole.AP_TRADE)
        jr = await c.fetch("""SELECT je.id, je.source_type, jl.account_id, jl.debit, jl.credit FROM journal_entries je JOIN journal_lines jl ON jl.journal_id = je.id
                              WHERE je.tenant_id = $1 AND je.source_id = $2::uuid AND je.status = 'POSTED'""", T, bid)
        baris = {(r["account_id"], D(str(r["debit"])), D(str(r["credit"]))) for r in jr}
        cek("(2) jurnal BILL: Dr Modal Saldo Awal 7.000 / Cr Hutang Usaha 7.000, satu jurnal",
            len({r["id"] for r in jr}) == 1 and {r["source_type"] for r in jr} == {"BILL"}
            and baris == {(modal, D("7000"), D("0")), (hutang, D("0"), D("7000"))}, [(str(a), d, k_) for a, d, k_ in baris])
        ap = await c.fetchrow("SELECT outstanding, due_date FROM compute_ap_outstanding($1) WHERE bill_id = $2::uuid", T, bid)
        s1 = D(str(await saldo(vid)))
        cek(f"(3) compute_ap: tagihan +7.000 jatuh tempo {hari}; saldo vendor {s0} -> {s1} (+7.000, POSITIF)",
            ap is not None and D(str(ap["outstanding"])) == D("7000") and ap["due_date"] == hari and s1 - s0 == D("7000"), (dict(ap or {}), s0, s1))
        e2, r2 = await galat(RV.set_vendor_opening_balance(req(badan, k), str(vid), h))
        n = await c.fetchval("SELECT count(*) FROM bills WHERE tenant_id = $1 AND vendor_id = $2 AND is_opening_balance", T, vid)
        cek("(4) kunci K diulang = jawaban PERTAMA + X-Idempotent-Replay, tetap 1 tagihan",
            r2 == r1 and h.headers.get("X-Idempotent-Replay") == "true" and n == 1, (e2 and e2.detail, n))
        e3, _ = await galat(RV.set_vendor_opening_balance(req(badan, f"TES-E2E-{uuid4()}"), str(vid), Response()))
        cek("(5) kunci baru = 409 SALDO_AWAL_VENDOR_SUDAH_ADA", e3 is not None and e3.status_code == 409
            and e3.detail.get("code") == "SALDO_AWAL_VENDOR_SUDAH_ADA", e3 and (e3.status_code, e3.detail))
        rv = await TA.rencana_void(c, T, bid)
        pb = {(r["account_id"], D(str(r["debit"])), D(str(r["credit"]))) for r in rv["baris"]}
        cek("(6) pratinjau batal: tanpa blok, pembalik Dr Hutang / Cr Modal",
            not rv["blocks"] and pb == {(str(hutang), D("7000"), D("0")), (str(modal), D("0"), D("7000"))}, (rv["blocks"], pb))
        lama = await c.fetchval("SELECT source_id FROM journal_entries WHERE tenant_id = $1 AND source_type = 'VENDOR_OPENING_BALANCE' "
                                "AND status = 'POSTED' AND reversed_by_id IS NULL AND reversal_of_id IS NULL LIMIT 1", T)
        if lama:
            e7, _ = await galat(RV.set_vendor_opening_balance(req(badan), str(lama), Response()))
            cek("(7) vendor ber-jurnal OB LAMA (terbalik, belum dibalik) = 409", e7 is not None and e7.status_code == 409, e7 and e7.detail)
    finally:
        await luar.rollback()
        if t_ddl is not None:
            print(f"INFO lama kunci bills (DDL V458 -> rollback): {time.monotonic() - t_ddl:.2f} dtk", flush=True)
        ada = await c.fetchval("SELECT 1 FROM information_schema.columns WHERE table_name = 'bills' AND column_name = 'is_opening_balance'")
        sisa = await c.fetchval("SELECT count(*) FROM bills WHERE tenant_id = $1 AND notes = 'TES E2E SALDO-AWAL-VENDOR-A'", T)
        cek(f"rollback: nol tagihan pembuka TES E2E ({sisa}); kolom V458 {'ADA (sudah diterapkan)' if ada else 'ikut dibatalkan'}", sisa == 0, sisa)
        await c.close()
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
