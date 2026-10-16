"""UKUR kinerja daftar Penjualan (16 Okt 2026). READ-ONLY (default_transaction_read_only=on). Hanya angka, tanpa data pribadi."""
import asyncio, inspect, os, statistics, sys, time
sys.path.insert(0, "/app/backend/api_gateway")
os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from starlette.requests import Request
from fastapi.params import Query as QP
from app.routers import sales_orders as SO, sales_invoices as SI

N = 20
TEN = ["kaos-biru-konveksi", "grapgrap-manado"]
CNT = {"n": 0}


class CW:
    def __init__(s, c): s._c = c
    def __getattr__(s, k):
        a = getattr(s._c, k)
        if k in ("fetch", "fetchrow", "fetchval", "execute", "executemany"):
            async def w(*x, **kw):
                CNT["n"] += 1
                return await a(*x, **kw)
            return w
        return a


def req(t, uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": t, "user_id": uid}
    return r


def kw_for(fn, r, **ov):
    out = {}
    for n, p in inspect.signature(fn).parameters.items():
        if n == "request": out[n] = r
        elif n in ov: out[n] = ov[n]
        elif isinstance(p.default, QP): out[n] = p.default.default
        elif p.default is not inspect._empty: out[n] = p.default
    return out


def pct(v, q):
    v = sorted(v); return v[min(len(v) - 1, int(round(q * (len(v) - 1))))]


async def main():
    conn = await asyncpg.connect(os.environ["URL"])
    await conn.execute("SET default_transaction_read_only = on")
    w = CW(conn)
    class CM:
        async def __aenter__(s): return w
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    SO.get_pool = gp; SI.get_pool = gp
    for t in TEN:
        uid = str(await conn.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", t))
        nso = await conn.fetchval("SELECT count(*) FROM sales_orders WHERE tenant_id=$1", t)
        nsi = await conn.fetchval("SELECT count(*) FROM sales_invoices WHERE tenant_id=$1", t)
        print(f"== {t}: SO={nso} SI={nsi}")
        V = [
            ("SO list100", SO.list_sales_orders, dict(limit=100)),
            ("SO list100+totals", SO.list_sales_orders, dict(limit=100, with_totals=True)),
            ("SO list100 skip100", SO.list_sales_orders, dict(limit=100, skip=100)),
            ("SO list100 search", SO.list_sales_orders, dict(limit=100, search="a")),
            ("SO summary", SO.get_sales_order_summary, dict()),
            ("SO summary search", SO.get_sales_order_summary, dict(search="a")),
            ("SI list100", SI.list_invoices, dict(limit=100)),
            ("SI list100+totals", SI.list_invoices, dict(limit=100, with_totals=True)),
            ("SI list100 skip100", SI.list_invoices, dict(limit=100, skip=100)),
            ("SI list100 search", SI.list_invoices, dict(limit=100, search="a")),
            ("SI summary", SI.get_invoice_summary, dict()),
            ("SI summary search", SI.get_invoice_summary, dict(search="a")),
        ]
        for nama, fn, ov in V:
            ts = []; qs = []; err = None
            for i in range(N):
                CNT["n"] = 0
                t0 = time.perf_counter()
                try:
                    await fn(**kw_for(fn, req(t, uid), **ov))
                except Exception as e:
                    err = type(e).__name__ + ":" + str(getattr(e, "detail", e))[:60]; break
                ts.append((time.perf_counter() - t0) * 1000); qs.append(CNT["n"])
            if err: print(f"  {nama:22s} ERR {err}"); continue
            print(f"  {nama:22s} p50={statistics.median(ts):7.1f}ms p95={pct(ts,.95):7.1f}ms max={max(ts):7.1f}ms q/req={qs[-1]}")
    await conn.close()

asyncio.run(main())
