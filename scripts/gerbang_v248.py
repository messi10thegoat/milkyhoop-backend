"""GERBANG V248 — verify_ar_reconciliation_all tak lagi buta. Satu transaksi luar, ROLLBACK; hasil di memori Python.

argv[1] mode:
  lama            : checker HIDUP (tanpa V248). Butir DETEKSI wajib MERAH (bukti kebutaan) -> gerbang merah = benar.
  baru            : badan V248 dipasang di transaksi. Semua butir wajib hijau.
  sabotase_cn     : V248 dgn cabang klaim CREDIT_NOTE dibuang -> kontrol apply CN wajib MERAH.
  sabotase_skalar : V248 dgn L2 dibaca sebagai SKALAR (merah hanya bila jumlah beda) -> skenario offset wajib MERAH.
Subjek skenario deteksi = grapgrap (garis dasar HIJAU PASS_EXEMPT) supaya merahnya berasal dari skenario, bukan dari
merah kaos-biru yang sudah ada. Subjek tanpa data yang diuji -> exit 2 TAK SAH.
"""
import asyncio
import os
import re
import sys
import uuid as U
from datetime import date
from decimal import Decimal

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402
from starlette.requests import Request  # noqa: E402

MODE = sys.argv[1]
G, K = "grapgrap-manado", "kaos-biru-konveksi"
BADAN = "/tmp/V248_badan.sql"
hasil = []


