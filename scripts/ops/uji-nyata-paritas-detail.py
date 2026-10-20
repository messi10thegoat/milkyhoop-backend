"""UJI NYATA PARITAS DETAIL == BARIS DAFTAR (21 Okt 2026): Penawaran, Penerimaan, Pengiriman, Proforma. BACA-SAJA, kaos+grapgrap, SEMUA baris.
Mode: lama <out.json> | baru <out.json> (+paritas) | banding <lama.json> <baru.json>. Syarat: medan detail lama IDENTIK; di 'baru' tiap medan paritas ADA di detail dan
NILAINYA == baris daftar (fungsi sama)."""
import asyncio, inspect, json, os, sys
from uuid import UUID
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from fastapi.params import Query as QP
from starlette.requests import Request
from app.routers import quotes as QU, receive_payments as RP, deliveries as DL, proformas as PF

TEN = ["kaos-biru-konveksi", "grapgrap-manado"]
gagal = []
PAR = {
    "Penawaran": ["position_text", "aksi", "documents", "doc_count", "kedaluwarsa_terbuka", "kedaluwarsa_hari", "kedaluwarsa_keadaan", "kedaluwarsa_teks", "is_expired", "status",
                  "quote_number", "sales_order_id", "sales_order_number", "order_code", "order_title"],
    "Penerimaan": ["dialokasikan", "belum_dialokasikan", "aksi", "alokasi_keadaan", "alokasi_teks", "documents", "doc_count", "invoice_count", "payment_number", "settlement_type"],
    "Pengiriman": ["serah_keadaan", "serah_teks", "aksi", "documents", "doc_count", "status", "delivery_number", "asal", "order_code", "order_title"],
    "Proforma": ["aksi", "jatuh_tempo_terbuka", "jatuh_tempo_hari", "jatuh_tempo_keadaan", "jatuh_tempo_teks", "documents", "doc_count", "status", "proforma_number",
                 "outstanding_amount", "paid_amount", "is_fully_paid", "order_code", "order_title"],
}
BARU = {"Penawaran": {"position_text", "aksi", "documents", "doc_count"}, "Penerimaan": {"dialokasikan", "belum_dialokasikan", "aksi", "alokasi_keadaan", "alokasi_teks", "documents", "doc_count", "invoice_count"},
        "Pengiriman": {"documents", "doc_count"}, "Proforma": {"aksi", "documents", "doc_count", "jatuh_tempo_terbuka", "jatuh_tempo_hari", "jatuh_tempo_keadaan", "jatuh_tempo_teks"}}


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


def cek(nama, ok, rinci=""):
    if not ok: gagal.append((nama, str(rinci)[:240]))


async def semua(fn, r, pagekey, **ov):
    items, n = [], 0
    while True:
        res = J(await fn(**kw(fn, r, **ov, **({"limit": 100, "skip": n} if pagekey == "skip" else {"per_page": 100, "page": n // 100 + 1}))))
        items += res["items"]; n += 100
        if not res.get("has_more") or n > 3000: return items


async def main():
    mode = sys.argv[1]
    c = await asyncpg.connect(os.environ["URL"]); await c.execute("SET default_transaction_read_only = on")
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    for m in (QU, RP, DL, PF): m.get_pool = gp
    out = {}
    for t in TEN:
        uid = str(await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", t)); r = req(t, uid)
        M = {"Penawaran": (QU.list_quotes, "skip", {}, QU.get_quote_detail, "quote_id", lambda x: x["data"]),
             "Penerimaan": (RP.list_receive_payments, "skip", {"include_voided": True}, RP.get_receive_payment, "payment_id", lambda x: x["data"]),
             "Pengiriman": (DL.list_deliveries, "page", {}, DL.get_delivery_detail, "delivery_id", lambda x: x),
             "Proforma": (PF.list_proformas, "skip", {}, PF.get_proforma_detail, "proforma_id", lambda x: x["data"])}
        for nama, (lf, pk, lov, df, idn, ambil) in M.items():
            rows = await semua(lf, r, pk, **lov)
            n_cmp = 0
            for row in rows:
                arg = UUID(row["id"]) if nama == "Penerimaan" else row["id"]
                det = ambil(J(await df(**kw(df, r, **{idn: arg}))))
                out[f"{t}|{nama}|{row['id']}"] = det
                if mode == "baru":
                    for k in PAR[nama]:
                        if k not in det:
                            cek(f"{t} {nama} {row.get('payment_number') or row.get('quote_number') or row.get('delivery_number') or row.get('proforma_number')} ADA {k}", False, "kunci hilang di detail")
                        elif k in row and det[k] != row[k]:
                            cek(f"{t} {nama} {row['id'][:8]} {k}", False, (row[k], det[k]))
                    n_cmp += 1
            if mode == "baru":
                print(f"[baru] {t} {nama}: {n_cmp} baris dibandingkan dengan detailnya")
    if mode in ("lama", "baru"):
        json.dump(out, open(sys.argv[2], "w"), default=str, sort_keys=True); print(mode, len(out), "detail")
    if mode == "baru":
        print("RINGKAS PARITAS gagal=%d %s" % (len(gagal), gagal[:5])); sys.exit(1 if gagal else 0)
    await c.close()


async def banding():
    a, b = json.load(open(sys.argv[2])), json.load(open(sys.argv[3]))
    beda = []
    for k, x in a.items():
        nama = k.split("|")[1]
        y = b.get(k)
        if y is None: beda.append((k, "hilang")); continue
        if x != {kk: v for kk, v in y.items() if kk not in BARU[nama]}: beda.append((k, "detail lama berubah"))
    print("BANDING: %d detail, BEDA=%d %s" % (len(a), len(beda), beda[:5])); sys.exit(1 if beda else 0)

if sys.argv[1] == "banding": asyncio.run(banding())
else: asyncio.run(main())
