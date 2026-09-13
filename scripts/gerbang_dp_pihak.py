"""GERBANG DP pelanggan -> faktur: pihak SAMA. Kode LAMA vs BARU atas data sama, ROLLBACK.

Sisi (hasil di memori Python, cacah di-assert):
  MERAH lama   pelanggan BEDA -> 200 (celah nyata)
  TOLAK baru   pelanggan BEDA -> 400; nol jurnal baru, nol aplikasi baru, amount_paid diam
  HIJAU baru   pelanggan SAMA -> 200; outstanding -1000, amount_paid +1000, jurnal DEPOSIT_APPLICATION
  TOLAK baru   DP tanpa pelanggan (disetel NULL di savepoint) -> 400
  TOLAK baru   multi-aplikasi [SAMA, BEDA] -> 400 dan NOL tulis (diperiksa semua sebelum tulis)
  SABOTASE     pastikan_pihak_sama dijadikan no-op -> BEDA 200 (gerbang bisa merah)
  KONTROL      nol baris menetap
Modul baru dimuat dari /tmp (main tree tak disentuh); pihak_helpers disuntik ke sys.modules.
"""
import asyncio
import importlib.util
import os
import sys

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402
from starlette.requests import Request  # noqa: E402

T = "kaos-biru-konveksi"
HARAP = 9
hasil = []


def catat(sisi, uji, ok, ket=""):
    hasil.append((sisi, uji, bool(ok), str(ket)[:240]))


