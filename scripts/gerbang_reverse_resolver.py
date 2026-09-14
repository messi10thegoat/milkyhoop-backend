"""GERBANG unit (1) Option A. Muat entity_resolver (baru/lama) di bawah nama modul asli (import relatif tetap jalan),
panggil resolve_and_complete('reverse_journal', {journal_number}). Satu transaksi luar, ROLLBACK.
argv: <path entity_resolver.py> <mode: baru|lama>
"""
import asyncio
import importlib.util
import os
import sys
import uuid as U
from datetime import date

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402

PATH, MODE = sys.argv[1], sys.argv[2]
T = "kaos-biru-konveksi"
hasil = []


def catat(s, u, ok, k=""):
    hasil.append((s, u, bool(ok), str(k)[:180]))


def muat_resolver(path):
    name = "app.services.unified_agent.entity_resolver"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    m.__package__ = "app.services.unified_agent"
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    import app.services.unified_agent  # noqa: F401
    er = muat_resolver(PATH)
    R = er.EntityResolver(conn, T)

    async def resolve(jnum):
        res = await R.resolve_and_complete("reverse_journal", {"journal_number": jnum})
        klar = " ".join(res.clarifications or [])
        return res.needs_clarification, klar

    luar = conn.transaction(); await luar.start()
    try:
        uid = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)

        async def sintetis_manual():
            jid = U.uuid4()
            await conn.execute("""INSERT INTO journal_entries (id, tenant_id, journal_number, journal_date, description,
                source_type, source_id, total_debit, total_credit, status, created_by)
                VALUES ($1,$2,$3,CURRENT_DATE,'g','MANUAL',$4,0,0,'POSTED',$5)""",
                jid, T, "GERBANG-RJ-MANUAL", U.uuid4(), uid)
            return "GERBANG-RJ-MANUAL"

        # subjek dokumen: cari journal_number tiap source_type + nomor dokumennya
        async def doc_subject(st, tbl, col):
            r = await conn.fetchrow(f"""SELECT je.journal_number, d.{col} AS docnum FROM journal_entries je
                JOIN {tbl} d ON d.id = je.source_id AND d.tenant_id = je.tenant_id
                WHERE je.tenant_id=$1 AND je.source_type=$2 LIMIT 1""", T, st)
            return (r["journal_number"], r["docnum"]) if r else (None, None)

        # 1 MANUAL sintetis -> tidak diblok (lanjut / bukan pesan "milik dokumen")
        sp = conn.transaction(); await sp.start()
        try:
            jn = await sintetis_manual()
            try:
                nk, klar = await resolve(jn)
            except Exception as e:  # noqa: BLE001 (Step A bisa berat; yang penting BUKAN pesan milik-dokumen)
                nk, klar = False, f"(step-a: {type(e).__name__})"
        finally:
            await sp.rollback()
        catat("MANUAL", "MANUAL tidak diblok gate (bukan pesan 'milik dokumen')", "milik" not in klar and "tak bisa dibalik lewat pembalikan" not in klar, (nk, klar[:60]))

        # 2 dokumen: BILL, INVOICE, PRODUCTION
        for st, tbl, col, sebut, frasa in (("BILL", "bills", "invoice_number", "tagihan", "void tagihan"),
                                           ("INVOICE", "sales_invoices", "invoice_number", "faktur", "void faktur"),
                                           ("PRODUCTION_OUTPUT", "production_orders", "order_number", "work order", "batalkan work order")):
            jn, docnum = await doc_subject(st, tbl, col)
            if not jn:
                catat("REJECT", f"{st}: subjek ada", False, "tak ada pasangan jurnal+dokumen"); continue
            sp = conn.transaction(); await sp.start()
            try:
                nk, klar = await resolve(jn)
            except Exception as e:  # noqa: BLE001 (lama: Step A bisa error; itu = tak diblok = celah)
                nk, klar = False, f"(err {type(e).__name__})"
            finally:
                await sp.rollback()
            if MODE == "lama":
                catat("LAMA", f"{st}: TIDAK diklarifikasi (pending dibuat) — celah", not nk or ("milik" not in klar), (nk, klar[:40]))
            else:
                ok = nk and (str(docnum) in klar) and (frasa in klar)
                catat("REJECT", f"{st}: klarifikasi menyebut {sebut} {docnum} + '{frasa}', 0 pending", ok, (nk, klar[:90]))

        # 3 doc-not-found: jurnal BILL dgn source_id acak -> sebut source_type saja
        if MODE != "lama":
            sp = conn.transaction(); await sp.start()
            try:
                jid = U.uuid4()
                await conn.execute("""INSERT INTO journal_entries (id, tenant_id, journal_number, journal_date, description,
                    source_type, source_id, total_debit, total_credit, status, created_by)
                    VALUES ($1,$2,'GERBANG-RJ-ORPHAN',CURRENT_DATE,'g','BILL',$3,0,0,'POSTED',$4)""", jid, T, U.uuid4(), uid)
                nk, klar = await resolve("GERBANG-RJ-ORPHAN")
            finally:
                await sp.rollback()
            catat("NOTFOUND", "dokumen tak ditemukan -> sebut source_type (BILL) saja, tanpa tebak nomor", nk and "(BILL)" in klar and "void tagihan" not in klar, (nk, klar[:80]))
    finally:
        await luar.rollback()
        sisa = await conn.fetchval("SELECT count(*) FROM journal_entries WHERE journal_number LIKE 'GERBANG-RJ-%'")
        await conn.close()
    catat("BERSIH", "nol jurnal sintetis tersisa", sisa == 0, sisa)

    for s, u, ok, k in hasil:
        print(("[H] " if ok else "[X] ") + f"{s:9} {u}  | {k}")
    g = [h for h in hasil if not h[2]]
    print(f"\nMODE={MODE} gagal={len(g)} total={len(hasil)}")
    if MODE == "baru":
        sys.exit(0 if not g else 1)
    merah_lama = [h for h in hasil if h[0] == "LAMA" and not h[2]]
    print("[lama] ->", "CELAH TERBUKTI (dokumen diklarifikasi hanya di baru)" if not merah_lama else "TAK SESUAI", [h[1] for h in merah_lama])
    sys.exit(0 if not merah_lama else 1)


asyncio.run(main())
