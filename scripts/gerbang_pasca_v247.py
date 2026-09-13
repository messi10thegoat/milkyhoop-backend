"""GERBANG PASCA-RESTART V247 atas modul & koneksi HIDUP (koneksi BARU sesudah restart). Baca-saja; uji tulis di ROLLBACK.

1 skema: kedua kolom uuid; FK komposit ada; UNIQUE customers(id, tenant_id) ada; V247 terdaftar
2 pagar DB: INSERT pelanggan tenant lain -> 23503 (ROLLBACK), di kedua tabel
3 rute utama (handler hidup, validasi response_model): CN list/detail, DP list/detail, receive-payments list,
  applicable-deposits -> 200
4 tab jurnal pelanggan ber-CN: entries == total == independen
5 validasi pelanggan: nama -> 400; tenant lain -> 400; sah -> 200 kanonik (buat CN, buat DP) di ROLLBACK
6 DP lintas pelanggan -> 400 · terapkan CN -> 400 penutupan
7 hc_verdict 8 sesuai patok · verify_ar_all · V244/V245/V246 penanda hidup
Cacah harapan dari struktur.
"""
import asyncio
import json
import os
import sys
import uuid
from datetime import date

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402
from starlette.requests import Request  # noqa: E402
from pydantic import TypeAdapter  # noqa: E402

T, TB = "kaos-biru-konveksi", "grapgrap-manado"
hasil = []


def catat(s, u, ok, k=""):
    hasil.append((s, u, bool(ok), str(k)[:200]))


