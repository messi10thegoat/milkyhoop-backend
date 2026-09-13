"""GERBANG unit (2) batalkan penerapan nota kredit. Satu transaksi luar, savepoint per skenario, hasil di memori, ROLLBACK.
argv: <dir modul: credit_notes.py sales_invoices.py schemas_credit_notes.py | '-' untuk modul hidup> <mode>
  mode: baru | hidup | lama | sabotase_pemicu | sabotase_kaitan | sabotase_periode
  baru/sabotase_*: badan V249 dipasang di transaksi (sabotase_pemicu: tanpa perubahan pemicu)
  hidup: V249 sudah terpasang, modul hidup
  lama : modul hidup SEBELUM unit (2), tanpa V249 -> butir un-apply MERAH (endpoint tak ada)
CN SINTETIS; CN historis tak disentuh. Subjek tanpa data -> exit 2 TAK SAH.
"""
import asyncio
import importlib.util
import os
import re
import sys
import uuid as U
from datetime import date
from decimal import Decimal

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402
from starlette.requests import Request  # noqa: E402

DIR, MODE = sys.argv[1], sys.argv[2]
T, TB = "kaos-biru-konveksi", "grapgrap-manado"
V249 = "/tmp/V249_badan.sql"
hasil = []


def catat(s, u, ok, k=""):
    hasil.append((s, u, bool(ok), str(k)[:230]))