def catat(s, u, ok, k=""):
    hasil.append((s, u, bool(ok), str(k)[:230]))


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
    from app.routers import credit_notes as rcn
    import app.schemas.credit_notes as s
    Item = s.CreateCreditNoteRequest.model_fields["items"].annotation.__args__[0]

    n_jurnal0 = await conn.fetchval("SELECT count(*) FROM journal_entries")
    n_audit0 = await conn.fetchval("""SELECT count(*) FROM audit_logs WHERE "eventType"='AR_RECONCILIATION_PIN'""")
    pin_ada0 = await conn.fetchval("SELECT to_regclass('ar_reconciliation_pins') IS NOT NULL")

    badan = open(BADAN, encoding="utf-8").read()
    if MODE == "sabotase_cn":
        a0 = "        -- nota kredit yang terkait faktur\n"
        a1 = "        -- penerapan uang muka"
        if badan.count(a0) != 1:
            print("ALAT: jangkar sabotase_cn"); sys.exit(3)
        i, j = badan.index(a0), badan.index(a1)
        badan = badan[:i] + badan[j:]
    if MODE == "sabotase_skalar":
        A = "WHEN f.tak_terpatok > 0 THEN 'FAIL_UNPINNED'"
        if badan.count(A) != 1:
            print("ALAT: jangkar sabotase_skalar"); sys.exit(3)
        badan = badan.replace(A, "WHEN f.tak_terpatok > 0 AND ABS(p.gl - p.canon - p.pin_net) > 0.01 THEN 'FAIL_UNPINNED'")

    luar = conn.transaction(); await luar.start()
    try:
        if MODE not in ("lama", "hidup"):   # hidup = V248 sudah terpasang; gerbang atas fungsi yang HIDUP
            await conn.execute(badan)
        baru = MODE != "lama"

        async def verdikt(t):
            return await conn.fetchval("SELECT verdict FROM verify_ar_reconciliation_all() WHERE tenant_id=$1", t)

        async def anggota(t):
            if not baru:
                return None
            return {(r["lapis"], r["kunci"], r["nilai"]) for r in await conn.fetch("SELECT * FROM verify_ar_reconciliation_rincian($1)", t)}

        dasar_g, dasar_k = await verdikt(G), await verdikt(K)
        ang_g0, ang_k0 = await anggota(G), await anggota(K)
        if baru:
            catat("DASAR", "grapgrap PASS_EXEMPT (pin CN-2608-0001 cocok)", dasar_g == "PASS_EXEMPT", (dasar_g, ang_g0))
            catat("DASAR", "kaos-biru FAIL_UNPINNED dgn TEPAT 1 anggota ce216061 −25.000, nol L3",
                  dasar_k == "FAIL_UNPINNED" and ang_k0 == {("L2_RESIDU_TAK_TERPATOK", U.UUID("ce216061-6f26-40e1-a1a9-d14db3075fb3"), Decimal("-25000.00"))},
                  (dasar_k, ang_k0))
        else:
            catat("DASAR", "checker lama: grapgrap PASS & kaos-biru PASS (buta terhadap 225.000)", dasar_g == "PASS" and dasar_k == "PASS", (dasar_g, dasar_k))

        # --- kontrak cron check_14: kueri persis skrip, verdikt dalam kosakata, tak galat
        for t in (G, K):
            v = await conn.fetchval(f"SELECT verdict FROM verify_ar_reconciliation_all() WHERE tenant_id = '{t}';")
            d = await conn.fetchval(f"SELECT total_drift FROM verify_ar_reconciliation_all() WHERE tenant_id = '{t}';")
            catat("CRON", f"check_14 {t}: kueri skrip jalan, verdikt berkosakata, drift terbaca",
                  v in ("PASS", "PASS_EXEMPT", "FAIL_UNPINNED", "FAIL_STALE_PIN", "FAIL_PER_INVOICE", "FAIL_TOTAL", "FAIL_NON_EXEMPT", "FAIL_DRIFT_CHANGED") and d is not None, (v, d))

        # --- perkakas jurnal uji (DRAFT -> baris -> POSTED), di savepoint
        uid_g = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", G)
        ar_g = await conn.fetchval("SELECT id FROM chart_of_accounts WHERE tenant_id=$1 AND account_type='RECEIVABLE' AND account_code='1-10400'", G)
        lawan_g = await conn.fetchval("SELECT id FROM chart_of_accounts WHERE tenant_id=$1 AND account_type='EXPENSE' AND NOT COALESCE(is_header,false) ORDER BY account_code LIMIT 1", G)
        if not (uid_g and ar_g and lawan_g):
            print("TAK SAH: perkakas grapgrap tak lengkap", uid_g, ar_g, lawan_g); sys.exit(2)

        async def jurnal(source_type, baris, reversal_of=None):
            jid = U.uuid4()
            tot = sum(d for _, d, _ in baris)
            nomor = f"GERBANG-V248-{str(jid)[:8]}"
            await conn.execute("""INSERT INTO journal_entries (id, tenant_id, journal_number, journal_date, description, source_type,
                source_id, reversal_of_id, status, total_debit, total_credit, created_by)
                VALUES ($1,$2,$3,CURRENT_DATE,'gerbang V248',$4,$5,$6,'DRAFT',$7,$7,$8)""",
                jid, G, nomor, source_type, U.uuid4(), reversal_of, tot, uid_g)
            ids = []
            for n, (akun, debit, kredit) in enumerate(baris, 1):
                lid = U.uuid4(); ids.append(lid)
                await conn.execute("INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo) VALUES ($1,$2,$3,$4,$5,$6,'gerbang V248')",
                                   lid, jid, n, akun, debit, kredit)
            await conn.execute("UPDATE journal_entries SET status='POSTED' WHERE id=$1", jid)
            return jid, ids

        async def skenario(label, fn, harap_anggota_tambah):
            """harap_anggota_tambah: set (lapis, net) yang wajib MUNCUL; deteksi = verdikt berubah jadi merah DAN anggota tepat."""
            sp = conn.transaction(); await sp.start()
            try:
                info = await fn()
                v = await verdikt(G)
                ang = await anggota(G)
            finally:
                await sp.rollback()
            if baru:
                tambah = {(l, n) for (l, _, n) in (ang - ang_g0)}
                ok = v != "PASS_EXEMPT" and tambah == harap_anggota_tambah
                catat("DETEKSI", label, ok, (v, sorted(tambah, key=str), info))
            else:
                catat("DETEKSI", label, v not in ("PASS", "PASS_EXEMPT"), (v, "checker lama", info))

        async def s1():
            return await jurnal("MANUAL", [(ar_g, 0, 777), (lawan_g, 777, 0)])
        await skenario("S1 MANUAL kredit piutang 777 tak teratribusi -> merah, residu tepat {−777}", s1,
                       {("L2_RESIDU_TAK_TERPATOK", Decimal("-777.00"))})

        async def s2():
            return await jurnal("MANUAL", [(ar_g, 777, 0), (ar_g, 0, 777)])
        await skenario("S2 offset +777/−777 (L1 total tetap) -> merah, residu 2 anggota", s2,
                       {("L2_RESIDU_TAK_TERPATOK", Decimal("777.00")), ("L2_RESIDU_TAK_TERPATOK", Decimal("-777.00"))})

        inv_j = await conn.fetchrow("""SELECT je.id, jl.debit FROM journal_entries je JOIN journal_lines jl ON jl.journal_id=je.id
            WHERE je.tenant_id=$1 AND je.source_type='INVOICE' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND jl.account_id=$2 AND jl.debit>1000 LIMIT 1""", G, ar_g)
        if not inv_j:
            print("TAK SAH: tak ada jurnal faktur grapgrap"); sys.exit(2)

        async def s3():
            # pembalik TIMPANG: nominal pembalik kurang 1 dari aslinya
            return await jurnal("INVOICE_REVERSAL", [(ar_g, 0, inv_j["debit"] - 1), (lawan_g, inv_j["debit"] - 1, 0)], reversal_of=inv_j["id"])
        await skenario("S3 pembalik timpang (−(X−1) atas jurnal faktur X) -> merah, residu tepat {−(X−1)}", s3,
                       {("L2_RESIDU_TAK_TERPATOK", -(inv_j["debit"] - 1))})

        # --- kontrol benar (tak boleh merah palsu): apply CN sintetis lewat handler unit B yang HIDUP
        mel = await conn.fetchrow("""SELECT o.invoice_id, o.outstanding, si.customer_id FROM compute_ar_outstanding($1) o
            JOIN sales_invoices si ON si.id=o.invoice_id WHERE si.customer_id IS NOT NULL AND o.outstanding>0
              AND o.outstanding = trunc(o.outstanding) ORDER BY o.outstanding LIMIT 1""", G)
        if not mel:
            print("TAK SAH: tak ada faktur grapgrap ber-outstanding bulat"); sys.exit(2)

        def rq():
            return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b"",
                            "state": {"user": {"tenant_id": G, "user_id": str(uid_g)}}})

        sp = conn.transaction(); await sp.start()
        try:
            r = await rcn.create_credit_note(rq(), s.CreateCreditNoteRequest(customer_id=str(mel["customer_id"]), customer_name="gerbang V248",
                                             credit_note_date=date.today(), reason="other",
                                             items=[Item(description="gerbang V248", quantity=1, unit_price=int(mel["outstanding"]))]))
            cid = U.UUID(str((r.get("data") or r)["id"]))
            await rcn.post_credit_note(rq(), cid)
            v_post, ang_post = await verdikt(G), await anggota(G)
            await rcn.apply_credit_note(rq(), cid, s.ApplyCreditNoteRequest(applications=[s.ApplyCreditNoteItem(invoice_id=str(mel["invoice_id"]), amount=int(mel["outstanding"]))]))
            v_app, ang_app = await verdikt(G), await anggota(G)
            ok_k = True
        except Exception as e:  # noqa: BLE001
            ok_k, v_post, v_app, ang_post, ang_app = False, None, None, None, f"{type(e).__name__}: {getattr(e, 'detail', e)}"
        finally:
            await sp.rollback()
        if baru:
            catat("KONTROL", "CN sintetis dibukukan (belum diterapkan) -> merah dgn tepat 1 anggota baru (−outstanding)",
                  ok_k and v_post == "FAIL_UNPINNED" and {(l, n) for (l, _, n) in (ang_post - ang_g0)} == {("L2_RESIDU_TAK_TERPATOK", -mel["outstanding"])},
                  (v_post, ang_post and sorted(ang_post - ang_g0, key=str)))
            catat("KONTROL", "sesudah apply lewat handler B -> kembali PASS_EXEMPT, himpunan anggota == garis dasar (tak merah palsu)",
                  ok_k and v_app == "PASS_EXEMPT" and ang_app == ang_g0, (v_app, ang_app))
        else:
            catat("KONTROL", "checker lama: CN dibukukan tanpa faktur -> TETAP PASS (buta)", False, (v_post, v_app, "lama"))
            catat("KONTROL", "checker lama: sesudah apply", v_app == "PASS", v_app)

        # --- pin basi (sidik diubah 1 rupiah)
        if baru:
            sp = conn.transaction(); await sp.start()
            await conn.execute("UPDATE ar_reconciliation_pins SET net = net + 1 WHERE tenant_id=$1", G)
            v = await verdikt(G)
            await sp.rollback()
            catat("PIN", "sidik pin diubah 1 rupiah -> FAIL_STALE_PIN", v == "FAIL_STALE_PIN", v)
            # independensi: peta klaim tak memanggil compute/is_effective (komentar dibuang); kontrol positif pada _all
            def tanpa_komentar(x):
                return re.sub(r"--[^\n]*", "", x)
            klaim = tanpa_komentar(await conn.fetchval("SELECT pg_get_functiondef('ar_klaim_piutang(text)'::regprocedure)"))
            semua = tanpa_komentar(await conn.fetchval("SELECT pg_get_functiondef('verify_ar_reconciliation_all()'::regprocedure)"))
            rinci = tanpa_komentar(await conn.fetchval("SELECT pg_get_functiondef('verify_ar_reconciliation_rincian(text)'::regprocedure)"))
            catat("INDEPENDEN", "peta klaim TAK memanggil compute_ar_outstanding / is_effective_journal",
                  "compute_ar_outstanding" not in klaim and "is_effective_journal" not in klaim, "")
            catat("INDEPENDEN", "kontrol positif: pencari MENEMUKAN compute_ar_outstanding di pembanding (_all & rincian)",
                  "compute_ar_outstanding" in semua and "compute_ar_outstanding" in rinci, "")
            catat("INDEPENDEN", "checker baru tak memakai is_effective_journal di mana pun", "is_effective_journal" not in semua + rinci, "")
        else:
            for u in ("sidik pin diubah 1 rupiah -> FAIL_STALE_PIN", "INDEPENDEN peta klaim", "INDEPENDEN kontrol positif", "INDEPENDEN is_effective"):
                catat("PIN/INDEP", u + " (tak ada di checker lama)", False, "lama")
    finally:
        await luar.rollback()

    n_jurnal1 = await conn.fetchval("SELECT count(*) FROM journal_entries")
    n_audit1 = await conn.fetchval("""SELECT count(*) FROM audit_logs WHERE "eventType"='AR_RECONCILIATION_PIN'""")
    pin_ada1 = await conn.fetchval("SELECT to_regclass('ar_reconciliation_pins') IS NOT NULL")
    sisa_cn = await conn.fetchval("SELECT count(*) FROM credit_notes WHERE customer_name='gerbang V248'")
    catat("ROLLBACK", "nol sisa: jurnal, audit pin, tabel pin, CN sintetis sama seperti sebelum",
          n_jurnal1 == n_jurnal0 and n_audit1 == n_audit0 and pin_ada1 == pin_ada0 and sisa_cn == 0,
          (n_jurnal0, n_jurnal1, n_audit0, n_audit1, pin_ada0, pin_ada1, sisa_cn))
    await conn.close()

    # harap diturunkan dari struktur: situs catat() di main (tanpa cabang lama/else yang menggantikan situs yang sama)
    src = open(__file__, encoding="utf-8").read().split("async def main():", 1)[1]
    blok_baru = src.count("if baru:")
    situs = len(re.findall(r"^\s+catat\(", src, re.M))
    # tiap 'if baru: ... else:' menggantikan; hitung situs cabang lama untuk dikurangkan
    lama = len(re.findall(r'^\s+catat\("DETEKSI", label, v not in|^\s+catat\("KONTROL", "checker lama|^\s+catat\("PIN/INDEP"|^\s+catat\("DASAR", "checker lama', src, re.M))
    # situs cabang baru + (panggilan skenario() − 1 situsnya) + (loop CRON 2 tenant − 1 situs). Berlaku untuk mode baru/
    # sabotase; mode lama punya cacah cabang sendiri (DASAR 1 bukan 2) dan diputus oleh butir DETEKSI, bukan cacah.
    harap = (situs - lama) + (len(re.findall(r"^\s+await skenario\(", src, re.M)) - 1) + (2 - 1)
    for s_, u, ok, k in hasil:
        print(("[H] " if ok else "[X] ") + f"{s_:10} {u}  | {k}")
    g = [h for h in hasil if not h[2]]
    print(f"\nMODE={MODE} gagal={len(g)} total={len(hasil)} harap={harap} blok_baru={blok_baru}")
    if MODE in ("baru", "hidup"):
        sys.exit(0 if not g and len(hasil) == harap else 1)
    if MODE == "lama":
        merah = {h[1] for h in g}
        wajib = [h[1] for h in hasil if h[0] == "DETEKSI"]
        ok = all(w in merah for w in wajib) and len(wajib) == 3
        print(f"[lama] deteksi merah {sum(w in merah for w in wajib)}/{len(wajib)} -> {'BUTA TERBUKTI' if ok else 'TAK SAH'}")
        sys.exit(0 if ok else 1)
    if MODE == "sabotase_cn":
        ok = any("sesudah apply lewat handler B" in h[1] for h in g)
        print(f"[sabotase_cn] -> {'TERTANGKAP' if ok else 'LOLOS'}  merah={[h[1] for h in g]}")
        sys.exit(0 if ok else 1)
    if MODE == "sabotase_skalar":
        ok = any(h[1].startswith("S2 offset") for h in g) and not any(h[1].startswith("S1 ") for h in g)
        print(f"[sabotase_skalar] -> {'TERTANGKAP' if ok else 'LOLOS'}  merah={[h[1] for h in g]}")
        sys.exit(0 if ok else 1)


asyncio.run(main())
