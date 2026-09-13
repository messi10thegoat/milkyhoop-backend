"""GERBANG UNIT B — nota kredit: satu CN, satu faktur. Modul SALINAN (worktree) dimuat di kontainer; satu transaksi luar,
ROLLBACK di akhir. Hasil disimpan di memori Python (ROLLBACK TO SAVEPOINT menghapus baris hasil — Law 33 instans 8).

argv: <dir berkas baru: pihak_helpers.py credit_notes.py sales_invoices.py> [sabotase]
  tanpa 'sabotase' : semua butir wajib hijau
  'sabotase'       : penulisan original_invoice_id di apply dinetralkan -> butir 'selisih menutup' WAJIB merah

CN historis (CN-2608-0001, CN-2609-0006) tak disentuh: semua CN sintetis.
Subjek wajib punya data yang diuji, kalau tidak exit 2 TAK SAH.
"""
import asyncio
import importlib.util
import os
import sys
import uuid as U
from datetime import date
from decimal import Decimal

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402
from starlette.requests import Request  # noqa: E402

DIR = sys.argv[1]
SABOTASE = len(sys.argv) > 2 and sys.argv[2] == "sabotase"
T, TB = "kaos-biru-konveksi", "grapgrap-manado"
hasil = []


def catat(s, u, ok, k=""):
    hasil.append((s, u, bool(ok), str(k)[:220]))