def muat(nama, path, src=None):
    if src is not None:
        path = f"/tmp/{nama.replace('.', '_')}.py"
        open(path, "w", encoding="utf-8").write(src)
    spec = importlib.util.spec_from_file_location(nama, path)
    m = importlib.util.module_from_spec(spec); sys.modules[nama] = m; spec.loader.exec_module(m)
    return m


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
    import app.routers  # noqa: F401
    import app.schemas  # noqa: F401
    if DIR == "-":
        from app.routers import credit_notes as mcn, sales_invoices as msi
        import app.schemas.credit_notes as s
    else:
        s = muat("app.schemas.credit_notes", f"{DIR}/schemas_credit_notes.py")
        src = open(f"{DIR}/credit_notes.py", encoding="utf-8").read()
        if MODE == "sabotase_kaitan":
            A = "SET original_invoice_id = NULL, original_invoice_number = NULL, updated_at = NOW()\n                    WHERE id = $1 AND tenant_id = $2 AND original_invoice_id = $3"
            if src.count(A) != 1:
                print("ALAT: jangkar sabotase_kaitan"); sys.exit(3)
            src = src.replace(A, "SET original_invoice_id = original_invoice_id, updated_at = NOW()\n                    WHERE id = $1 AND tenant_id = $2 AND original_invoice_id = $3")
        if MODE == "sabotase_periode":
            A = "                if period_row and period_row[\"status\"] != \"OPEN\":\n                    raise HTTPException(\n                        status_code=400,\n                        detail=f\"Periode akuntansi penerapan"
            if src.count(A) != 1:
                print("ALAT: jangkar sabotase_periode"); sys.exit(3)
            src = src.replace(A, "                if False and period_row:\n                    raise HTTPException(\n                        status_code=400,\n                        detail=f\"Periode akuntansi penerapan")
        mcn = muat("app.routers.credit_notes_u2", None, src)
        msi = muat("app.routers.sales_invoices_u2", f"{DIR}/sales_invoices.py")
    Item = s.CreateCreditNoteRequest.model_fields["items"].annotation.__args__[0]
    punya_unapply = hasattr(mcn, "unapply_credit_note")

    uid = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)
    rq = lambda: Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b"",  # noqa: E731
                          "state": {"user": {"tenant_id": T, "user_id": str(uid)}}})

    async def panggil(coro_fn):
        try:
            return 200, await coro_fn()
        except Exception as e:  # noqa: BLE001
            return getattr(e, "status_code", type(e).__name__), str(getattr(e, "detail", e))

    async def unapply(cid, alasan="gerbang: salah faktur"):
        if not punya_unapply:
            raise RuntimeError("endpoint unapply TIDAK ADA")
        return await mcn.unapply_credit_note(rq(), cid, s.UnapplyCreditNoteRequest(reason=alasan))

    # subjek: dua faktur pelanggan yang sama (outstanding >= 30.000) + satu faktur yang BISA di-void (tanpa pembayaran/DP)
    inv = await conn.fetchrow("""
        SELECT o.invoice_id, si.customer_id FROM compute_ar_outstanding($1) o JOIN sales_invoices si ON si.id = o.invoice_id
        WHERE o.outstanding >= 30000 AND si.customer_id IS NOT NULL AND si.journal_id IS NOT NULL
          AND (SELECT count(*) FROM compute_ar_outstanding($1) o2 JOIN sales_invoices s2 ON s2.id=o2.invoice_id
               WHERE s2.customer_id = si.customer_id AND o2.outstanding >= 30000) >= 2
        ORDER BY o.outstanding LIMIT 1""", T)
    if not inv:
        print("TAK SAH: subjek dua faktur"); sys.exit(2)
    I, C = inv["invoice_id"], inv["customer_id"]
    I2 = await conn.fetchval("""SELECT o.invoice_id FROM compute_ar_outstanding($1) o JOIN sales_invoices si ON si.id=o.invoice_id
        WHERE si.customer_id=$2 AND o.invoice_id<>$3 AND o.outstanding>=30000 LIMIT 1""", T, C, I)
    IV = await conn.fetchrow("""SELECT o.invoice_id, si.customer_id FROM compute_ar_outstanding($1) o JOIN sales_invoices si ON si.id=o.invoice_id
        WHERE si.customer_id IS NOT NULL AND si.journal_id IS NOT NULL AND o.paid_amount = 0 AND o.outstanding >= 30000
          AND NOT EXISTS (SELECT 1 FROM receive_payment_allocations r WHERE r.invoice_id = si.id)
          AND NOT EXISTS (SELECT 1 FROM customer_deposit_applications d WHERE d.invoice_id = si.id AND d.status='active')
        ORDER BY o.outstanding LIMIT 1""", T)
    cn_tb = await conn.fetchval("SELECT id FROM credit_notes WHERE tenant_id=$1 LIMIT 1", TB)
    if not (I2 and IV and cn_tb):
        print("TAK SAH: subjek pembanding", I2, IV, cn_tb); sys.exit(2)

    async def ukur(inv_id):
        gl = await conn.fetchval("""SELECT COALESCE(SUM(jl.debit-jl.credit),0) FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id
            JOIN chart_of_accounts coa ON coa.id=jl.account_id WHERE je.tenant_id=$1 AND je.status='POSTED' AND coa.account_type='RECEIVABLE'""", T)
        out = await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ar_outstanding($1) WHERE invoice_id=$2", T, inv_id)
        nj = await conn.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id=$1", T)
        c = await conn.fetchrow("SELECT total_amount, amount_paid, status FROM sales_invoices WHERE id=$1", inv_id)
        return {"gl": gl, "out": out, "nj": nj, "cache": c}

    def cache_ok(m):
        c = m["cache"]
        paid = c["total_amount"] - max(Decimal(0), m["out"])
        st = "paid" if m["out"] < Decimal("0.01") else ("partial" if paid > Decimal("0.005") else "posted")
        return c["amount_paid"] == paid and c["status"] == st

    async def cn_diterapkan(nominal, pelanggan, faktur):
        r = await mcn.create_credit_note(rq(), s.CreateCreditNoteRequest(customer_id=str(pelanggan), customer_name="gerbang U2",
                                         credit_note_date=date.today(), reason="other", items=[Item(description="gerbang U2", quantity=1, unit_price=nominal)]))
        cid = U.UUID(str((r.get("data") or r)["id"]))
        await mcn.post_credit_note(rq(), cid)
        await mcn.apply_credit_note(rq(), cid, s.ApplyCreditNoteRequest(applications=[s.ApplyCreditNoteItem(invoice_id=str(faktur), amount=nominal)]))
        return cid

    async def rincian_kaos():
        try:
            return {(r["lapis"], r["nilai"]) for r in await conn.fetch("SELECT lapis, nilai FROM verify_ar_reconciliation_rincian($1)", T)}
        except asyncpg.PostgresError:
            return None

    luar = conn.transaction(); await luar.start()
    try:
        if MODE in ("baru",) or MODE.startswith("sabotase"):
            badan = open(V249, encoding="utf-8").read()
            if MODE == "sabotase_pemicu":
                i = badan.index("CREATE OR REPLACE FUNCTION public.update_credit_note_status()")
                badan = badan[:i]
            await conn.execute(badan)
        base_audit = await conn.fetchval("""SELECT count(*) FROM audit_logs WHERE "eventType"='CREDIT_NOTE_UNAPPLIED'""")

        # ================= 1 bahagia: apply -> unapply
        sp = conn.transaction(); await sp.start()
        m0 = await ukur(I)
        cid = await cn_diterapkan(20000, C, I)
        m1 = await ukur(I)
        r_app = await rincian_kaos()
        k, r = await panggil(lambda: unapply(cid))
        m2 = await ukur(I)
        r_un = await rincian_kaos()
        cn_row = await conn.fetchrow("SELECT status, amount_applied, original_invoice_id, original_invoice_number FROM credit_notes WHERE id=$1", cid)
        apps = await conn.fetch("SELECT * FROM credit_note_applications WHERE credit_note_id=$1", cid)
        n_audit = await conn.fetchval("""SELECT count(*) FROM audit_logs WHERE "eventType"='CREDIT_NOTE_UNAPPLIED'""") - base_audit
        detail_app = await panggil(lambda: msi.get_invoice(rq(), I))
        # 2 terapkan ulang ke faktur kedua
        o2a = (await ukur(I2))["out"]
        k2, r2 = await panggil(lambda: mcn.apply_credit_note(rq(), cid, s.ApplyCreditNoteRequest(applications=[s.ApplyCreditNoteItem(invoice_id=str(I2), amount=20000)])))
        o2b = (await ukur(I2))["out"]
        await sp.rollback()

        catat("UNAPPLY", "batalkan penerapan -> 200", k == 200, (k, r if k != 200 else ""))
        catat("UNAPPLY", "GL piutang TIDAK berubah & NOL jurnal (apply->unapply)", m2["gl"] == m1["gl"] == m0["gl"] - 20000 and m2["nj"] == m1["nj"], (m0["gl"], m1["gl"], m2["gl"], m1["nj"], m2["nj"]))
        catat("UNAPPLY", "outstanding faktur kembali TEPAT seperti sebelum apply", m2["out"] == m0["out"] and m1["out"] == m0["out"] - 20000, (m0["out"], m1["out"], m2["out"]))
        catat("UNAPPLY", "cache faktur == compute sesudah unapply", cache_ok(m2), tuple(m2["cache"]))
        catat("UNAPPLY", "CN kembali posted, amount_applied 0, original_invoice_id & nomor NULL",
              k == 200 and tuple(cn_row) == ("posted", Decimal("0.00"), None, None), tuple(cn_row))
        catat("UNAPPLY", "baris aplikasi TETAP ADA (1), status reversed, alasan & pelaku tercatat",
              len(apps) == 1 and apps[0].get("status") == "reversed" and apps[0].get("reversal_reason") == "gerbang: salah faktur" and apps[0].get("reversed_by") == uid,
              [dict(a) for a in apps][:1])
        catat("UNAPPLY", "audit CREDIT_NOTE_UNAPPLIED tepat 1", n_audit == 1, n_audit)
        catat("UNAPPLY", "checker V248: sesudah unapply muncul residu tak terpatok −20.000 (merah jujur), sesudah apply tidak",
              r_app is not None and r_un is not None and ("L2_RESIDU_TAK_TERPATOK", Decimal("-20000.00")) in (r_un - r_app), (r_app and sorted(r_un - r_app, key=str) if r_un else None))
        catat("UNAPPLY", "terapkan ulang ke faktur kedua -> 200, outstanding faktur kedua −20.000", k2 == 200 and o2a - o2b == 20000, (k2, r2 if k2 != 200 else "", o2a, o2b))
        ok_detail = detail_app[0] == 200 and not any(str(c["id"]) == str(cid) for c in detail_app[1]["data"]["applied_credits"])
        catat("UNAPPLY", "detail faktur: CN yang dibatalkan TIDAK tercantum di applied_credits", ok_detail, detail_app[0])

        # ================= detail faktur mencantumkan CN ber-status applied (perbaikan display)
        sp = conn.transaction(); await sp.start()
        cid = await cn_diterapkan(20000, C, I)
        k, d = await panggil(lambda: msi.get_invoice(rq(), I))
        await sp.rollback()
        catat("DETAIL", "detail faktur mencantumkan CN yang diterapkan (status applied)", k == 200 and any(str(c["id"]) == str(cid) for c in d["data"]["applied_credits"]), k)

        # ================= 3 void CN sesudah unapply
        sp = conn.transaction(); await sp.start()
        cid = await cn_diterapkan(20000, C, I)
        await panggil(lambda: unapply(cid))
        k, r = await panggil(lambda: mcn.void_credit_note(rq(), cid, s.VoidCreditNoteRequest(reason="gerbang")))
        await sp.rollback()
        catat("KUNCI", "void CN sesudah unapply -> 200 (kunci terbuka)", k == 200, (k, r if k != 200 else ""))

        # ================= 4 void faktur sesudah unapply (faktur tanpa pembayaran/DP)
        sp = conn.transaction(); await sp.start()
        cid = await cn_diterapkan(20000, IV["customer_id"], IV["invoice_id"])
        kb, rb = await panggil(lambda: msi.void_invoice(rq(), IV["invoice_id"], msi.VoidInvoiceRequest(reason="gerbang U2")))
        await panggil(lambda: unapply(cid))
        k, r = await panggil(lambda: msi.void_invoice(rq(), IV["invoice_id"], msi.VoidInvoiceRequest(reason="gerbang U2")))
        await sp.rollback()
        catat("KUNCI", "void faktur: sebelum unapply 400 'nota kredit terkait', sesudah unapply 200",
              kb == 400 and "nota kredit terkait" in str(rb) and k == 200, (kb, str(rb)[:60], k, r if k != 200 else ""))

        # ================= 5 penolakan
        async def tolak(label, siap, harap_kode, harap_teks):
            sp = conn.transaction(); await sp.start()
            try:
                k, r = await siap()
            finally:
                await sp.rollback()
            catat("TOLAK", label, k == harap_kode and harap_teks in str(r), (k, r))

        async def s_belum():
            r = await mcn.create_credit_note(rq(), s.CreateCreditNoteRequest(customer_id=str(C), customer_name="gerbang U2", credit_note_date=date.today(),
                                             reason="other", items=[Item(description="g", quantity=1, unit_price=20000)]))
            cid = U.UUID(str((r.get("data") or r)["id"])); await mcn.post_credit_note(rq(), cid)
            return await panggil(lambda: unapply(cid))
        await tolak("CN belum diterapkan -> 400", s_belum, 400, "belum diterapkan")

        async def s_draf():
            r = await mcn.create_credit_note(rq(), s.CreateCreditNoteRequest(customer_id=str(C), customer_name="gerbang U2", credit_note_date=date.today(),
                                             reason="other", items=[Item(description="g", quantity=1, unit_price=20000)]))
            return await panggil(lambda: unapply(U.UUID(str((r.get("data") or r)["id"]))))
        await tolak("CN draf -> 400", s_draf, 400, "belum diterapkan")

        async def s_alasan():
            cid = await cn_diterapkan(20000, C, I)
            return await panggil(lambda: unapply(cid, "   "))
        await tolak("alasan kosong -> 400 terbaca", s_alasan, 400, "Alasan pembatalan penerapan wajib diisi")

        async def s_dua_kali():
            cid = await cn_diterapkan(20000, C, I)
            await panggil(lambda: unapply(cid))
            return await panggil(lambda: unapply(cid))
        await tolak("dua kali berturut -> kedua 400", s_dua_kali, 400, "belum diterapkan")

        sp = conn.transaction(); await sp.start()
        j_tb = await panggil(lambda: unapply(cn_tb)); j_kr = await panggil(lambda: unapply(U.uuid4()))
        await sp.rollback()
        catat("TOLAK", "CN tenant lain == id karangan -> 404 identik", j_tb[0] == 404 and j_tb == j_kr, (j_tb, j_kr))

        # ================= 6 periode tutup (putusan pemilik)
        sp = conn.transaction(); await sp.start()
        cid = await cn_diterapkan(20000, C, I)
        await conn.execute("UPDATE fiscal_periods SET status='CLOSED' WHERE tenant_id=$1 AND CURRENT_DATE BETWEEN start_date AND end_date", T)
        k, r = await panggil(lambda: unapply(cid))
        tetap = await conn.fetchval("SELECT original_invoice_id FROM credit_notes WHERE id=$1", cid)
        await sp.rollback()
        catat("PERIODE", "periode penerapan TUTUP -> 400 'sudah CLOSED', kaitan tetap", k == 400 and "sudah CLOSED" in str(r) and tetap == I, (k, r, tetap))
    finally:
        await luar.rollback()
        sisa = await conn.fetchval("SELECT count(*) FROM credit_notes WHERE customer_name='gerbang U2'")
        kolom = await conn.fetchval("SELECT count(*) FROM information_schema.columns WHERE table_name='credit_note_applications' AND column_name='status'")
        per = await conn.fetchval("SELECT count(*) FROM fiscal_periods WHERE status<>'OPEN'")
        await conn.close()
    catat("ROLLBACK", "nol CN sintetis, periode tetap OPEN semua", sisa == 0 and per == 0, (sisa, kolom, per))

    badan = open(__file__, encoding="utf-8").read().split("async def main():", 1)[1]
    # situs catat() + (panggilan tolak() − 1 situs di dalamnya). (Versi pertama mengurangi 1 lagi tanpa alasan -> 19 vs 20.)
    harap = len(re.findall(r"^\s+catat\(", badan, re.M)) + (len(re.findall(r"^\s+await tolak\(", badan, re.M)) - 1)
    for s_, u, ok, k_ in hasil:
        print(("[H] " if ok else "[X] ") + f"{s_:8} {u}  | {k_}")
    g = [h[1] for h in hasil if not h[2]]
    print(f"\nMODE={MODE} gagal={len(g)} total={len(hasil)} harap={harap} (kolom status ada sesudah ROLLBACK={kolom})")
    if MODE in ("baru", "hidup"):
        sys.exit(0 if not g and len(hasil) == harap else 1)
    if MODE == "lama":
        ok = "batalkan penerapan -> 200" in g
        print("[lama] ->", "MERAH PADA KODE LAMA" if ok else "TAK MERAH"); sys.exit(0 if ok else 1)
    target = {"sabotase_pemicu": "void CN sesudah unapply", "sabotase_kaitan": "outstanding faktur kembali",
              "sabotase_periode": "periode penerapan TUTUP"}[MODE]
    ok = any(x.startswith(target) for x in g) and "batalkan penerapan -> 200" not in g
    print(f"[{MODE}] ->", "TERTANGKAP" if ok else "LOLOS", g); sys.exit(0 if ok else 1)


asyncio.run(main())
