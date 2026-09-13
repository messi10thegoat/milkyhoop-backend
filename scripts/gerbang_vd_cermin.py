"""GERBANG cermin bank uang muka vendor (R9). Satu transaksi luar; tiap skenario di savepoint; hasil di memori; ROLLBACK.
argv: <path vendor_deposits.py> <lama|baru|sabotase_tanda|sabotase_void>
Ukuran R9: check_bank_sync_health(tenant) — Σ gap dan Σ jurnal yatim; yang di-assert = SELISIH terhadap garis dasar
(tenant membawa riwayat yang dipatok; unit ini tak menyentuhnya).
  A  posting via rekening bank          -> Δgap 0, Δyatim 0, 1 cermin withdrawal −X terkait jurnal
  B  posting lalu void                  -> sesudah void Δgap 0, cermin penegasi terkait jurnal pembalik
  C  posting lalu refund sebagian       -> Δgap 0, cermin deposit +R terkait jurnal refund
  D  posting TANPA rekening (Kas bawaan) -> Δgap 0 (cermin ada iff Kas tertaut bank_accounts)
lama: A & C wajib merah (celah R9). sabotase_tanda: cermin posting +X -> A merah. sabotase_void: pembalik dibuang -> B merah.
"""
import asyncio
import importlib.util
import os
import re
import sys
from datetime import date

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402
from starlette.requests import Request  # noqa: E402

PATH, MODE = sys.argv[1], sys.argv[2]
T = "kaos-biru-konveksi"
hasil = []


