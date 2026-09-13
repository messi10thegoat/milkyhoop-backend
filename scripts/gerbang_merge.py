"""GERBANG unit (3) gabung pelanggan. Satu transaksi luar, savepoint per skenario, ROLLBACK. Hasil di memori.
argv: <path customers.py | -> <mode>
  baru | hidup | lama (kode hidup sebelum unit) | lama1 (lama + dinding deleted_by saja) | sabotase_tabel | sabotase_tenant
Daftar kolom 'tertinggal' DITURUNKAN SENDIRI dari information_schema (kolom *customer*id*), independen dari MERGE_TABEL.
"""
import asyncio
import importlib.util
import json
import os
import re
import sys
import uuid as U

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402
from starlette.requests import Request  # noqa: E402

PATH, MODE = sys.argv[1], sys.argv[2]
T, TB = "kaos-biru-konveksi", "grapgrap-manado"
LIVE = "/app/backend/api_gateway/app/routers/customers.py"
hasil = []
TEMUAN = {}


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
    src = open(LIVE if PATH == "-" else PATH, encoding="utf-8").read()
    if MODE == "lama1":
        A = 'deleted_by = $3 WHERE id = ANY($1) AND tenant_id = $2",\n                    source_ids,\n                    ctx["tenant_id"],\n                    ctx["user_id"],'
        if src.count(A) != 1:
            print("ALAT: jangkar lama1"); sys.exit(3)
        src = src.replace(A, 'deleted_by = $3 WHERE id = ANY($1) AND tenant_id = $2",\n                    source_ids,\n                    ctx["tenant_id"],\n                    str(ctx["user_id"]),')
    if MODE == "sabotase_tabel":
        A = '    ("sales_orders", "customer_id"),\n'
        if src.count(A) != 1:
            print("ALAT: jangkar sabotase_tabel"); sys.exit(3)
        src = src.replace(A, "")
    if MODE == "sabotase_tenant":
        A = '"SELECT id, nama, nomor_member, is_active, deleted_at FROM customers WHERE id = ANY($1::uuid[]) AND tenant_id = $2",\n        [sasaran, *sumber], tenant_id,'
        if src.count(A) != 1:
            print("ALAT: jangkar sabotase_tenant"); sys.exit(3)
        src = src.replace(A, '"SELECT id, nama, nomor_member, is_active, deleted_at FROM customers WHERE id = ANY($1::uuid[]) AND $2::text IS NOT NULL",\n        [sasaran, *sumber], tenant_id,')
    open("/tmp/cust_gerbang.py", "w", encoding="utf-8").write(src)
    import app.routers  # noqa: F401
    spec = importlib.util.spec_from_file_location("app.routers.cust_gerbang", "/tmp/cust_gerbang.py")
    m = importlib.util.module_from_spec(spec); sys.modules[spec.name] = m; spec.loader.exec_module(m)

    uid = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)

    def rq(body):
        async def recv():
            return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
        return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b"",
                        "state": {"user": {"tenant_id": T, "user_id": str(uid)}}}, recv)

    async def panggil(fn, body):
        try:
            return 200, await fn(rq(body))
        except Exception as e:  # noqa: BLE001
            return getattr(e, "status_code", type(e).__name__), str(getattr(e, "detail", e))

    kolom = [(r["table_name"], r["column_name"]) for r in await conn.fetch("""
        SELECT c.table_name, c.column_name FROM information_schema.columns c JOIN information_schema.tables t USING (table_schema, table_name)
        WHERE c.table_schema='public' AND t.table_type='BASE TABLE' AND c.column_name ILIKE '%customer%id%' AND c.data_type='uuid'
          AND c.table_name NOT IN ('customer_activities')""")]
    if len(kolom) < 16:
        print("TAK SAH: turunan kolom pelanggan terlalu sedikit", kolom); sys.exit(2)

    async def tertinggal(cids):
        out = {}
        for tb, col in kolom:
            n = await conn.fetchval(f"SELECT count(*) FROM {tb} WHERE {col} = ANY($1::uuid[])", cids)
            if n:
                out[f"{tb}.{col}"] = n
        return out

    async def gl_ar():
        return await conn.fetchval("""SELECT COALESCE(SUM(jl.debit-jl.credit),0) FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id
            JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id=$1 AND je.status='POSTED' AND c.account_type='RECEIVABLE'""", T)

    S = await conn.fetchval("""SELECT c.id FROM customers c WHERE c.tenant_id=$1 AND c.deleted_at IS NULL AND c.is_active
        AND EXISTS (SELECT 1 FROM sales_orders o WHERE o.customer_id=c.id) AND EXISTS (SELECT 1 FROM credit_notes n WHERE n.customer_id=c.id)
        ORDER BY (SELECT count(*) FROM sales_invoices s WHERE s.customer_id=c.id) DESC LIMIT 1""", T)
    D = await conn.fetchval("SELECT id FROM customers WHERE tenant_id=$1 AND deleted_at IS NULL AND is_active AND id<>$2 ORDER BY created_at LIMIT 1", T, S)
    S_polos = await conn.fetchval("""SELECT c.id FROM customers c WHERE c.tenant_id=$1 AND c.deleted_at IS NULL AND c.is_active AND c.id NOT IN ($2,$3)
        AND EXISTS (SELECT 1 FROM sales_invoices s WHERE s.customer_id=c.id)
        AND NOT EXISTS (SELECT 1 FROM credit_notes n WHERE n.customer_id=c.id) AND NOT EXISTS (SELECT 1 FROM customer_deposits d WHERE d.customer_id=c.id)
        LIMIT 1""", T, S, D)
    D_tb = await conn.fetchval("SELECT id FROM customers WHERE tenant_id=$1 AND deleted_at IS NULL LIMIT 1", TB)
    if not (S and D and S_polos and D_tb):
        print("TAK SAH: subjek", S, D, S_polos, D_tb); sys.exit(2)
    S, D, S_polos, D_tb = str(S), str(D), str(S_polos), str(D_tb)

    luar = conn.transaction(); await luar.start()
    try:
        # ================= S1 bahagia
        sp = conn.transaction(); await sp.start()
        g0 = await gl_ar()
        v0 = {r["tenant_id"]: r["verdict"] for r in await conn.fetch("SELECT tenant_id, verdict FROM verify_ar_reconciliation_all()")}
        tot0 = await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ar_outstanding($1)", T)
        arS = await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ar_outstanding($1) WHERE customer_id=$2", T, S)
        arD = await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ar_outstanding($1) WHERE customer_id=$2", T, D)
        nama0 = {r["id"]: r["customer_name"] for r in await conn.fetch("SELECT id, customer_name FROM sales_invoices WHERE customer_id=$1", U.UUID(S))}
        sisa0 = await tertinggal([S])
        kp, rp = await panggil(m.preview_customer_merge, {"source_ids": [S], "target_id": D})
        k, r = await panggil(m.merge_customers, {"source_ids": [S], "target_id": D})
        sisa1 = await tertinggal([S])
        g1 = await gl_ar()
        v1 = {r_["tenant_id"]: r_["verdict"] for r_ in await conn.fetch("SELECT tenant_id, verdict FROM verify_ar_reconciliation_all()")}
        tot1 = await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ar_outstanding($1)", T)
        arD1 = await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ar_outstanding($1) WHERE customer_id=$2", T, D)
        nama1 = {r_["id"]: r_["customer_name"] for r_ in await conn.fetch("SELECT id, customer_name FROM sales_invoices WHERE id = ANY($1::uuid[])", list(nama0))}
        cda = await conn.fetchval("""SELECT count(*) FROM customer_deposit_applications a JOIN customer_deposits d ON d.id=a.deposit_id
            JOIN sales_invoices s ON s.id=a.invoice_id WHERE d.customer_id <> s.customer_id AND a.status='active'""")
        cnx = await conn.fetchval("SELECT count(*) FROM credit_notes n JOIN sales_invoices s ON s.id=n.original_invoice_id WHERE n.customer_id<>s.customer_id")
        src_row = await conn.fetchrow("SELECT is_active, deleted_at IS NOT NULL dihapus, deleted_by FROM customers WHERE id=$1", U.UUID(S))
        n_audit = await conn.fetchval("""SELECT count(*) FROM audit_logs WHERE "eventType"='CUSTOMER_MERGED' AND entity_id=$1::uuid""", S)
        await sp.rollback()
        catat("BAHAGIA", "gabung -> 200", k == 200, (k, r if k != 200 else ""))
        catat("BAHAGIA", "NOL referensi tertinggal di sumber (kolom turunan information_schema)", k == 200 and not sisa1, (sisa0, sisa1))
        pv = rp["preview"]["records_to_move"] if kp == 200 and isinstance(rp, dict) and "preview" in rp else {}
        mv = r.get("moved", {}) if k == 200 and isinstance(r, dict) else {}
        catat("BAHAGIA", "pratinjau == eksekusi per tabel, dan menutup SEMUA yang ada di sumber",
              bool(mv) and all(pv.get(t_) == n for t_, n in mv.items()) and sum(v for key, v in sisa0.items() if not key.startswith("customer_price_lists")) == sum(v for t_, v in mv.items() if t_ != "customer_price_lists"),
              (pv, mv, sisa0))
        catat("BAHAGIA", "GL piutang, Σcompute, verdikt V248 TIDAK berubah", g0 == g1 and tot0 == tot1 and v0 == v1, (g0, g1, tot0, tot1, v0, v1))
        catat("BAHAGIA", "AR per pelanggan: sasaran naik TEPAT sebesar AR sumber", arD1 - arD == arS, (arS, arD, arD1))
        catat("BAHAGIA", "invariant pihak (DP≠faktur aktif, CN≠faktur) = 0", cda == 0 and cnx == 0, (cda, cnx))
        catat("BAHAGIA", "snapshot customer_name di faktur TETAP", nama0 == nama1 and bool(nama0), len(nama0))
        catat("BAHAGIA", "sumber nonaktif + dihapus, deleted_by teks; audit CUSTOMER_MERGED 1",
              src_row["is_active"] is False and src_row["dihapus"] and src_row["deleted_by"] == str(uid) and n_audit == 1, (tuple(src_row), n_audit))

        # ================= pratinjau tak mengubah apa pun
        sp = conn.transaction(); await sp.start()
        s_a = await tertinggal([S])
        await panggil(m.preview_customer_merge, {"source_ids": [S], "target_id": D})
        s_b = await tertinggal([S])
        await sp.rollback()
        catat("PRATINJAU", "pratinjau tidak memindah apa pun", s_a == s_b, (s_a == s_b))

        # ================= penolakan
        async def tolak(label, body, teks):
            sp = conn.transaction(); await sp.start()
            try:
                sebelum = await tertinggal([S, S_polos])
                k, r = await panggil(m.merge_customers, body)
                sesudah = await tertinggal([S, S_polos])
                aktif = await conn.fetchval("SELECT bool_and(is_active) FROM customers WHERE id = ANY($1::uuid[])", [S, D, S_polos])
            finally:
                await sp.rollback()
            catat("TOLAK", label, k == 400 and teks in str(r) and sebelum == sesudah and aktif, (k, r, sebelum == sesudah, aktif))
            return k, r

        await tolak("sasaran == sumber -> 400, tak ada yang nonaktif", {"source_ids": [S], "target_id": S}, "tidak boleh termasuk")
        sp = conn.transaction(); await sp.start()
        await conn.execute("UPDATE customers SET is_active=false, deleted_at=now() WHERE id=$1::uuid", D)
        k3, r3 = await panggil(m.merge_customers, {"source_ids": [S], "target_id": D})
        sisa3 = await tertinggal([S])
        await sp.rollback()
        catat("TOLAK", "sasaran sudah dihapus -> 400, dokumen tak pindah", k3 == 400 and "nonaktif atau dihapus" in str(r3) and sisa3 == sisa0, (k3, r3))
        await tolak("sumber ganda -> 400", {"source_ids": [S, S], "target_id": D}, "lebih dari sekali")
        await tolak("id bukan uuid -> 400", {"source_ids": ["Toko Melati"], "target_id": D}, "Pelanggan tidak ditemukan")
        k_kr, r_kr = await tolak("sasaran id karangan -> 400", {"source_ids": [S_polos], "target_id": str(U.uuid4())}, "Pelanggan tidak ditemukan")

        # ================= lintas tenant: sumber TANPA CN/DP -> sasaran pelanggan grapgrap (hipotesis kebocoran)
        sp = conn.transaction(); await sp.start()
        n0 = await conn.fetchval("SELECT count(*) FROM sales_invoices WHERE tenant_id=$1 AND customer_id=$2::uuid", T, D_tb)
        k4, r4 = await panggil(m.merge_customers, {"source_ids": [S_polos], "target_id": D_tb})
        n1 = await conn.fetchval("SELECT count(*) FROM sales_invoices WHERE tenant_id=$1 AND customer_id=$2::uuid", T, D_tb)
        await sp.rollback()
        TEMUAN["lintas_tenant"] = {"kode": k4, "respon": str(r4)[:80], "faktur_kaos_menunjuk_pelanggan_grapgrap": n1 - n0}
        catat("TOLAK", "sasaran pelanggan TENANT LAIN (sumber tanpa CN/DP) -> 400 identik dgn id karangan, NOL faktur berpindah",
              k4 == 400 and str(r4) == str(r_kr) and n1 == n0, TEMUAN["lintas_tenant"])
    finally:
        await luar.rollback()
        await conn.close()

    badan = open(__file__, encoding="utf-8").read().split("async def main():", 1)[1]
    harap = len(re.findall(r"^\s+catat\(", badan, re.M)) + (len(re.findall(r"^\s+(?:k_kr, r_kr = )?await tolak\(", badan, re.M)) - 1)
    for s_, u, ok, k_ in hasil:
        print(("[H] " if ok else "[X] ") + f"{s_:9} {u}  | {k_}")
    g = [h[1] for h in hasil if not h[2]]
    print(f"\nMODE={MODE} gagal={len(g)} total={len(hasil)} harap={harap}")
    print("TEMUAN lintas_tenant:", TEMUAN.get("lintas_tenant"))
    if MODE in ("baru", "hidup"):
        sys.exit(0 if not g and len(hasil) == harap else 1)
    wajib = {"lama": ["gabung -> 200"], "lama1": ["NOL referensi tertinggal", "sasaran == sumber", "sasaran sudah dihapus"],
             "sabotase_tabel": ["NOL referensi tertinggal"], "sabotase_tenant": ["sasaran pelanggan TENANT LAIN"]}[MODE]
    ok = all(any(x.startswith(w) for x in g) for w in wajib)
    if MODE.startswith("sabotase"):
        ok = ok and "gabung -> 200" not in g
    print(f"[{MODE}] ->", "SESUAI HARAPAN" if ok else "TAK SESUAI", g); sys.exit(0 if ok else 1)


asyncio.run(main())
