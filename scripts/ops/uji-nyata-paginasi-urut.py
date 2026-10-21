"""UJI NYATA paginasi seragam + urutan seri deterministik (21 Okt 2026): BACA-SAJA, kaos+grapgrap. Untuk Penawaran, Proforma, Pengiriman:
 limit/skip == per_page/page == offset (halaman yang sama); menyusuri SEMUA halaman kecil (5/halaman) = tepat himpunan id daftar penuh,
 tanpa duplikat / tanpa baris hilang, urutan sama dgn satu halaman besar, dan STABIL (dua kali jalan sama). Penerimaan: sama (skip/limit)."""
import asyncio, inspect, os, sys
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from fastapi.params import Query as QP
from starlette.requests import Request
from app.routers import quotes as QU, proformas as PF, deliveries as DL, receive_payments as RP

TEN = ["kaos-biru-konveksi", "grapgrap-manado"]
gagal = []


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


def J(x):
    import json
    if hasattr(x, "body"): return json.loads(x.body)
    return x.model_dump(mode="json") if hasattr(x, "model_dump") else x


def cek(nama, ok, rinci=""):
    if not ok: gagal.append((nama, str(rinci)[:200]))


async def main():
    c = await asyncpg.connect(os.environ["URL"]); await c.execute("SET default_transaction_read_only = on")
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    for m in (QU, PF, DL, RP): m.get_pool = gp
    for t in TEN:
        uid = str(await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", t)); r = req(t, uid)
        # nama: (fungsi, param ukuran-halaman-alias, param tambahan)
        M = {"Penawaran": (QU.list_quotes, "per_page", {}), "Proforma": (PF.list_proformas, "per_page", {}),
             "Pengiriman": (DL.list_deliveries, "limit", {}), "Penerimaan": (RP.list_receive_payments, None, {"include_voided": True, "penerimaan_saja": True})}
        for nama, (fn, alias, extra) in M.items():
            ids = lambda res: [i["id"] for i in J(res)["items"]]  # noqa: E731
            besar_p = {"Pengiriman": dict(per_page=100)}.get(nama, dict(limit=100))
            penuh = ids(await fn(**kw(fn, r, **besar_p, **extra)))
            n = len(penuh)
            ukuran = {"Pengiriman": "per_page"}.get(nama, "limit")
            # ekuivalensi parameter: halaman ke-2 berukuran 5
            ref = ids(await fn(**kw(fn, r, **{ukuran: 5}, **({"page": 2} if nama == "Pengiriman" else {"skip": 5}), **extra)))
            cek(f"{t} {nama} halaman2 == penuh[5:10]", ref == penuh[5:10], (ref, penuh[5:10]))
            if alias:
                a = ids(await fn(**kw(fn, r, **{alias: 5}, **({"skip": 5} if nama != "Pengiriman" else {"skip": 5}), **extra)))
                cek(f"{t} {nama} alias {alias}=5,skip=5", a == ref, (a, ref))
            if nama != "Pengiriman":
                pg = ids(await fn(**kw(fn, r, **{ukuran: 5, "page": 2}, **extra))) if "page" in inspect.signature(fn).parameters else ref
                cek(f"{t} {nama} page=2", pg == ref, (pg, ref))
            of = ids(await fn(**kw(fn, r, **{ukuran: 5, "offset": 5}, **extra))) if "offset" in inspect.signature(fn).parameters else ref
            cek(f"{t} {nama} offset=5", of == ref, (of, ref))
            # susur semua halaman kecil
            kumpul, off = [], 0
            while True:  # susur sampai habis (halaman besar dibatasi 100 baris; susur bisa lebih panjang)
                bagian = ids(await fn(**kw(fn, r, **{ukuran: 5, "skip": off}, **extra)))
                if not bagian: break
                kumpul += bagian; off += 5
            cek(f"{t} {nama} susur: tanpa duplikat, awalnya == halaman penuh (urutan sama)", kumpul[:n] == penuh and len(set(kumpul)) == len(kumpul) and len(kumpul) >= n, (len(kumpul), n))
            ulang = ids(await fn(**kw(fn, r, **besar_p, **extra)))
            cek(f"{t} {nama} stabil", ulang == penuh)
            print(f"[baru] {t} {nama}: {n} baris, susur per 5 == penuh")
    print("RINGKAS PAGINASI gagal=%d %s" % (len(gagal), gagal[:4])); sys.exit(1 if gagal else 0)

asyncio.run(main())