def muat(nama, path, src=None):
    if src is not None:
        path2 = f"/tmp/{nama.replace('.', '_')}.py"
        open(path2, "w", encoding="utf-8").write(src)
        path = path2
    spec = importlib.util.spec_from_file_location(nama, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[nama] = m
    spec.loader.exec_module(m)
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
    import app.services  # noqa: F401
    import app.routers  # noqa: F401
    muat("app.services.pihak_helpers", f"{DIR}/pihak_helpers.py")  # rute salinan mengimpor helper BARU
    cn_src = open(f"{DIR}/credit_notes.py", encoding="utf-8").read()
    if SABOTASE:
        A = "SET original_invoice_id = $1, original_invoice_number = $2, updated_at = NOW()"
        if cn_src.count(A) != 1:
            print("ALAT: jangkar sabotase tak 1x"); sys.exit(3)
        # pernyataan tetap SAH ($1 tetap bertipe) supaya apply tetap 200: merahnya harus dari atribusi, bukan dari galat
        # (percobaan pertama membuang $1 -> IndeterminateDatatype 500 = merah untuk alasan yang salah).
        cn_src = cn_src.replace(A, "SET original_invoice_id = (CASE WHEN $1::uuid IS NULL THEN NULL ELSE NULL END)::uuid, original_invoice_number = $2, updated_at = NOW()")
    mcn = muat("app.routers.credit_notes_b", None, cn_src)
    msi = muat("app.routers.sales_invoices_b", f"{DIR}/sales_invoices.py")
    import app.schemas.credit_notes as s
    Item = s.CreateCreditNoteRequest.model_fields["items"].annotation.__args__[0]

    uid = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)

    def rq():
        return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b"",
                        "state": {"user": {"tenant_id": T, "user_id": str(uid)}}})

    # ---- subjek
    inv = await conn.fetchrow("""
        SELECT o.invoice_id, o.outstanding, si.customer_id, si.total_amount FROM compute_ar_outstanding($1) o
        JOIN sales_invoices si ON si.id = o.invoice_id
        WHERE o.outstanding >= 30000 AND si.customer_id IS NOT NULL AND si.journal_id IS NOT NULL
          AND (SELECT count(*) FROM compute_ar_outstanding($1) o2 JOIN sales_invoices s2 ON s2.id=o2.invoice_id
               WHERE s2.customer_id = si.customer_id AND o2.outstanding >= 30000) >= 2
        ORDER BY o.outstanding LIMIT 1""", T)
    if not inv:
        print("TAK SAH: tak ada pelanggan dengan >=2 faktur ber-outstanding >= 30.000"); sys.exit(2)
    I, C = inv["invoice_id"], inv["customer_id"]
    I2 = await conn.fetchval("""SELECT o.invoice_id FROM compute_ar_outstanding($1) o JOIN sales_invoices si ON si.id=o.invoice_id
        WHERE si.customer_id=$2 AND o.invoice_id<>$3 AND o.outstanding>=30000 LIMIT 1""", T, C, I)
    I_lain = await conn.fetchval("""SELECT o.invoice_id FROM compute_ar_outstanding($1) o JOIN sales_invoices si ON si.id=o.invoice_id
        WHERE si.customer_id IS NOT NULL AND si.customer_id<>$2 AND o.outstanding>=30000 LIMIT 1""", T, C)
    I_tenant_lain = await conn.fetchval("SELECT id FROM sales_invoices WHERE tenant_id=$1 AND journal_id IS NOT NULL LIMIT 1", TB)
    if not (I2 and I_lain and I_tenant_lain):
        print("TAK SAH: subjek pembanding tak lengkap", I2, I_lain, I_tenant_lain); sys.exit(2)
    print("subjek", {"I": I, "out": inv["outstanding"], "C": C, "I2": I2, "I_lain": I_lain, "I_tenant_lain": I_tenant_lain})

    async def ukur(inv_id):
        gl = await conn.fetchval("""SELECT COALESCE(SUM(jl.debit-jl.credit),0) FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id
            JOIN chart_of_accounts coa ON coa.id=jl.account_id WHERE je.tenant_id=$1 AND je.status='POSTED' AND coa.account_type='RECEIVABLE'""", T)
        sig = await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ar_outstanding($1)", T)
        out = await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ar_outstanding($1) WHERE invoice_id=$2", T, inv_id)
        nj = await conn.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id=$1", T)
        c = await conn.fetchrow("SELECT total_amount, amount_paid, status FROM sales_invoices WHERE id=$1", inv_id)
        ar = await conn.fetchrow("SELECT amount_paid, status FROM accounts_receivable WHERE source_id=$1 AND source_type='INVOICE' AND status<>'VOID' LIMIT 1", inv_id)
        return {"gl": gl, "sig": sig, "gap": gl - sig, "out": out, "nj": nj, "cache": c, "ar": ar}

    def cache_sepakat(m):
        c = m["cache"]
        harap_paid = c["total_amount"] - max(Decimal(0), m["out"])
        harap_st = "paid" if m["out"] < Decimal("0.01") else ("partial" if harap_paid > Decimal("0.005") else "posted")
        ok = c["amount_paid"] == harap_paid and c["status"] == harap_st
        if m["ar"] is not None:
            ok = ok and m["ar"]["amount_paid"] == harap_paid
        return ok, (c["amount_paid"], c["status"], harap_paid, harap_st, m["ar"] and tuple(m["ar"]))

    jejak = {}

    async def panggil(coro, kunci=None):
        try:
            r = await coro
            return 200, r
        except Exception as e:  # noqa: BLE001
            if kunci:
                # bentuk respons FastAPI untuk HTTPException = status + {"detail": detail} + headers -> simpan ketiganya
                jejak[kunci] = (type(e).__name__, getattr(e, "status_code", None), repr(getattr(e, "detail", e)), repr(getattr(e, "headers", None)))
            return getattr(e, "status_code", type(e).__name__), str(getattr(e, "detail", e))

    async def buat_cn(nominal, pelanggan, posting=True, original_invoice_id=None):
        k, r = await panggil(mcn.create_credit_note(rq(), s.CreateCreditNoteRequest(
            customer_id=str(pelanggan) if pelanggan else None, customer_name="gerbang B", credit_note_date=date.today(),
            reason="other", original_invoice_id=str(original_invoice_id) if original_invoice_id else None,
            items=[Item(description="gerbang B", quantity=1, unit_price=nominal)])))
        if k != 200:
            return k, r
        cid = U.UUID(str((r.get("data") or r)["id"]))
        if posting:
            k2, r2 = await panggil(mcn.post_credit_note(rq(), cid))
            if k2 != 200:
                return k2, r2
        return 200, cid

    def apl(inv_id, amount):
        return s.ApplyCreditNoteRequest(applications=[s.ApplyCreditNoteItem(invoice_id=str(inv_id), amount=int(amount))])

    luar = conn.transaction(); await luar.start()
    try:
        # ================= jalur bahagia
        sp = conn.transaction(); await sp.start()
        k, cid = await buat_cn(20000, C)
        m1 = await ukur(I)
        catat("PRASYARAT", "CN sintetis 20.000 dibuat & dibukukan", k == 200, (k, cid))
        k, r = await panggil(mcn.apply_credit_note(rq(), cid, apl(I, 20000)))
        m2 = await ukur(I)
        catat("B BAHAGIA", "apply seluruh nilai ke satu faktur -> 200", k == 200, (k, r if k != 200 else ""))
        catat("B BAHAGIA", "GL piutang TIDAK berubah", m2["gl"] == m1["gl"], (m1["gl"], m2["gl"]))
        catat("B BAHAGIA", "selisih GL−Σcompute menutup TEPAT 20.000", m2["gap"] - m1["gap"] == Decimal("20000"), (m1["gap"], m2["gap"]))
        catat("B BAHAGIA", "outstanding faktur turun TEPAT 20.000", m1["out"] - m2["out"] == Decimal("20000"), (m1["out"], m2["out"]))
        catat("B BAHAGIA", "nol jurnal baru saat apply", m2["nj"] == m1["nj"], (m1["nj"], m2["nj"]))
        ok, d = cache_sepakat(m2)
        catat("B BAHAGIA", "cache faktur (+accounts_receivable) == compute", ok, d)
        row = await conn.fetchrow("SELECT original_invoice_id, status, amount_applied, (SELECT count(*) FROM credit_note_applications WHERE credit_note_id=$1) n FROM credit_notes WHERE id=$1", cid)
        catat("B BAHAGIA", "CN terkait ke faktur, status applied, 1 aplikasi", row["original_invoice_id"] == I and row["status"] == "applied" and row["n"] == 1, tuple(row))
        k, r = await panggil(mcn.apply_credit_note(rq(), cid, apl(I2, 20000)))
        catat("B TOLAK", "tunjuk-ulang ke faktur kedua -> 400 sudah terkait", k == 400 and "sudah terkait" in r, (k, r))
        k, r = await panggil(mcn.void_credit_note(rq(), cid, s.VoidCreditNoteRequest(reason="gerbang")))
        catat("B TOLAK", "void CN yang sudah diterapkan -> 400", k == 400, (k, r))
        k, r = await panggil(msi.void_invoice(rq(), I, msi.VoidInvoiceRequest(reason="gerbang B")))
        catat("B (b)", "void faktur ber-CN terkait -> 400 menyebut nomor CN", k == 400 and "nota kredit terkait" in r, (k, r))
        await sp.rollback()

        # ================= penolakan apply (tiap skenario savepoint sendiri)
        async def tolak(label, nominal, pelanggan, target, jumlah, harap_teks, dua=False, posting=True):
            sp = conn.transaction(); await sp.start()
            k, cid = await buat_cn(nominal, pelanggan, posting=posting)
            if k != 200:
                catat("B TOLAK", label, False, ("ALAT: CN gagal dibuat", k, cid)); await sp.rollback(); return None
            body = apl(target, jumlah) if not dua else s.ApplyCreditNoteRequest(applications=[
                s.ApplyCreditNoteItem(invoice_id=str(I), amount=10000), s.ApplyCreditNoteItem(invoice_id=str(I2), amount=10000)])
            k, r = await panggil(mcn.apply_credit_note(rq(), cid, body), kunci=label)
            await sp.rollback()
            catat("B TOLAK", label, k == 400 and harap_teks in r, (k, r))
            return r

        await tolak("sebagian -> 400 seluruhnya (Rp terbaca)", 20000, C, I, 10000, "harus diterapkan seluruhnya (Rp 20.000)")
        await tolak("dua faktur -> 400 satu faktur", 20000, C, I, 0, "hanya bisa diterapkan ke satu faktur", dua=True)
        await tolak("faktur pelanggan lain -> 400", 20000, C, I_lain, 20000, "milik pelanggan lain")
        await tolak("CN tanpa pelanggan -> 400", 20000, None, I, 20000, "Pilih pelanggan nota kredit")
        r_tl = await tolak("faktur tenant lain -> 400 Faktur tidak ditemukan", 20000, C, I_tenant_lain, 20000, "Faktur tidak ditemukan")
        r_kr = await tolak("id faktur karangan -> 400 Faktur tidak ditemukan", 20000, C, U.uuid4(), 20000, "Faktur tidak ditemukan")
        j_tl = jejak.get("faktur tenant lain -> 400 Faktur tidak ditemukan")
        j_kr = jejak.get("id faktur karangan -> 400 Faktur tidak ditemukan")
        catat("B TOLAK", "tenant lain == karangan: jenis galat + status + repr(detail) + headers identik",
              r_tl is not None and j_tl is not None and j_tl == j_kr, (j_tl, j_kr))

        # ================= apply yang MELUNASI: accounts_receivable lunas (kasus tak tercakup pembanding hari ini)
        lunas = await conn.fetchrow("""
            SELECT o.invoice_id, o.outstanding, si.customer_id FROM compute_ar_outstanding($1) o
            JOIN sales_invoices si ON si.id = o.invoice_id
            WHERE si.customer_id IS NOT NULL AND si.journal_id IS NOT NULL AND o.outstanding > 0
              AND o.outstanding = trunc(o.outstanding)
              AND EXISTS (SELECT 1 FROM accounts_receivable ar WHERE ar.source_id = si.id AND ar.source_type = 'INVOICE' AND ar.status <> 'VOID')
            ORDER BY o.outstanding LIMIT 1""", T)
        if not lunas:
            catat("B LUNAS", "subjek melunasi dengan baris accounts_receivable ada", False, "TAK SAH: tak ada subjek")
        else:
            sp = conn.transaction(); await sp.start()
            k, cid = await buat_cn(int(lunas["outstanding"]), lunas["customer_id"])
            k2, r2 = await panggil(mcn.apply_credit_note(rq(), cid, apl(lunas["invoice_id"], int(lunas["outstanding"])))) if k == 200 else (k, cid)
            m = await ukur(lunas["invoice_id"])
            await sp.rollback()
            catat("B LUNAS", "apply melunasi: faktur paid, accounts_receivable PAID, amount_paid == total",
                  k2 == 200 and m["out"] == 0 and m["cache"]["status"] == "paid" and m["ar"] is not None
                  and m["ar"]["status"] == "PAID" and m["ar"]["amount_paid"] == m["cache"]["total_amount"] == m["cache"]["amount_paid"],
                  (k2, r2 if k2 != 200 else "", lunas["outstanding"], m["out"], tuple(m["cache"]), m["ar"] and tuple(m["ar"])))
        lebih = int(inv["outstanding"]) + 1000
        await tolak("melebihi sisa tagihan -> 400 (Rp terbaca)", lebih, C, I, lebih, "melebihi sisa tagihan faktur")
        await tolak("CN draf -> 400", 20000, C, I, 20000, "sudah dibukukan", posting=False)

        # ================= (a) pembuat & penyunting draf
        sp = conn.transaction(); await sp.start()
        k, r = await buat_cn(20000, C, posting=False, original_invoice_id=I_tenant_lain)
        k2, r2 = await buat_cn(20000, C, posting=False, original_invoice_id=U.uuid4())
        catat("B (a)", "buat draf dgn faktur tenant lain == karangan -> 400 identik", k == 400 and k2 == 400 and r == r2 == "Faktur tidak ditemukan", (k, r, k2, r2))
        k, r = await buat_cn(20000, C, posting=False, original_invoice_id=I_lain)
        catat("B (a)", "buat draf dgn faktur pelanggan lain -> 400", k == 400 and "milik pelanggan lain" in r, (k, r))
        k, did = await buat_cn(20000, C, posting=False)
        k2, r2 = await panggil(mcn.update_credit_note(rq(), did, s.UpdateCreditNoteRequest(original_invoice_id=str(I_lain))))
        catat("B (a)", "PATCH draf ke faktur pelanggan lain -> 400", k2 == 400 and "milik pelanggan lain" in r2, (k2, r2))
        k3, r3 = await panggil(mcn.update_credit_note(rq(), did, s.UpdateCreditNoteRequest(original_invoice_id=str(I))))
        simpan = await conn.fetchrow("SELECT original_invoice_id, original_invoice_number FROM credit_notes WHERE id=$1", did)
        catat("B (a)", "PATCH draf ke faktur sah -> 200, id+nomor tersimpan", k3 == 200 and simpan["original_invoice_id"] == I and simpan["original_invoice_number"], (k3, r3 if k3 != 200 else "", tuple(simpan)))
        # jalur posting draf terkait: atribusi + cache
        m1 = await ukur(I)
        k4, r4 = await panggil(mcn.post_credit_note(rq(), did))
        m2 = await ukur(I)
        ok, d = cache_sepakat(m2)
        catat("B POST", "posting draf terkait -> 200, outstanding turun 20.000, cache == compute",
              k4 == 200 and m1["out"] - m2["out"] == Decimal("20000") and ok, (k4, r4 if k4 != 200 else "", m1["out"], m2["out"], d))
        k5, r5 = await panggil(mcn.void_credit_note(rq(), did, s.VoidCreditNoteRequest(reason="gerbang")))
        m3 = await ukur(I)
        ok, d = cache_sepakat(m3)
        catat("B (c)", "void CN terkait (tanpa aplikasi) -> outstanding pulih, cache == compute",
              k5 == 200 and m3["out"] == m1["out"] and ok, (k5, r5 if k5 != 200 else "", m1["out"], m3["out"], d))
        await sp.rollback()
        sp = conn.transaction(); await sp.start()
        k, did = await buat_cn(lebih, C, posting=False, original_invoice_id=I)
        k2, r2 = await panggil(mcn.post_credit_note(rq(), did)) if k == 200 else (k, did)
        catat("B POST", "posting draf terkait yang melebihi sisa tagihan -> 400", k2 == 400 and "melebihi sisa tagihan" in r2, (k, k2, r2))
        await sp.rollback()
    finally:
        await luar.rollback()
        sisa = await conn.fetchval("SELECT count(*) FROM credit_notes WHERE customer_name='gerbang B'")
        await conn.close()

    # harap DITURUNKAN dari struktur (konstanta tangan pertama 28 salah hitung -> TAK SAH palsu):
    # situs catat() di main, dikurangi situs cabang-alternatif (ALAT di tolak, TAK SAH di lunas), dikurangi situs di
    # badan tolak(), ditambah jumlah pemanggilan tolak().
    import re
    badan = open(__file__, encoding="utf-8").read().split("async def main():", 1)[1]
    situs = len(re.findall(r"^\s+catat\(", badan, re.M))
    # (penanda disusun dari potongan: literalnya sendiri di baris ini pernah ikut terhitung -> 28 vs 27)
    alternatif = len(re.findall(r'catat\("B TOLAK", label, False|TAK SAH: tak ' + 'ada subjek', badan))
    di_tolak = len(re.findall(r'catat\("B TOLAK", label, k == 400', badan))
    panggilan_tolak = len(re.findall(r"^\s+(?:r_\w+ = )?await tolak\(", badan, re.M))
    harap = situs - alternatif - di_tolak + panggilan_tolak
    for s_, u, ok, k in hasil:
        print(("[H] " if ok else "[X] ") + f"{s_:10} {u}  | {k}")
    g = [h for h in hasil if not h[2]]
    print(f"\nCN sintetis tersisa sesudah ROLLBACK = {sisa}")
    if SABOTASE:
        merah = [h[1] for h in g]
        # merah untuk alasan yang BENAR: apply tetap 200 (galat tak bergeser), tapi selisih tak menutup
        ok = (any("selisih GL" in x for x in merah) and not any("apply seluruh nilai" in x for x in merah)
              and len(hasil) == harap)
        print(f"[sabotase] merah={merah} -> {'SABOTASE TERTANGKAP' if ok else 'SABOTASE LOLOS / TAK SAH'}")
        sys.exit(0 if ok else 1)
    print(f"gagal={len(g)} total={len(hasil)} harap={harap} -> {'LENGKAP' if len(hasil) == harap else 'TAK SAH'}")
    sys.exit(0 if not g and len(hasil) == harap and sisa == 0 else 1)


asyncio.run(main())
