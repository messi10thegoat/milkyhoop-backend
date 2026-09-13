"""GERBANG pagar V250 credit_notes.original_invoice_id. Satu transaksi luar, savepoint per skenario, ROLLBACK.
argv: <path credit_notes.py | -> <mode>
  baru          : V250 dipasang di transaksi + modul berurutan-baru. Semua hijau.
  hidup         : V250 sudah terpasang, modul hidup.
  lama          : tanpa V250 -> UPDATE langsung LOLOS -> butir pagar MERAH.
  urutan_lama   : V250 dipasang + modul SEBELUM urutan ditukar -> apply/unapply handler MERAH (bukti urutan wajib).
  sabotase_isi  : pagar tanpa syarat aplikasi aktif pada NULL->X -> butir 'UPDATE langsung NULL->faktur' MERAH.
  sabotase_lepas: pagar tanpa syarat pembatalan pada X->NULL     -> butir 'UPDATE langsung faktur->NULL' MERAH.
CN sintetis. Subjek tanpa data -> exit 2 TAK SAH.
"""
import asyncio
import importlib.util
import os
import re
import sys
import uuid as U
from datetime import date

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402
from starlette.requests import Request  # noqa: E402

PATH, MODE = sys.argv[1], sys.argv[2]
T = "kaos-biru-konveksi"
V250 = "/tmp/V250_badan.sql"
hasil = []


