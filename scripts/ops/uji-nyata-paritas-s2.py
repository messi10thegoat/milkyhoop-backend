"""UJI NYATA PARITAS DETAIL == BARIS DAFTAR (21 Okt 2026): Uang Muka, Nota Kredit, Faktur Penjualan. BACA-SAJA (READ ONLY, ROLLBACK),
kaos + grapgrap, SEMUA baris. Pola uji-nyata-paritas-detail (BACKEND2).
Mode: dump <out.json> | banding <lama.json> <baru.json> (medan detail LAMA identik di baru) | paritas (SETIAP medan baris daftar ADA di
detail dengan nilai SAMA -- seluruh baris, bukan daftar pilihan)."""
import asyncio, inspect, json, os, sys
from collections import Counter
from uuid import UUID
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")

gagal = 0


def cek(ok, l, i=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else "")); gagal += 0 if ok else 1


if sys.argv[1] == "banding":
    a, b = json.load(open(sys.argv[2])), json.load(open(sys.argv[3]))
    cek(sorted(a) == sorted(b), f"kunci sama ({len(a)} detail)")
    beda = Counter()
    for k, d in a.items():
        for f, v in d.items():
            if b[k].get(f) != v or f not in b[k]:
                beda[(k.split("|")[1], f)] += 1
    cek(not beda, f"medan detail LAMA identik di baru ({len(a)} detail)", str(dict(list(beda.items())[:5])))
    print("RINGKAS gagal=%d" % gagal); sys.exit(0)

import asyncpg
from fastapi.params import Query as QP
from starlette.requests import Request
from app.routers import customer_deposits as CD, credit_notes as CN, sales_invoices as SI


def req(t, uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}, receive); r.state.user = {"tenant_id": t, "user_id": uid}; return r


def kw(fn, r, **ov):
    o = {}
    for n, p in inspect.signature(fn).parameters.items():
        if n == "request": o[n] = r
        elif n in ov: o[n] = ov[n]
        elif isinstance(p.default, QP): o[n] = p.default.default
        elif p.default is not inspect._empty: o[n] = p.default
    return o


def J(x): return json.loads(json.dumps(x if isinstance(x, dict) else x.model_dump(), default=str))


async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"]); tx = c.transaction(readonly=True); await tx.start()
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    for m in (CD, CN, SI): m.get_pool = gp
    out = {}
    try:
        for t in ("kaos-biru-konveksi", "grapgrap-manado"):
            uid = str(await c.fetchval('SELECT id FROM "User" WHERE "tenantId"=$1 ORDER BY "createdAt" LIMIT 1', t))
            r = req(t, uid)
            for nama, lf, df, idk in (("UM", CD.list_customer_deposits, CD.get_customer_deposit, "deposit_id"),
                                      ("NK", CN.list_credit_notes, CN.get_credit_note, "credit_note_id"),
                                      ("Faktur", SI.list_invoices, SI.get_invoice, "invoice_id")):
                rows, skip = [], 0
                while True:
                    x = J(await lf(**kw(lf, r, skip=skip, limit=100)))
                    rows += x["items"]; skip += 100
                    if skip >= x["total"]: break
                hilang, beda, contoh = Counter(), Counter(), {}
                for row in rows:
                    d = J(await df(**kw(df, r, **{idk: UUID(row["id"])})))["data"]
                    out[f"{t}|{nama}|{row['id']}"] = d
                    for k, v in row.items():
                        if k not in d: hilang[k] += 1
                        elif d[k] != v:
                            beda[k] += 1; contoh.setdefault(k, (row["id"][:8], str(v)[:40], str(d[k])[:40]))
                if sys.argv[1] == "paritas":
                    cek(not hilang and not beda, f"{t} {nama}: SETIAP medan baris daftar == detail ({len(rows)} baris)",
                        f"hilang={dict(hilang)} beda={dict(beda)} {contoh}")
    finally:
        await tx.rollback(); await c.close()
    if sys.argv[1] == "dump":
        json.dump(out, open(sys.argv[2], "w"), sort_keys=True); print("dump", len(out))
    print("RINGKAS gagal=%d" % gagal)

asyncio.run(main())
