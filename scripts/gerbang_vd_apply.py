"""GERBANG terapkan uang muka vendor (kolom hantu). Satu transaksi luar, ROLLBACK; hasil di memori Python.
argv: <path vendor_deposits.py> <label: lama|baru|sabotase_tanpa_cache|sabotase_cache_ganda>
  lama   : modul sebelum tambal -> butir apply MERAH (KeyError bill_number)
  baru   : semua hijau
  sabotase_tanpa_cache : UPDATE cache tagihan dibuang (apply tetap 200) -> butir cache WAJIB merah
  sabotase_cache_ganda : cache ditambah 2x nominal (apply tetap 200)     -> butir cache WAJIB merah
Uang muka vendor SINTETIS (tak ada data historis: vendor_deposits = 0 baris). Subjek kosong -> exit 2 TAK SAH.
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
T, TB = "kaos-biru-konveksi", "grapgrap-manado"
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
    if MODE.startswith("sabotase"):
        A = "amount_paid = COALESCE(amount_paid, 0) + $2,"
        if src.count(A) != 1:
            print("ALAT: jangkar sabotase"); sys.exit(3)
        if MODE == "sabotase_tanpa_cache":
            src = src.replace(A, "amount_paid = COALESCE(amount_paid, 0) + 0 * $2,")
            src = src.replace("WHEN COALESCE(amount_paid, 0) + $2 >= amount THEN 'paid'\n                        ELSE 'partial'",
                              "WHEN false THEN 'paid'\n                        ELSE status")
        else:
            src = src.replace(A, "amount_paid = COALESCE(amount_paid, 0) + 2 * $2,")
    open("/tmp/vd_gerbang.py", "w", encoding="utf-8").write(src)
    import app.routers  # noqa: F401
    spec = importlib.util.spec_from_file_location("app.routers.vd_gerbang", "/tmp/vd_gerbang.py")
    m = importlib.util.module_from_spec(spec); sys.modules[spec.name] = m; spec.loader.exec_module(m)
    import app.schemas.vendor_deposits as s

    uid = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)
    rq = lambda: Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b"",  # noqa: E731
                          "state": {"user": {"tenant_id": T, "user_id": str(uid)}}})
    bill = await conn.fetchrow("""SELECT o.bill_id, o.outstanding, b.vendor_id, b.invoice_number FROM compute_ap_outstanding($1) o
        JOIN bills b ON b.id=o.bill_id WHERE o.outstanding >= 3000 AND o.outstanding = trunc(o.outstanding)
          AND b.status IN ('posted','partial') AND COALESCE(b.amount_paid,0) = 0 AND o.outstanding = b.amount
        ORDER BY o.outstanding LIMIT 1""", T)
    ba = await conn.fetchrow("SELECT id FROM bank_accounts WHERE tenant_id=$1 AND is_active ORDER BY account_name LIMIT 1", T)
    bill_tb = await conn.fetchval("SELECT id FROM bills WHERE tenant_id=$1 LIMIT 1", TB)
    if not (bill and ba and bill_tb):
        print("TAK SAH: subjek tak lengkap", bill, ba, bill_tb); sys.exit(2)
    total = int(bill["outstanding"])
    print("subjek", dict(bill))

    async def ukur():
        gl = await conn.fetchval("""SELECT COALESCE(SUM(jl.credit-jl.debit),0) FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id
            JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id=$1 AND je.status='POSTED' AND c.account_type='PAYABLE'""", T)
        out = await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ap_outstanding($1) WHERE bill_id=$2", T, bill["bill_id"])
        b = await conn.fetchrow("SELECT amount_paid, status FROM bills WHERE id=$1", bill["bill_id"])
        return gl, out, b["amount_paid"], b["status"]

    async def panggil(coro):
        try:
            return 200, await coro
        except Exception as e:  # noqa: BLE001
            return getattr(e, "status_code", type(e).__name__), str(getattr(e, "detail", e))

    luar = conn.transaction(); await luar.start()
    try:
        r = await m.create_vendor_deposit(rq(), s.VendorDepositCreate(deposit_date=date.today(), vendor_id=bill["vendor_id"], amount=total + 5000,
                                          payment_method="transfer", bank_account_id=ba["id"], reference="gerbang-vd", notes="gerbang-vd"))
        did = r.id
        await m.post_vendor_deposit(rq(), did)
        catat("PRASYARAT", "uang muka vendor sintetis dibuat & dibukukan", True, did)

        g0, o0, p0, s0 = await ukur()
        k, r = await panggil(m.apply_vendor_deposit(rq(), did, s.ApplyDepositRequest(bill_id=bill["bill_id"], amount=1000)))
        g1, o1, p1, s1 = await ukur()
        catat("SEBAGIAN", "terapkan 1.000 -> 200", k == 200, (k, r if k != 200 else ""))
        catat("SEBAGIAN", "outstanding compute turun TEPAT 1.000", o0 - o1 == 1000, (o0, o1))
        catat("SEBAGIAN", "GL hutang turun TEPAT 1.000 (Dr hutang)", g0 - g1 == 1000, (g0, g1))
        catat("SEBAGIAN", "cache amount_paid +1.000, status partial", p1 - p0 == 1000 and s1 == "partial", (p0, p1, s1))
        if k == 200:
            catat("SEBAGIAN", "respons: bill_number == invoice_number, bill_remaining == outstanding compute",
                  r.bill_number == bill["invoice_number"] and r.bill_remaining == int(o1), (r.bill_number, r.bill_remaining, o1))
        else:
            catat("SEBAGIAN", "respons: bill_number == invoice_number, bill_remaining == outstanding compute", False, "apply gagal")
        vd = await conn.fetchrow("SELECT status, applied_amount, remaining_amount, (SELECT count(*) FROM vendor_deposit_applications WHERE vendor_deposit_id=$1) n FROM vendor_deposits WHERE id=$1", did)
        catat("SEBAGIAN", "uang muka: 1 aplikasi, applied 1.000, status partial", vd["n"] == 1 and vd["applied_amount"] == 1000 and vd["status"] == "partial", tuple(vd))

        # melebihi sisa diuji SEBELUM pelunasan: sesudah lunas, 400 datang dari 'Bill must be posted' (run pertama) -> alasan salah
        k, r = await panggil(m.apply_vendor_deposit(rq(), did, s.ApplyDepositRequest(bill_id=bill["bill_id"], amount=total - 1000 + 1)))
        catat("TOLAK", "melebihi sisa tagihan (tagihan partial) -> 400 dgn alasan 'exceeds bill balance'", k == 400 and "exceeds bill balance" in str(r), (k, r))

        k, r = await panggil(m.apply_vendor_deposit(rq(), did, s.ApplyDepositRequest(bill_id=bill["bill_id"], amount=total - 1000)))
        g2, o2, p2, s2 = await ukur()
        catat("LUNAS", "terapkan sisa -> 200, outstanding 0, cache amount_paid == nilai tagihan, status paid",
              k == 200 and o2 == 0 and p2 == total and s2 == "paid", (k, r if k != 200 else "", o2, p2, s2))

        k, r = await panggil(m.apply_vendor_deposit(rq(), did, s.ApplyDepositRequest(bill_id=bill_tb, amount=1000)))
        # BUKAN pembeda lama/baru: kode lama pun menolak (vendor_id tagihan tenant lain tak cocok); tenant_id = pagar tambahan
        catat("TOLAK", "tagihan tenant lain -> 400 (sabuk pengaman; kode lama juga 400 lewat vendor)", k == 400, (k, r))
    finally:
        await luar.rollback()
        sisa = await conn.fetchval("SELECT count(*) FROM vendor_deposits WHERE reference='gerbang-vd'")
        await conn.close()
    catat("ROLLBACK", "nol uang muka sintetis tersisa", sisa == 0, sisa)

    badan = open(__file__, encoding="utf-8").read().split("async def main():", 1)[1]
    harap = len(re.findall(r"^\s+catat\(", badan, re.M)) - 1   # dua situs 'respons' saling menggantikan (if/else)
    for s_, u, ok, k_ in hasil:
        print(("[H] " if ok else "[X] ") + f"{s_:10} {u}  | {k_}")
    g = [h for h in hasil if not h[2]]
    print(f"\nMODE={MODE} gagal={len(g)} total={len(hasil)} harap={harap}")
    if MODE == "baru":
        sys.exit(0 if not g and len(hasil) == harap else 1)
    if MODE == "lama":
        ok = any(h[1] == "terapkan 1.000 -> 200" for h in g)
        print("[lama] ->", "MERAH PADA KODE LAMA" if ok else "TAK MERAH")
        sys.exit(0 if ok else 1)
    merah = [h[1] for h in g]
    ok = any("cache amount_paid +1.000" in x for x in merah) and "terapkan 1.000 -> 200" not in merah
    print(f"[{MODE}] ->", "TERTANGKAP" if ok else "LOLOS", merah)
    sys.exit(0 if ok else 1)


asyncio.run(main())
