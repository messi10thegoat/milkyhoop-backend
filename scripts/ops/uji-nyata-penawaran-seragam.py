"""UJI NYATA PENJUALAN-SERAGAM / Penawaran (17 Okt 2026): BACA-SAJA (default_transaction_read_only=on), kaos+grapgrap.
Mode: lama <out.json> (pohon master) | baru <out.json> (pohon cabang; juga cek konsistensi) | banding <lama.json> <baru.json>.
Syarat: field lama SAMA persis; Pesanan (refactor jumlah_sj/kwitansi) IDENTIK; field baru konsisten (telat==is_expired, documents ==
kolom Dok. SO hasil konversi, sum_total == Σ semua halaman, summary?search.total_quotes == daftar?search.total)."""
import asyncio, inspect, json, os, sys
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from fastapi.params import Query as QP
from starlette.requests import Request
from app.routers import quotes as QU, sales_orders as SO

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


def d(x): return x if isinstance(x, dict) else x.model_dump(mode="json")


def cek(nama, ok, rinci=""):
    if not ok: gagal.append((nama, str(rinci)[:200]))


async def semua_halaman(fn, r, **ov):
    items, page, tot = [], 1, None
    while True:
        res = d(await fn(**kw(fn, r, limit=100, page=page, skip=(page - 1) * 100 if "skip" in inspect.signature(fn).parameters and "page" not in inspect.signature(fn).parameters else 0, **ov)))
        items += res["items"]; tot = res
        if not res.get("has_more") or page > 20: break
        page += 1
    return items, tot


