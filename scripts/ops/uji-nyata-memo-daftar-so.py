"""UJI NYATA PERF-DAFTAR-SO (16 Okt 2026): handler daftar/summary SO dengan memo (terbungkus) == tanpa memo (__wrapped__) atas data
kaos+grapgrap, BACA-SAJA (default_transaction_read_only=on). Membandingkan isi respons penuh + jumlah kueri + waktu (p50 x N).
Jalankan di kontainer sekali-pakai gateway: docker run --rm --network <jaringan> -e URL=<dsn> -v <pohon>/backend/api_gateway:/app/backend/api_gateway:ro
-v <skrip>:/u.py:ro -w /app/backend/api_gateway --entrypoint python <image> /u.py"""
import asyncio, inspect, json, os, statistics, sys, time
sys.path.insert(0, "/app/backend/api_gateway")
os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from fastapi.params import Query as QP
from starlette.requests import Request
from app.routers import sales_orders as SO

N = int(os.environ.get("N", "10"))
TEN = ["kaos-biru-konveksi", "grapgrap-manado"]
CNT = {"n": 0}
gagal = []


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


def isi(x):
    return json.dumps(x if isinstance(x, dict) else x.model_dump(mode="json"), sort_keys=True, default=str)


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
    SO.get_pool = gp
    L, S = SO.list_sales_orders, SO.get_sales_order_summary
    for t in TEN:
        uid = str(await conn.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", t))
        V = [("list100", L, dict(limit=100)), ("list100+totals", L, dict(limit=100, with_totals=True)),
             ("list skip100", L, dict(limit=100, skip=100, with_totals=True)), ("list search", L, dict(limit=100, search="a", with_totals=True)),
             ("list sort sisa", L, dict(limit=100, sort_by="outstanding", with_totals=True)),
             ("list sort sisa asc", L, dict(limit=100, sort_by="outstanding", sort_dir="asc")),
             ("summary", S, dict()), ("summary search", S, dict(search="a"))]
        for st in ("draft", "confirmed", "partial_shipped", "shipped", "partial_invoiced", "invoiced", "completed", "cancelled"):
            V.append((f"list status={st}", L, dict(limit=100, status=st, with_totals=True)))
        for tg in ("harus_kirim", "dp_belum_diterima", "menunggu_tagih", "selesai", "tersedia"):
            V.append((f"list tugas={tg}", L, dict(limit=100, tugas=tg, with_totals=True)))
        for ak in ("telat_kirim", "perlu_minta_dp", "menunggu_dp", "perlu_dikonfirmasi", "perlu_dikirim", "siap_diserahkan", "perlu_ditagih"):
            V.append((f"list aksi={ak}", L, dict(limit=100, aksi=ak, with_totals=True)))
        print("==", t)
        for nama, fn, ov in V:
            hasil = {}
            for mode, f in (("lama", fn.__wrapped__), ("baru", fn)):
                ts = []
                for i in range(N if nama in ("list100+totals", "summary", "list sort sisa") else 1):
                    CNT["n"] = 0
                    t0 = time.perf_counter()
                    r = await f(**kw_for(f, req(t, uid), **ov))
                    ts.append((time.perf_counter() - t0) * 1000)
                hasil[mode] = (isi(r), CNT["n"], statistics.median(ts))
            ok = hasil["lama"][0] == hasil["baru"][0]
            if not ok:
                gagal.append((t, nama))
            print(f"  {'ok  ' if ok else 'BEDA'} {nama:28s} kueri {hasil['lama'][1]:3d}->{hasil['baru'][1]:3d}  p50 {hasil['lama'][2]:6.1f}->{hasil['baru'][2]:6.1f} ms")
    await conn.close()
    print("RINGKAS BEDA=%d %s" % (len(gagal), gagal))
    sys.exit(1 if gagal else 0)

asyncio.run(main())