def catat(s, u, ok, k=""):
    hasil.append((s, u, bool(ok), str(k)[:200]))


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
    if PATH == "-":
        from app.routers import credit_notes as mcn
    else:
        spec = importlib.util.spec_from_file_location("app.routers.cn_pagar", PATH)
        mcn = importlib.util.module_from_spec(spec); sys.modules[spec.name] = mcn; spec.loader.exec_module(mcn)
    import app.schemas.credit_notes as s
    Item = s.CreateCreditNoteRequest.model_fields["items"].annotation.__args__[0]

    uid = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)
    rq = lambda: Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b"",  # noqa: E731
                          "state": {"user": {"tenant_id": T, "user_id": str(uid)}}})
    inv = await conn.fetchrow("""
        SELECT o.invoice_id, si.customer_id FROM compute_ar_outstanding($1) o JOIN sales_invoices si ON si.id = o.invoice_id
        WHERE o.outstanding >= 30000 AND si.customer_id IS NOT NULL AND si.journal_id IS NOT NULL
          AND (SELECT count(*) FROM compute_ar_outstanding($1) o2 JOIN sales_invoices s2 ON s2.id=o2.invoice_id
               WHERE s2.customer_id = si.customer_id AND o2.outstanding >= 30000) >= 2
        ORDER BY o.outstanding LIMIT 1""", T)
    if not inv:
        print("TAK SAH: subjek"); sys.exit(2)
    I, C = inv["invoice_id"], inv["customer_id"]
    I2 = await conn.fetchval("""SELECT o.invoice_id FROM compute_ar_outstanding($1) o JOIN sales_invoices si ON si.id=o.invoice_id
        WHERE si.customer_id=$2 AND o.invoice_id<>$3 AND o.outstanding>=30000 LIMIT 1""", T, C, I)

    badan = open(V250, encoding="utf-8").read()
    if MODE == "sabotase_isi":
        A = "        IF NOT EXISTS (SELECT 1 FROM credit_note_applications a\n                       WHERE a.credit_note_id = NEW.id AND a.invoice_id = NEW.original_invoice_id"
        if badan.count(A) != 1:
            print("ALAT: jangkar sabotase_isi"); sys.exit(3)
        badan = badan.replace(A, "        IF false AND NOT EXISTS (SELECT 1 FROM credit_note_applications a\n                       WHERE a.credit_note_id = NEW.id AND a.invoice_id = NEW.original_invoice_id")
    if MODE == "sabotase_lepas":
        A = "    -- X -> NULL\n    IF EXISTS"
        if badan.count(A) != 1:
            print("ALAT: jangkar sabotase_lepas"); sys.exit(3)
        # percobaan pertama 'IF false AND EXISTS ... OR NOT EXISTS ...' TAK mematikan cabang (presedensi AND/OR:
        # syarat pembatalan tetap dievaluasi) -> sabotase tak jalan = alat, bukan kebutaan gerbang. Kini lolos-dini.
        badan = badan.replace(A, "    -- X -> NULL\n    RETURN NEW;  -- SABOTASE: lepas kaitan tanpa syarat\n    IF EXISTS")

    async def panggil(fn):
        try:
            return 200, await fn()
        except Exception as e:  # noqa: BLE001
            return getattr(e, "status_code", None) or getattr(e, "sqlstate", None) or type(e).__name__, str(getattr(e, "detail", e))

    async def cn_posting(nominal=20000):
        r = await mcn.create_credit_note(rq(), s.CreateCreditNoteRequest(customer_id=str(C), customer_name="gerbang V250", credit_note_date=date.today(),
                                         reason="other", items=[Item(description="g", quantity=1, unit_price=nominal)]))
        cid = U.UUID(str((r.get("data") or r)["id"]))
        await mcn.post_credit_note(rq(), cid)
        return cid

    def apl(cid, faktur):
        return mcn.apply_credit_note(rq(), cid, s.ApplyCreditNoteRequest(applications=[s.ApplyCreditNoteItem(invoice_id=str(faktur), amount=20000)]))

    async def langsung(sql, *arg):
        sp = conn.transaction(); await sp.start()
        try:
            await conn.execute(sql, *arg)
            return "LOLOS", ""
        except asyncpg.PostgresError as e:
            return e.sqlstate, str(e)[:90]
        finally:
            await sp.rollback()

    luar = conn.transaction(); await luar.start()
    try:
        if MODE not in ("lama", "hidup"):
            await conn.execute(badan)
        n_trg = await conn.fetchval("SELECT count(*) FROM pg_trigger WHERE tgname='trg_credit_note_kaitan_faktur_beku'")
        if MODE != "lama":
            catat("PRASYARAT", "pagar terpasang (1 trigger)", n_trg == 1, n_trg)
        else:
            catat("PRASYARAT", "fase lama: pagar TIDAK ada", n_trg == 0, n_trg)

        # ---- penolakan langsung (merah jika LOLOS)
        sp = conn.transaction(); await sp.start()
        cid_posted = await cn_posting()
        k, d = await langsung("UPDATE credit_notes SET original_invoice_id=$1 WHERE id=$2", I, cid_posted)
        catat("PAGAR", "UPDATE langsung NULL->faktur pada CN posted TANPA aplikasi -> ditolak 23514", k == "23514", (k, d))
        k0, _ = await panggil(lambda: apl(cid_posted, I))
        k, d = await langsung("UPDATE credit_notes SET original_invoice_id=$1 WHERE id=$2", I2, cid_posted)
        catat("PAGAR", "UPDATE langsung faktur->faktur lain pada CN diterapkan -> ditolak 23514", k0 == 200 and k == "23514", (k0, k, d))
        k, d = await langsung("UPDATE credit_notes SET original_invoice_id=NULL WHERE id=$1", cid_posted)
        catat("PAGAR", "UPDATE langsung faktur->NULL tanpa pembatalan aplikasi -> ditolak 23514", k0 == 200 and k == "23514", (k0, k, d))
        # pembatalan aplikasi TANPA lewat handler tapi tanpa transaksi yang sama? (reversed_at != now()) -> tetap ditolak
        k, d = await langsung("""UPDATE credit_note_applications SET status='reversed', reversed_at = now() - interval '1 day', reversed_by=$2, reversal_reason='x'
                                 WHERE credit_note_id=$1; UPDATE credit_notes SET original_invoice_id=NULL WHERE id=$1""".replace("$2", f"'{uid}'").replace("$1", f"'{cid_posted}'"))
        catat("PAGAR", "aplikasi 'dibatalkan' dgn reversed_at lampau lalu lepas kaitan -> ditolak", k == "23514", (k, d))
        await sp.rollback()

        # ---- tak ada merah palsu
        sp = conn.transaction(); await sp.start()
        r = await mcn.create_credit_note(rq(), s.CreateCreditNoteRequest(customer_id=str(C), customer_name="gerbang V250", credit_note_date=date.today(),
                                         reason="other", items=[Item(description="g", quantity=1, unit_price=20000)]))
        cid_draf = U.UUID(str((r.get("data") or r)["id"]))
        k, d = await langsung("UPDATE credit_notes SET original_invoice_id=$1 WHERE id=$2", I, cid_draf)
        catat("BEBAS", "draf: UPDATE langsung kaitan -> diizinkan", k == "LOLOS", (k, d))
        k1, r1 = await panggil(lambda: mcn.update_credit_note(rq(), cid_draf, s.UpdateCreditNoteRequest(original_invoice_id=str(I))))
        k2, r2 = await panggil(lambda: mcn.post_credit_note(rq(), cid_draf))
        catat("BEBAS", "draf terkait: PATCH 200 lalu posting 200 (jalur B POST)", k1 == 200 and k2 == 200, (k1, r1 if k1 != 200 else "", k2, r2 if k2 != 200 else ""))
        k3, r3 = await panggil(lambda: mcn.void_credit_note(rq(), cid_draf, s.VoidCreditNoteRequest(reason="gerbang")))
        catat("BEBAS", "void CN terkait-dari-draf -> 200", k3 == 200, (k3, r3 if k3 != 200 else ""))
        await sp.rollback()

        sp = conn.transaction(); await sp.start()
        cid = await cn_posting()
        ka, ra = await panggil(lambda: apl(cid, I))
        link_a = await conn.fetchval("SELECT original_invoice_id FROM credit_notes WHERE id=$1", cid)
        ku, ru = await panggil(lambda: mcn.unapply_credit_note(rq(), cid, s.UnapplyCreditNoteRequest(reason="gerbang V250")))
        link_u = await conn.fetchval("SELECT original_invoice_id FROM credit_notes WHERE id=$1", cid)
        kr, rr = await panggil(lambda: apl(cid, I2))
        link_r = await conn.fetchval("SELECT original_invoice_id FROM credit_notes WHERE id=$1", cid)
        await sp.rollback()
        catat("HANDLER", "apply lewat handler -> 200, kaitan terisi", ka == 200 and link_a == I, (ka, ra if ka != 200 else "", link_a))
        catat("HANDLER", "unapply lewat handler -> 200, kaitan NULL", ku == 200 and link_u is None, (ku, ru if ku != 200 else "", link_u))
        catat("HANDLER", "terapkan ulang ke faktur kedua -> 200", kr == 200 and link_r == I2, (kr, rr if kr != 200 else "", link_r))

        sp = conn.transaction(); await sp.start()
        cid = await cn_posting()
        await panggil(lambda: apl(cid, I))
        kx, rx = await panggil(lambda: apl(cid, I2))
        n_app = await conn.fetchval("SELECT count(*) FROM credit_note_applications WHERE credit_note_id=$1", cid)
        await sp.rollback()
        catat("HANDLER", "apply kedua ditolak 400 'sudah terkait' dan aplikasi kedua IKUT BATAL (1 baris)", kx == 400 and "sudah terkait" in str(rx) and n_app == 1, (kx, rx, n_app))
    finally:
        await luar.rollback()
        sisa = await conn.fetchval("SELECT count(*) FROM credit_notes WHERE customer_name='gerbang V250'")
        await conn.close()
    catat("ROLLBACK", "nol CN sintetis tersisa", sisa == 0, sisa)

    badan_g = open(__file__, encoding="utf-8").read().split("async def main():", 1)[1]
    harap = len(re.findall(r"^\s+catat\(", badan_g, re.M)) - 1   # PRASYARAT: if/else satu slot
    for s_, u, ok, k in hasil:
        print(("[H] " if ok else "[X] ") + f"{s_:9} {u}  | {k}")
    g = [h[1] for h in hasil if not h[2]]
    print(f"\nMODE={MODE} gagal={len(g)} total={len(hasil)} harap={harap}")
    if MODE in ("baru", "hidup"):
        sys.exit(0 if not g and len(hasil) == harap else 1)
    wajib = {"lama": ["UPDATE langsung NULL->faktur", "UPDATE langsung faktur->faktur", "UPDATE langsung faktur->NULL"],
             "urutan_lama": ["apply lewat handler", "unapply lewat handler"],
             "sabotase_isi": ["UPDATE langsung NULL->faktur"],
             "sabotase_lepas": ["UPDATE langsung faktur->NULL"]}[MODE]
    ok = all(any(x.startswith(w) for x in g) for w in wajib)
    if MODE.startswith("sabotase"):
        ok = ok and not any(x.startswith("apply lewat handler") or x.startswith("unapply lewat handler") for x in g)
    print(f"[{MODE}] ->", "SESUAI HARAPAN (merah yang wajib)" if ok else "TAK SESUAI", g)
    sys.exit(0 if ok else 1)


asyncio.run(main())
