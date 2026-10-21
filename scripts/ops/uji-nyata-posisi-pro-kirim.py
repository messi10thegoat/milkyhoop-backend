"""UJI NYATA position_text Proforma + Pengiriman (21 Okt 2026, putusan MASTER). BACA-SAJA, kaos+grapgrap, SEMUA baris.
 - baris daftar == detail == anchor /…/{id}/documents (SATU fungsi: proforma_daftar.position_text / pengiriman_daftar.position_text)
 - nilai == aturan yang dihitung ULANG di sini dari medan LAIN baris (bukan dari fungsi yang diuji):
     Proforma  : draft/cancelled/expired -> Draf/Dibatalkan/Kedaluwarsa; terbit -> is_fully_paid ? Lunas : paid_amount>0 ? Dibayar sebagian : Menunggu bayar
                 (== teks inline anchor sebelum unit ini)
     Pengiriman: voided -> "Dibatalkan"; selain itu == serah_teks
 - MERAH=1 : kontrol merah -- harapan satu baris per modul dibalik; harness WAJIB melapor gagal."""
import asyncio, inspect, json, os, sys
from uuid import UUID
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from fastapi.params import Query as QP
from starlette.requests import Request
from app.routers import deliveries as DL, proformas as PF, dokumen as DK

TEN = ["kaos-biru-konveksi", "grapgrap-manado"]
MERAH = os.environ.get("MERAH") == "1"
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
    if hasattr(x, "body"): return json.loads(x.body)
    if hasattr(x, "model_dump"): return x.model_dump(mode="json")
    return json.loads(json.dumps(x, default=str))


async def semua(fn, r, pagekey):
    items, n = [], 0
    while True:
        res = J(await fn(**kw(fn, r, **({"limit": 100, "skip": n} if pagekey == "skip" else {"per_page": 100, "page": n // 100 + 1}))))
        items += res["items"]; n += 100
        if not res.get("has_more") or n > 3000: return items


def harap_proforma(x):
    st = {"draft": "Draf", "cancelled": "Dibatalkan", "expired": "Kedaluwarsa"}.get(x["status"])
    if st: return st
    return "Lunas" if x.get("is_fully_paid") else ("Dibayar sebagian" if float(x.get("paid_amount") or 0) > 0 else "Menunggu bayar")


def harap_kirim(x):
    return "Dibatalkan" if x["status"] == "voided" else x["serah_teks"]


async def main():
    c = await asyncpg.connect(os.environ["URL"]); await c.execute("SET default_transaction_read_only = on")
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    for m in (DL, PF, DK): m.get_pool = gp
    for t in TEN:
        uid = str(await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", t)); r = req(t, uid)
        ctx = {"tenant_id": t}
        M = {"Proforma": (PF.list_proformas, "skip", PF.get_proforma_detail, "proforma_id", lambda x: x["data"], DK.susun_dokumen_proforma, harap_proforma, "proforma_number"),
             "Pengiriman": (DL.list_deliveries, "page", DL.get_delivery_detail, "delivery_id", lambda x: x, DK.susun_dokumen_pengiriman, harap_kirim, "delivery_number")}
        for nama, (lf, pk, df, idn, ambil, susun, harap, nomor) in M.items():
            rows = await semua(lf, r, pk)
            hit, sebaran = 0, {}
            for i, row in enumerate(rows):
                det = ambil(J(await df(**kw(df, r, **{idn: row["id"]}))))
                anc = J(await susun(c, ctx, UUID(row["id"])))["anchor"]["position_text"]
                h = harap(row)
                if MERAH and i == 0: h = h + " (SABOTASE)"
                sebaran[row.get("position_text")] = sebaran.get(row.get("position_text"), 0) + 1
                if "position_text" not in row or "position_text" not in det:
                    gagal.append((t, nama, row.get(nomor), "kunci position_text hilang")); continue
                if not (row["position_text"] == det["position_text"] == anc == h):
                    gagal.append((t, nama, row.get(nomor), row["status"], row["position_text"], det["position_text"], anc, h)); continue
                hit += 1
            print(f"{'PASS' if hit == len(rows) else 'FAIL'} {t} {nama}: baris==detail==anchor==aturan {hit}/{len(rows)} | sebaran {json.dumps(sebaran, ensure_ascii=False)}")
    await c.close()
    print("RINGKAS gagal=%d %s" % (len(gagal), gagal[:5])); sys.exit(1 if gagal else 0)

asyncio.run(main())