def catat(s, u, ok, k=""):
    hasil.append((s, u, bool(ok), str(k)[:220]))


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    import app.services.db_pool as dbp

    class FakePool:
        def acquire(self, *a, **k):
            class C:
                async def __aenter__(s):
                    return conn
                async def __aexit__(s, *e):
                    return False
            return C()
        async def release(self, c):
            return None

    async def fake(*a, **k):
        return FakePool()
    dbp.get_db_pool = fake
    src = open(PATH, encoding="utf-8").read()
    if MODE == "sabotase_tanda":
        A = 'transaction_type="withdrawal",\n                        amount=-vd["amount"],'
        if src.count(A) != 1:
            print("ALAT: jangkar sabotase_tanda"); sys.exit(3)
        src = src.replace(A, 'transaction_type="withdrawal",\n                        amount=vd["amount"],')
    if MODE == "sabotase_void":
        A = "                    await create_reversal_bank_transaction("
        if src.count(A) != 1:
            print("ALAT: jangkar sabotase_void"); sys.exit(3)
        src = src.replace(A, "                    pass\n                    if False: await create_reversal_bank_transaction(")
    open("/tmp/vd_cermin_gerbang.py", "w", encoding="utf-8").write(src)
    import app.routers  # noqa: F401
    spec = importlib.util.spec_from_file_location("app.routers.vd_cermin_gerbang", "/tmp/vd_cermin_gerbang.py")
    m = importlib.util.module_from_spec(spec); sys.modules[spec.name] = m; spec.loader.exec_module(m)
    import app.schemas.vendor_deposits as s

    uid = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)
    rq = lambda: Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b"",  # noqa: E731
                          "state": {"user": {"tenant_id": T, "user_id": str(uid)}}})
    vendor = await conn.fetchval("SELECT id FROM vendors WHERE tenant_id=$1 LIMIT 1", T)
    ba = await conn.fetchrow("SELECT id, coa_id FROM bank_accounts WHERE tenant_id=$1 AND is_active ORDER BY account_name LIMIT 1", T)
    kas_bank = await conn.fetchval("""SELECT ba.id FROM bank_accounts ba JOIN chart_of_accounts c ON c.id=ba.coa_id
        WHERE ba.tenant_id=$1 AND c.account_code='1-10100'""", T)
    if not (vendor and ba):
        print("TAK SAH: vendor / rekening bank tak ada"); sys.exit(2)

    async def r9():
        rows = await conn.fetch("SELECT gap, orphan_journals, orphan_bank_txns FROM check_bank_sync_health($1)", T)
        return (sum((r["gap"] or 0) for r in rows), sum((r["orphan_journals"] or 0) for r in rows), sum((r["orphan_bank_txns"] or 0) for r in rows))

    async def buat_posting(nominal, bank_id):
        r = await m.create_vendor_deposit(rq(), s.VendorDepositCreate(deposit_date=date.today(), vendor_id=vendor, amount=nominal,
                                          payment_method="transfer", bank_account_id=bank_id, reference="gerbang-vd-cermin", notes="gerbang"))
        p = await m.post_vendor_deposit(rq(), r.id)
        return r.id, p.journal_id

    luar = conn.transaction(); await luar.start()
    try:
        dasar = await r9()
        print("garis dasar R9 (gap, yatim_jurnal, yatim_txn):", dasar)

        # A
        sp = conn.transaction(); await sp.start()
        did, jid = await buat_posting(7000, ba["id"])
        a = await r9()
        cermin = await conn.fetch("SELECT bank_account_id, amount, transaction_type FROM bank_transactions WHERE journal_id=$1", jid)
        await sp.rollback()
        catat("A POSTING", "Δgap R9 = 0 dan Δjurnal yatim = 0 sesudah posting via rekening bank", a[0] == dasar[0] and a[1] == dasar[1], (dasar, a))
        catat("A POSTING", "tepat 1 cermin: rekening yang dikredit, withdrawal −7.000, terkait jurnal",
              len(cermin) == 1 and cermin[0]["bank_account_id"] == ba["id"] and cermin[0]["amount"] == -7000 and cermin[0]["transaction_type"] == "withdrawal",
              [tuple(c) for c in cermin])

        # B
        sp = conn.transaction(); await sp.start()
        did, jid = await buat_posting(6000, ba["id"])
        b1 = await r9()
        await m.void_vendor_deposit(rq(), did)
        b2 = await r9()
        rev = await conn.fetchval("SELECT reversed_by_id FROM journal_entries WHERE id=$1", jid)
        cermin_rev = await conn.fetch("SELECT amount FROM bank_transactions WHERE journal_id=$1", rev) if rev else []
        await sp.rollback()
        catat("B VOID", "sesudah posting Δgap 0", b1[0] == dasar[0], (dasar, b1))
        catat("B VOID", "sesudah void Δgap 0 dan cermin penegasi +6.000 terkait jurnal pembalik",
              b2[0] == dasar[0] and b2[1] == dasar[1] and [c["amount"] for c in cermin_rev] == [6000], (b2, [c["amount"] for c in cermin_rev]))

        # C
        sp = conn.transaction(); await sp.start()
        did, jid = await buat_posting(8000, ba["id"])
        # kode lama mati di respons refund (kolom hantu bank_accounts.name) SESUDAH jurnal ditulis -> tangkap, jangan batalkan gerbang
        sp2 = conn.transaction(); await sp2.start()
        try:
            rf = await m.refund_vendor_deposit(rq(), did, s.VendorDepositRefundCreate(refund_date=date.today(), amount=3000, bank_account_id=ba["id"], reference="gerbang"))
            await sp2.commit()
            c1 = await r9()
            jref = await conn.fetchval("SELECT journal_id FROM vendor_deposit_refunds WHERE id=$1", rf.id)
            cermin_ref = await conn.fetch("SELECT amount, transaction_type FROM bank_transactions WHERE journal_id=$1", jref)
        except Exception as e:  # noqa: BLE001
            await sp2.rollback()
            c1, cermin_ref = (f"refund GAGAL: {type(e).__name__}: {e}", None, None), []
        await sp.rollback()
        catat("C REFUND", "sesudah posting + refund Δgap 0 dan Δyatim 0", c1[0] == dasar[0] and c1[1] == dasar[1], (dasar, c1))
        catat("C REFUND", "cermin refund: deposit +3.000 terkait jurnal refund", [tuple(c) for c in cermin_ref] == [(3000, "deposit")], [tuple(c) for c in cermin_ref])

        # D
        sp = conn.transaction(); await sp.start()
        did, jid = await buat_posting(5000, None)
        d1 = await r9()
        n_cermin = await conn.fetchval("SELECT count(*) FROM bank_transactions WHERE journal_id=$1", jid)
        await sp.rollback()
        catat("D KAS", f"posting tanpa rekening (Kas bawaan, tertaut bank={bool(kas_bank)}) -> Δgap 0, cermin {'1' if kas_bank else '0'}",
              d1[0] == dasar[0] and n_cermin == (1 if kas_bank else 0), (dasar, d1, n_cermin))
    finally:
        await luar.rollback()
        sisa = await conn.fetchval("SELECT count(*) FROM vendor_deposits WHERE reference='gerbang-vd-cermin'")
        await conn.close()
    catat("ROLLBACK", "nol uang muka sintetis tersisa", sisa == 0, sisa)

    badan = open(__file__, encoding="utf-8").read().split("async def main():", 1)[1]
    harap = len(re.findall(r"^\s+catat\(", badan, re.M))
    for s_, u, ok, k in hasil:
        print(("[H] " if ok else "[X] ") + f"{s_:10} {u}  | {k}")
    g = [h[1] for h in hasil if not h[2]]
    print(f"\nMODE={MODE} gagal={len(g)} total={len(hasil)} harap={harap}")
    if MODE == "baru":
        sys.exit(0 if not g and len(hasil) == harap else 1)
    if MODE == "lama":
        ok = any(x.startswith("Δgap R9 = 0") for x in g) and any("posting + refund" in x for x in g)
        print("[lama] ->", "CELAH R9 TERBUKTI" if ok else "TAK MERAH"); sys.exit(0 if ok else 1)
    if MODE == "sabotase_tanda":
        ok = any(x.startswith("Δgap R9 = 0") for x in g)
    else:
        ok = any(x.startswith("sesudah void") for x in g) and not any(x.startswith("Δgap R9 = 0") for x in g)
    print(f"[{MODE}] ->", "TERTANGKAP" if ok else "LOLOS", g); sys.exit(0 if ok else 1)


asyncio.run(main())
