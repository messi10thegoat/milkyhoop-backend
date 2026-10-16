"""Harness NYATA chip "Jatuh tempo minggu ini" (BACA saja, ROLLBACK), grapgrap + kaos, kode cabang dipasang:
baris ?aksi=jatuh_tempo_minggu_ini == baris daftar penuh berflag jatuh_tempo_minggu_ini == summary _count (dan _value ==
Σ sisa baris); juga dengan ?search=. Hari = tanggal bisnis tenant; akhir minggu = dashboard_v2.akhir_minggu."""
import asyncio, inspect, json, os, sys
sys.path.insert(0, "/app"); os.environ.setdefault("OPENAI_API_KEY", "x")
from decimal import Decimal
from types import SimpleNamespace
import asyncpg
from backend.api_gateway.app.routers import sales_invoices as SI
gagal = 0
def cek(ok, l, i=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else "")); gagal += 0 if ok else 1
def kw(fn, **o):
    d = {n: getattr(p.default, "default", p.default) for n, p in inspect.signature(fn).parameters.items()
         if n != "request" and p.default is not inspect.Parameter.empty}
    d.update(o); return d
async def semua(req, **f):
    out, skip = [], 0
    while True:
        r = await SI.list_invoices(req, **kw(SI.list_invoices, skip=skip, limit=100, **f))
        out += r["items"]; skip += 100
        if skip >= r["total"]: return out, r["total"]
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
            req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": t, "user_id": None}), headers={})
            w = (await c.fetchval("SELECT split_part(customer_name,' ',1) FROM sales_invoices WHERE tenant_id=$1 GROUP BY 1 ORDER BY count(*) DESC LIMIT 1", t)) or "a"
            for cari in (None, w):
                f = {"search": cari} if cari else {}
                penuh, _ = await semua(req, **f)
                fl = sorted(r["id"] for r in penuh if r["jatuh_tempo_minggu_ini"])
                ak, tot = await semua(req, aksi="jatuh_tempo_minggu_ini", **f)
                s = (await SI.get_invoice_summary(req, **kw(SI.get_invoice_summary, **({"search": cari} if cari else {}))))["data"]
                nilai = sum((Decimal(r["outstanding_amount"]) for r in ak), Decimal(0))
                cek(sorted(r["id"] for r in ak) == fl and tot == len(fl) == s["jatuh_tempo_minggu_ini_count"]
                    and abs(Decimal(str(s["jatuh_tempo_minggu_ini_value"])) - nilai) < Decimal("0.01"),
                    f"{t} search={cari}: ?aksi == baris berflag == summary", f"{len(fl)} faktur, Σ sisa {nilai}")
                bad = [r["invoice_number"] for r in penuh if r["jatuh_tempo_minggu_ini"] and not (
                    r["jatuh_tempo_terbuka"] and r["jatuh_tempo_hari"] is not None and r["jatuh_tempo_hari"] >= 0)]
                cek(not bad, f"{t} search={cari}: berflag ⇒ terbuka & jatuh_tempo_hari ≥ 0 (telat tak ikut)", str(bad[:3]))
    finally:
        await tx.rollback(); await c.close()
    print("RINGKAS gagal=%d" % gagal)
asyncio.run(main())
