"""Harness NYATA S1 detail faktur (20 Okt 2026), BACA saja (transaksi read-only, ROLLBACK), grapgrap + kaos, SEMUA faktur.
Untuk tiap faktur di daftar (semua halaman): GET detail == baris daftar pada medan S1; position_text == kolom Status FE r288
(statusSiklusFaktur dari cache status) -- terukur 20 Okt cache == jurnal; jumlah faktur daftar == DB."""
import asyncio, inspect, json, os, sys
sys.path.insert(0, "/app"); os.environ.setdefault("OPENAI_API_KEY", "x")
from types import SimpleNamespace
import asyncpg
from backend.api_gateway.app.routers import sales_invoices as SI

MEDAN = ("position_text", "aksi", "jatuh_tempo_terbuka", "jatuh_tempo_hari", "jatuh_tempo_minggu_ini",
         "jatuh_tempo_keadaan", "documents", "is_overdue")
FE_R288 = {"draft": "Draf", "void": "Batal", "paid": "Lunas", "partial": "Sebagian", "posted": "Belum lunas"}
gagal = 0


def cek(ok, l, i=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else "")); gagal += 0 if ok else 1


def kw(fn, **o):
    d = {n: getattr(p.default, "default", p.default) for n, p in inspect.signature(fn).parameters.items()
         if n != "request" and p.default is not inspect.Parameter.empty}
    d.update(o); return d


def js(o): return json.loads(json.dumps(o, sort_keys=True, default=str))


async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    tx = c.transaction(readonly=True); await tx.start()
    class Pool:
        def acquire(self_):
            class A:
                async def __aenter__(s): return c
                async def __aexit__(s, *a): return False
            return A()
    async def gp(): return Pool()
    SI.get_pool = gp
    try:
        for t in ("grapgrap-manado", "kaos-biru-konveksi"):
            uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId"=$1 ORDER BY "createdAt" LIMIT 1', t)
            req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": t, "user_id": str(uid)}), headers={})
            baris, skip = {}, 0
            while True:
                r = await SI.list_invoices(req, **kw(SI.list_invoices, skip=skip, limit=100))
                for x in r["items"]: baris[x["id"]] = x
                skip += 100
                if skip >= r["total"]: break
            n_db = await c.fetchval("SELECT count(*) FROM sales_invoices WHERE tenant_id=$1", t)
            cek(len(baris) == n_db, f"{t}: daftar memuat semua faktur", f"{len(baris)} vs DB {n_db}")
            beda, beda_fe, hit = [], [], {}
            for iid, x in baris.items():
                d = js((await SI.get_invoice(req, iid))["data"])
                xx = js(x)
                for f in MEDAN:
                    if d.get(f) != xx.get(f):
                        beda.append((x["invoice_number"], f, d.get(f), xx.get(f)))
                if d["position_text"] != FE_R288.get(x["status"]):
                    beda_fe.append((x["invoice_number"], x["status"], d["position_text"]))
                hit[d["position_text"]] = hit.get(d["position_text"], 0) + 1
            cek(not beda, f"{t}: detail == baris daftar pada {len(MEDAN)} medan ({len(baris)} faktur)", str(beda[:3]))
            cek(not beda_fe, f"{t}: position_text == kolom Status FE r288", str(beda_fe[:3]))
            print("  posisi", t, json.dumps(hit, sort_keys=True))
    finally:
        await tx.rollback(); await c.close()
    print("RINGKAS gagal=%d" % gagal)

asyncio.run(main())