def baca_id(r):
    for k in (r, getattr(r, "data", None), (r.get("data") if isinstance(r, dict) else None)):
        if isinstance(k, dict) and k.get("id"):
            return k["id"]
        if k is not None and getattr(k, "id", None):
            return k.id
    raise RuntimeError("ALAT: id tak terbaca")


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
    from app.main import app
    from app.routers import credit_notes as rcn, customer_deposits as rdp, customers as rcu, sales_invoices as rsi, receive_payments as rrp
    import app.schemas.credit_notes as scn, app.schemas.customer_deposits as sdp
    rm = {(r.endpoint.__module__, r.endpoint.__name__): getattr(r, "response_model", None) for r in app.routes if getattr(r, "endpoint", None)}

    uid = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)

    def req():
        return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b"",
                        "state": {"user": {"tenant_id": T, "user_id": str(uid)}}})

    # 1 skema
    tipe = {r["table_name"]: r["data_type"] for r in await conn.fetch(
        "SELECT table_name, data_type FROM information_schema.columns WHERE column_name='customer_id' AND table_name IN ('credit_notes','customer_deposits')")}
    catat("1 SKEMA", "kedua kolom uuid", tipe == {"credit_notes": "uuid", "customer_deposits": "uuid"}, tipe)
    fk = await conn.fetchval("SELECT count(*) FROM pg_constraint WHERE conname IN ('fk_credit_notes_customer_tenant','fk_customer_deposits_customer_tenant')")
    uq = await conn.fetchval("SELECT count(*) FROM pg_constraint WHERE conname='uq_customers_id_tenant'")
    reg = await conn.fetchval("SELECT count(*) FROM schema_migrations WHERE version='V247__pelanggan_uuid_fk_komposit.sql'")
    catat("1 SKEMA", "FK komposit 2 + UNIQUE 1 + V247 terdaftar", fk == 2 and uq == 1 and reg == 1, (fk, uq, reg))

    # 2 pagar DB
    lain = await conn.fetchval("SELECT id FROM customers WHERE tenant_id=$1 LIMIT 1", TB)
    for tabel in ("credit_notes", "customer_deposits"):
        tr = conn.transaction(); await tr.start()
        try:
            await conn.execute(f"UPDATE {tabel} SET customer_id = $1 WHERE id = (SELECT id FROM {tabel} WHERE tenant_id=$2 LIMIT 1)", lain, T)
            got = "LOLOS"
        except asyncpg.PostgresError as e:
            got = e.sqlstate
        await tr.rollback()
        catat("2 PAGAR DB", f"{tabel}: pelanggan tenant lain -> 23503", got == "23503", got)

    # 3 rute utama
    cn_id = await conn.fetchval("SELECT id FROM credit_notes WHERE tenant_id=$1 AND customer_id IS NOT NULL LIMIT 1", T)
    dep_id = await conn.fetchval("SELECT id FROM customer_deposits WHERE tenant_id=$1 AND customer_id IS NOT NULL LIMIT 1", T)
    rp_id = await conn.fetchval("SELECT id FROM receive_payments WHERE tenant_id=$1 LIMIT 1", T)
    inv = await conn.fetchval("SELECT id FROM sales_invoices WHERE tenant_id=$1 AND journal_id IS NOT NULL LIMIT 1", T)
    import inspect
    from fastapi.params import Param

    async def get(mod, nama, **ids):
        fn = getattr(mod, nama)
        kw = {}
        for n, p in inspect.signature(fn).parameters.items():
            if n == "request":
                kw[n] = req()
            elif n in ids:
                kw[n] = ids[n]
            elif isinstance(p.default, Param):
                kw[n] = p.default.default
            elif p.default is not inspect._empty:
                kw[n] = p.default
        try:
            out = await fn(**kw)
            m = rm.get((fn.__module__, nama))
            if m is not None:
                TypeAdapter(m).validate_python(out)
            return 200, ""
        except Exception as e:  # noqa: BLE001
            return getattr(e, "status_code", type(e).__name__), str(getattr(e, "detail", e))[:80]

    for label, args in (("CN list", (rcn, "list_credit_notes", {})), ("CN detail", (rcn, "get_credit_note", {"credit_note_id": cn_id})),
                        ("DP list", (rdp, "list_customer_deposits", {})), ("DP detail", (rdp, "get_customer_deposit", {"deposit_id": dep_id})),
                        ("receive-payments list", (rrp, "list_receive_payments", {})),
                        ("receive-payment detail", (rrp, "get_receive_payment", {"payment_id": rp_id})),
                        ("applicable-deposits", (rsi, "get_applicable_deposits", {"invoice_id": inv}))):
        k = await get(args[0], args[1], **args[2])
        catat("3 RUTE", f"{label} -> 200", k[0] == 200, k)

    # 4 tab jurnal
    cust_cn = await conn.fetchval("SELECT customer_id FROM credit_notes WHERE tenant_id=$1 AND customer_id IS NOT NULL LIMIT 1", T)
    ind = await conn.fetchval("""
        WITH p AS (SELECT $2::uuid cid), c AS (
          SELECT si.journal_id j FROM sales_invoices si, p WHERE si.customer_id=p.cid AND si.tenant_id=$1
          UNION SELECT si.cogs_journal_id FROM sales_invoices si, p WHERE si.customer_id=p.cid AND si.tenant_id=$1
          UNION SELECT rp.journal_id FROM receive_payments rp, p WHERE rp.customer_id=p.cid AND rp.tenant_id=$1
          UNION SELECT cd.journal_id FROM customer_deposits cd, p WHERE cd.customer_id=p.cid AND cd.tenant_id=$1
          UNION SELECT cn.journal_id FROM credit_notes cn, p WHERE cn.customer_id=p.cid AND cn.tenant_id=$1
          UNION SELECT sip.journal_id FROM sales_invoice_payments sip JOIN sales_invoices si ON si.id=sip.invoice_id, p WHERE si.customer_id=p.cid AND si.tenant_id=$1)
        SELECT count(*) FROM c JOIN journal_entries je ON je.id=c.j WHERE je.tenant_id=$1 AND je.status='POSTED'""", T, cust_cn)
    try:
        r = await rcu.get_customer_journal_entries(req(), str(cust_cn), None, None, None, 1, 100)
        n, tot = len(r["data"]["entries"]), r["data"]["total"]
    except Exception as e:  # noqa: BLE001
        n, tot = f"GAGAL {getattr(e,'detail',e)}", None
    # Subjek tanpa data membuat 0==0==0 tautologis: kosong = TAK SAH, bukan hijau (run 1 memang 0,0,0 —
    # isi dibuktikan terpisah oleh gerbang_jurnal_pelanggan_uuid.py pada subjek ber-jurnal DP & CN).
    if not ind:
        print("TAK SAH: subjek tab jurnal tanpa jurnal POSTED — pilih subjek lain")
        sys.exit(2)
    catat("4 TAB JURNAL", "entries == total == independen (tak kosong)", n == tot == ind, (n, tot, ind))

    # 5 validasi pelanggan (ROLLBACK)
    Item = scn.CreateCreditNoteRequest.model_fields["items"].annotation.__args__[0]
    akun = await conn.fetchval("SELECT coa_id FROM bank_accounts WHERE tenant_id=$1 AND is_active LIMIT 1", T)
    for tag, nilai, harap in (("nama", "Toko Melati", 400), ("tenant lain", str(lain), 400), ("sah", str(cust_cn).upper(), 200)):
        for pen in ("CN", "DP"):
            tr = conn.transaction(); await tr.start()
            try:
                if pen == "CN":
                    rr = await rcn.create_credit_note(req(), scn.CreateCreditNoteRequest(customer_id=nilai, customer_name="uji pasca", credit_note_date=date.today(),
                                                      reason="other", items=[Item(description="u", quantity=1, unit_price=1000)]))
                    simpan = await conn.fetchval("SELECT customer_id::text FROM credit_notes WHERE id=$1", uuid.UUID(str(baca_id(rr))))
                else:
                    rr = await rdp.create_customer_deposit(req(), sdp.CreateCustomerDepositRequest(customer_id=nilai, customer_name="uji pasca", amount=1000,
                                                           deposit_date=date.today(), payment_method="transfer", account_id=str(akun)))
                    simpan = await conn.fetchval("SELECT customer_id::text FROM customer_deposits WHERE id=$1", uuid.UUID(str(baca_id(rr))))
                kode = 200
            except Exception as e:  # noqa: BLE001
                kode, simpan = getattr(e, "status_code", type(e).__name__), str(getattr(e, "detail", e))[:60]
            await tr.rollback()
            ok = kode == harap and (harap != 200 or simpan == str(cust_cn).lower())
            catat("5 VALIDASI", f"buat {pen} pelanggan {tag} -> {harap}", ok, (kode, simpan))

    # 6 DP lintas pelanggan & apply CN
    from app.schemas.customer_deposits import ApplyCustomerDepositRequest as AR_, ApplyDepositItem as AI
    pas = await conn.fetchrow("""SELECT d.id dep, s.id inv FROM customer_deposits d JOIN sales_invoices s ON s.tenant_id=d.tenant_id AND s.journal_id IS NOT NULL
        WHERE d.tenant_id=$1 AND d.status IN ('posted','partial') AND s.customer_id <> d.customer_id LIMIT 1""", T)
    tr = conn.transaction(); await tr.start()
    try:
        await rdp.apply_customer_deposit(req(), pas["dep"], AR_(applications=[AI(invoice_id=str(pas["inv"]), amount=1000)]))
        k = 200
    except Exception as e:  # noqa: BLE001
        k = getattr(e, "status_code", type(e).__name__)
    await tr.rollback()
    catat("6 PIHAK", "DP lintas pelanggan -> 400", k == 400, k)
    tr = conn.transaction(); await tr.start()
    try:
        await rcn.apply_credit_note(req(), cn_id, scn.ApplyCreditNoteRequest(applications=[scn.ApplyCreditNoteItem(invoice_id=str(inv), amount=1)]))
        k, d = 200, ""
    except Exception as e:  # noqa: BLE001
        k, d = getattr(e, "status_code", type(e).__name__), str(getattr(e, "detail", e))
    await tr.rollback()
    catat("6 PIHAK", "terapkan CN -> 400 penutupan", k == 400 and "belum tersedia" in d, (k, d[:50]))

    # 7 pemeriksaan
    v = {(t, c): await conn.fetchval("SELECT verdict FROM hc_verdict($1,$2)", c, t) for t in (T, TB)
         for c in ("ap_invariant", "inventory_value", "status_desync", "bank_sync")}
    harapv = {k: ("PASS_EXEMPT" if k[0] == T else "PASS") for k in v}
    catat("7 PERIKSA", "hc_verdict 8 sesuai patok", v == harapv, {k: v[k] for k in v if v[k] != harapv[k]})
    va = {r["tenant_id"]: r["verdict"] for r in await conn.fetch("SELECT tenant_id, verdict FROM verify_ar_reconciliation_all()")}
    catat("7 PERIKSA", "verify_ar_all PASS", all(x == "PASS" for x in va.values()), va)
    pen = await conn.fetchrow("""SELECT (SELECT count(*) FROM pg_trigger WHERE tgname='trg_law19_bekukan_nominal') trg,
        position('pd_bayar' in pg_get_functiondef('compute_ap_outstanding(text)'::regprocedure))>0 v245,
        position('a3.id' in pg_get_functiondef('hc_ap_members(text)'::regprocedure))>0 v246""")
    catat("7 PERIKSA", "V244 5 trigger · V245 · V246 hidup", pen["trg"] == 5 and pen["v245"] and pen["v246"], dict(pen))
    await conn.close()

    harap = 2 + 2 + 7 + 1 + 6 + 2 + 3
    for s, u, ok, k in hasil:
        print(("[H] " if ok else "[X] ") + f"{s:11} {u}  | {k}")
    g = sum(1 for h in hasil if not h[2])
    print(f"\ngagal={g} total={len(hasil)} harap={harap} (dari struktur) -> {'LENGKAP' if len(hasil) == harap else 'TAK SAH'}")
    sys.exit(0 if g == 0 and len(hasil) == harap else 1)


asyncio.run(main())