async def main():
    mode = sys.argv[1]
    c = await asyncpg.connect(os.environ["URL"]); await c.execute("SET default_transaction_read_only = on")
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    QU.get_pool = gp; SO.get_pool = gp
    out = {}
    for t in TEN:
        uid = str(await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", t))
        r = req(t, uid)
        V = [("all", dict())] + [(f"status={s}", dict(status=s)) for s in ("draft", "sent", "viewed", "accepted", "declined", "expired", "converted", "void", "invoiced")] \
            + [(f"aksi={a}", dict(aksi=a)) for a in ("perlu_dikirim", "tunggu_jawaban", "segera_habis", "lewat_masa", "perlu_dipesan")] \
            + [("cari a", dict(search="a")), ("cari 2 kata", dict(search="kaos biru")), ("limit100 p2", dict(limit=100, page=2))]
        for nama, ov in V:
            out[f"{t}|QL|{nama}"] = d(await QU.list_quotes(**kw(QU.list_quotes, r, **ov)))
        out[f"{t}|QS"] = d(await QU.get_quote_summary(**kw(QU.get_quote_summary, r)))
        for nama, ov in [("so all", dict(limit=100)), ("so +totals", dict(limit=100, with_totals=True)), ("so p2", dict(limit=100, skip=100)), ("so cari", dict(limit=100, search="a"))]:
            out[f"{t}|SO|{nama}"] = d(await SO.list_sales_orders(**kw(SO.list_sales_orders, r, **ov)))
        out[f"{t}|SOS"] = d(await SO.get_sales_order_summary(**kw(SO.get_sales_order_summary, r)))
        if mode == "baru":
            qs, resp = await semua_halaman(QU.list_quotes, r, with_totals=True)
            so_items, _ = await semua_halaman(SO.list_sales_orders, r)
            dok_so = {i["id"]: i["documents"] for i in so_items}
            n_conv = 0
            for q in qs:
                cek(f"{t} telat==is_expired {q['quote_number']}", (q["kedaluwarsa_keadaan"] == "telat") == q["is_expired"], q["kedaluwarsa_keadaan"])
                cek(f"{t} doc_count {q['quote_number']}", q["doc_count"] == len(q["documents"]) and q["doc_count"] >= 1)
                if q["sales_order_id"]:
                    n_conv += 1
                    cek(f"{t} documents==SO {q['quote_number']}", q["documents"] == dok_so.get(q["sales_order_id"]), (q["documents"], dok_so.get(q["sales_order_id"])))
                else:
                    cek(f"{t} tanpa SO = quotation {q['quote_number']}", q["documents"] == [{"type": "quotation"}])
            # 21 Okt: GET /quotes/{id}/documents -- jangkar tampil sebagai dirinya; documents[] baris == bundel tanpa rekap & tanpa nota kredit; terkonversi: groups == bundel SO
            from uuid import UUID as _U
            from app.routers import dokumen as DK
            for q in qs:
                b = await DK.susun_dokumen_penawaran(c, {'tenant_id': t, 'user_id': None}, _U(q['id']))
                tipe = [{'type': x['kind']} for g in b['groups'] if g['key'] != 'rekap' for x in g['docs'] if x['kind'] != 'credit_note']
                cek(f"{t} bundel==documents {q['quote_number']}", q['documents'] == tipe, (q['documents'], tipe))
                pn = [x for g in b['groups'] if g['key'] == 'pnw' for x in g['docs']]
                esp = 'draf' if q['status'] == 'draft' else 'batal' if q['status'] == 'void' else 'terbit'
                cek(f"{t} jangkar {q['quote_number']}", len(pn) == 1 and pn[0]['id'] == q['id'] and pn[0]['status'] == esp and pn[0]['batal'] == (q['status'] == 'void'), pn)
                cek(f"{t} anchor {q['quote_number']}", b['anchor']['kind'] == 'quotation' and b['anchor']['id'] == q['id'] and b['anchor']['paid'] is None and b['default'] == {'group': 'pnw', 'index': 0}, b['anchor'])
                cek(f"{t} lengkap {q['quote_number']}", all({'sent', 'batal', 'status'} <= set(x) for g in b['groups'] for x in g['docs']))
                if q['sales_order_id']:
                    so_b = await DK.susun_dokumen(c, {'tenant_id': t, 'user_id': None}, _U(q['sales_order_id']), sertakan_nk=True)
                    kunci = lambda gs: [(g['key'], [(x['kind'], x['id']) for x in g['docs']]) for g in gs]  # noqa: E731
                    cek(f"{t} groups==bundel SO {q['quote_number']}", kunci(b['groups']) == kunci(so_b['groups']), (kunci(b['groups']), kunci(so_b['groups'])))
                else:
                    cek(f"{t} tanpa SO = pnw saja {q['quote_number']}", [g['key'] for g in b['groups']] == ['pnw'], [g['key'] for g in b['groups']])
            first = d(await QU.list_quotes(**kw(QU.list_quotes, r, limit=100, with_totals=True)))
            cek(f"{t} sum_total", abs(first["sum_total"] - sum(float(q["total_amount"]) for q in qs)) < 0.005, (first["sum_total"], sum(float(q['total_amount']) for q in qs)))
            cek(f"{t} total==semua halaman", first["total"] == len(qs))
            for cari in (None, "a", "kaos biru"):
                lst = d(await QU.list_quotes(**kw(QU.list_quotes, r, limit=1, **({"search": cari} if cari else {}))))
                sm = d(await QU.get_quote_summary(**kw(QU.get_quote_summary, r, **({"search": cari} if cari else {}))))
                dd = sm.get("data", sm)
                cek(f"{t} summary?search={cari} total_quotes==daftar.total", dd["total_quotes"] == lst["total"], (dd["total_quotes"], lst["total"]))
            print(f"[baru] {t}: {len(qs)} penawaran, {n_conv} terkonversi ke SO, sum_total={first['sum_total']}")
    if mode in ("lama", "baru"):
        json.dump(out, open(sys.argv[2], "w"), default=str, sort_keys=True)
        print(mode, len(out), "variant")
    if mode == "baru":
        print("RINGKAS KONSISTENSI gagal=%d %s" % (len(gagal), gagal[:4]))
        sys.exit(1 if gagal else 0)
    await c.close()


async def banding():
    a, b = json.load(open(sys.argv[2])), json.load(open(sys.argv[3]))
    BARU = {"kedaluwarsa_terbuka", "kedaluwarsa_hari", "kedaluwarsa_keadaan", "kedaluwarsa_teks", "documents", "doc_count", "sum_total"}
    beda = []
    for k in a:
        x, y = a[k], b.get(k)
        if "|QL|" in k:
            x2 = {kk: v for kk, v in x.items() if kk != "items"}; y2 = {kk: v for kk, v in y.items() if kk not in ("items", "sum_total")}
            if x2 != y2: beda.append((k, "kepala"))
            for i, (p, q) in enumerate(zip(x["items"], y["items"])):
                if p != {kk: v for kk, v in q.items() if kk not in BARU}: beda.append((k, f"item{i}"))
            if len(x["items"]) != len(y["items"]): beda.append((k, "jumlah"))
        else:
            if x != y: beda.append((k, "tak identik"))
    print("BANDING: %d variant, BEDA=%d %s" % (len(a), len(beda), beda[:5]))
    sys.exit(1 if beda else 0)

if sys.argv[1] == "banding":
    asyncio.run(banding())
else:
    asyncio.run(main())