def muat(nama, path):
    spec = importlib.util.spec_from_file_location(nama, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[nama] = m
    spec.loader.exec_module(m)
    return m


def req(uid):
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b"",
                    "state": {"user": {"tenant_id": T, "user_id": str(uid)}}})


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

    muat("app.services.pihak_helpers", "/tmp/pihak_helpers.py")
    from app.routers import customer_deposits as lama
    baru = muat("app.routers.customer_deposits_baru", "/tmp/customer_deposits_baru.py")
    src = open("/tmp/customer_deposits_baru.py", encoding="utf-8").read()
    if src.count("pastikan_pihak_sama(\n") + src.count("pastikan_pihak_sama(") < 2:
        catat("PRASYARAT", "helper dipanggil di modul baru", False, "tak ditemukan")
    from app.schemas.customer_deposits import ApplyCustomerDepositRequest as Req, ApplyDepositItem as Item

    uid = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)
    je0 = await conn.fetchval("SELECT count(*) FROM journal_entries")
    ap0 = await conn.fetchval("SELECT count(*) FROM customer_deposit_applications")

    base = """
      SELECT d.id AS dep, s.id AS inv FROM customer_deposits d
      JOIN sales_invoices s ON s.tenant_id = d.tenant_id AND s.journal_id IS NOT NULL
      JOIN compute_ar_outstanding($1) a ON a.invoice_id = s.id
      WHERE d.tenant_id=$1 AND d.status IN ('posted','partial')
        AND d.amount - COALESCE(d.amount_applied,0) - COALESCE(d.amount_refunded,0) >= 2000
        AND a.outstanding >= 1000
        AND NOT EXISTS (SELECT 1 FROM customer_deposit_applications x WHERE x.deposit_id=d.id AND x.invoice_id=s.id)
    """
    luar = conn.transaction(); await luar.start()
    try:
        sama = await conn.fetchrow(base + " AND lower(d.customer_id::text)=lower(s.customer_id::text) ORDER BY d.id, s.id LIMIT 1", T)
        beda = await conn.fetchrow(
            base + " AND lower(d.customer_id::text)<>lower(s.customer_id::text) AND d.id = $2 ORDER BY s.id LIMIT 1", T, sama["dep"])

        async def jalan(mod, dep, apps, persiapan=None):
            sp = conn.transaction(); await sp.start()
            if persiapan:
                await conn.execute(persiapan, dep)
            j0 = await conn.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id=$1", T)
            a0 = await conn.fetchval("SELECT count(*) FROM customer_deposit_applications")
            p0 = {i: await conn.fetchval("SELECT amount_paid FROM sales_invoices WHERE id=$1", i) for i, _ in apps}
            o0 = {i: await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ar_outstanding($1) WHERE invoice_id=$2", T, i) for i, _ in apps}
            try:
                await mod.apply_customer_deposit(req(uid), dep, Req(applications=[Item(invoice_id=str(i), amount=n) for i, n in apps]))
                kode, det = 200, "OK"
            except Exception as e:  # noqa: BLE001
                kode, det = getattr(e, "status_code", type(e).__name__), getattr(e, "detail", str(e))
            j1 = await conn.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id=$1", T)
            a1 = await conn.fetchval("SELECT count(*) FROM customer_deposit_applications")
            p1 = {i: await conn.fetchval("SELECT amount_paid FROM sales_invoices WHERE id=$1", i) for i, _ in apps}
            o1 = {i: await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ar_outstanding($1) WHERE invoice_id=$2", T, i) for i, _ in apps}
            jr = await conn.fetchval(
                "SELECT count(*) FROM customer_deposit_applications x JOIN journal_entries je ON je.id=x.journal_id WHERE x.deposit_id=$1 AND je.source_type='DEPOSIT_APPLICATION'", dep)
            await sp.rollback()
            return dict(kode=kode, det=det, dj=j1 - j0, da=a1 - a0,
                        dp={str(i)[:8]: (p1[i] or 0) - (p0[i] or 0) for i, _ in apps},
                        do={str(i)[:8]: o1[i] - o0[i] for i, _ in apps}, jr=jr)

        r = await jalan(lama, beda["dep"], [(beda["inv"], 1000)])
        catat("MERAH lama", "pelanggan BEDA -> 200 (celah nyata)", r["kode"] == 200 and r["dj"] >= 1, r)

        r = await jalan(baru, beda["dep"], [(beda["inv"], 1000)])
        catat("TOLAK baru", "pelanggan BEDA -> 400, nol jurnal, nol aplikasi, amount_paid diam",
              r["kode"] == 400 and r["dj"] == 0 and r["da"] == 0 and all(v == 0 for v in r["dp"].values()), r)
        catat("TOLAK baru", "pesan menyebut pelanggan lain", "pelanggan lain" in str(r["det"]), r["det"])

        r = await jalan(baru, sama["dep"], [(sama["inv"], 1000)])
        catat("HIJAU baru", "pelanggan SAMA -> 200", r["kode"] == 200, r)
        catat("HIJAU baru", "efek penuh: outstanding -1000, amount_paid +1000, 1 aplikasi, jurnal DEPOSIT_APPLICATION",
              r["da"] == 1 and list(r["do"].values())[0] == -1000 and list(r["dp"].values())[0] == 1000 and r["jr"] >= 1, r)

        r = await jalan(baru, sama["dep"], [(sama["inv"], 1000)],
                        persiapan="UPDATE customer_deposits SET customer_id = NULL WHERE id = $1")
        catat("TOLAK baru", "DP tanpa pelanggan -> 400, nol tulis", r["kode"] == 400 and r["dj"] == 0 and r["da"] == 0, r)

        r = await jalan(baru, sama["dep"], [(sama["inv"], 1000), (beda["inv"], 1000)])
        catat("TOLAK baru", "multi [SAMA, BEDA] -> 400 dan NOL tulis (faktur SAMA pun tak tersentuh)",
              r["kode"] == 400 and r["dj"] == 0 and r["da"] == 0 and all(v == 0 for v in r["dp"].values()), r)

        import app.routers.customer_deposits_baru as mb
        asli = mb.pastikan_pihak_sama
        mb.pastikan_pihak_sama = lambda *a, **k: None
        r = await jalan(mb, beda["dep"], [(beda["inv"], 1000)])
        mb.pastikan_pihak_sama = asli
        catat("SABOTASE", "helper no-op -> BEDA lolos 200 (gerbang bisa merah)", r["kode"] == 200, r)
    finally:
        await luar.rollback()

    je1 = await conn.fetchval("SELECT count(*) FROM journal_entries")
    ap1 = await conn.fetchval("SELECT count(*) FROM customer_deposit_applications")
    catat("KONTROL", "nol baris menetap", je0 == je1 and ap0 == ap1, f"je {je0}->{je1} app {ap0}->{ap1}")
    await conn.close()
    for s, u, ok, k in hasil:
        print(("[H] " if ok else "[X] ") + f"{s:11} {u}  | {k}")
    g = sum(1 for h in hasil if not h[2])
    print(f"\ngagal={g} total={len(hasil)} harap={HARAP} -> {'LENGKAP' if len(hasil) == HARAP else 'TAK SAH'}")
    sys.exit(0 if g == 0 and len(hasil) == HARAP else 1)


asyncio.run(main())
