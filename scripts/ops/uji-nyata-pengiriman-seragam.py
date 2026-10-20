"""UJI NYATA PENJUALAN-SERAGAM / Pengiriman (20 Okt 2026): BACA-SAJA (default_transaction_read_only=on), kaos+grapgrap.
Mode: lama <out.json> | baru <out.json> (+konsistensi) | banding <lama.json> <baru.json>. Syarat: field lama IDENTIK (daftar & detail);
baru: serah_keadaan/aksi == SQL independen; ?aksi= total == hitungan baris == summary; documents == bundel tanpa rekap + hitungan independen;
skip/offset/has_more benar; summary?search.total_all == daftar?search.total."""
import asyncio, inspect, json, os, sys
from uuid import UUID
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from fastapi.params import Query as QP
from starlette.requests import Request
from app.routers import deliveries as DL

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


def cek(nama, ok, rinci=""):
    if not ok: gagal.append((nama, str(rinci)[:220]))


async def main():
    mode = sys.argv[1]
    c = await asyncpg.connect(os.environ["URL"]); await c.execute("SET default_transaction_read_only = on")
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    DL.get_pool = gp
    out = {}
    for t in TEN:
        uid = str(await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", t)); r = req(t, uid)
        V = [("default", dict()), ("p100", dict(per_page=100)), ("posted", dict(status="posted", per_page=100)), ("voided", dict(status="voided", per_page=100)),
             ("cari a", dict(search="a", per_page=100)), ("tgl", dict(date_from=__import__("datetime").date(2026, 9, 1), per_page=100)),
             ("sort status", dict(sort_by="status", sort_order="asc", per_page=100)), ("p2x5", dict(page=2, per_page=5)), ("nomor", dict(sort_by="delivery_number", per_page=100))]
        for nama, ov in V:
            out[f"{t}|DL|{nama}"] = await DL.list_deliveries(**kw(DL.list_deliveries, r, **ov))
        out[f"{t}|DS"] = await DL.get_deliveries_summary(**kw(DL.get_deliveries_summary, r))
        semua = (await DL.list_deliveries(**kw(DL.list_deliveries, r, per_page=100)))["items"]
        for it in semua[:12]:
            out[f"{t}|DD|{it['id']}"] = await DL.get_delivery_detail(**kw(DL.get_delivery_detail, r, delivery_id=it["id"]))
        if mode == "baru":
            from app.routers import dokumen as DK
            aktif = bool(await c.fetchval("SELECT 1 FROM tenant_features WHERE tenant_id=$1 AND feature='pengiriman_serah' AND enabled", t))
            n_pr = n_bd = 0
            for it in semua:
                s = await c.fetchrow("SELECT jenis_serah, diterima_pada FROM pengiriman_serah WHERE tenant_id=$1 AND fulfillment_id=$2", t, UUID(it["id"]))
                nresi = await c.fetchval("SELECT count(*) FROM pengiriman_resi WHERE tenant_id=$1 AND fulfillment_id=$2", t, UUID(it["id"]))
                post = it["status"] == "posted"
                jenis = s["jenis_serah"] if s else None
                esp_bd = aktif and post and jenis is None
                esp_pr = aktif and post and jenis == "kurir" and nresi == 0
                esp_aksi = "perlu_resi" if esp_pr else ("belum_dicatat" if esp_bd else None)
                n_pr += esp_pr; n_bd += esp_bd
                cek(f"{t} aksi {it['delivery_number']}", it["aksi"] == esp_aksi, (it["aksi"], esp_aksi))
                kd = "tanpa" if not (aktif and post) else ("belum_dicatat" if jenis is None else ("diterima" if s["diterima_pada"] else "belum_diterima"))
                cek(f"{t} keadaan {it['delivery_number']}", it["serah_keadaan"] == kd, (it["serah_keadaan"], kd))
                bundel = await DK.susun_dokumen_pengiriman(c, {"tenant_id": t, "user_id": None}, UUID(it["id"]))
                tipe = [{"type": x["kind"]} for g in bundel["groups"] for x in g["docs"] if g["key"] != "rekap"]
                cek(f"{t} documents==bundel {it['delivery_number']}", it["documents"] == tipe and it["doc_count"] == len(tipe), (it["documents"], tipe))
                kinds = [x["kind"] for g in bundel["groups"] for x in g["docs"]]
                cek(f"{t} independen {it['delivery_number']}", kinds.count("delivery") == 1 and kinds.count("invoice") == (1 if it["invoice_id"] else 0), kinds)
                cek(f"{t} bundel lengkap {it['delivery_number']}", all({"sent", "batal", "status"} <= set(x) for g in bundel["groups"] for x in g["docs"]))
                dl = next(x for g in bundel["groups"] for x in g["docs"] if x["kind"] == "delivery")
                cek(f"{t} batal==voided {it['delivery_number']}", dl["batal"] == (it["status"] == "voided"), (dl["batal"], it["status"]))
                n_bd += 1
            for ak, n in (("perlu_resi", n_pr), ("belum_dicatat", n_bd and 0)):
                pass
            for ak, esp in (("perlu_resi", n_pr), ("belum_dicatat", sum(1 for x in semua if x["aksi"] == "belum_dicatat"))):
                lst = await DL.list_deliveries(**kw(DL.list_deliveries, r, per_page=100, aksi=ak))
                cek(f"{t} ?aksi={ak} total", lst["total"] == esp == len(lst["items"]) and all(x["aksi"] == ak for x in lst["items"]), (lst["total"], esp))
                sm = await DL.get_deliveries_summary(**kw(DL.get_deliveries_summary, r))
                cek(f"{t} summary {ak}_count", sm[f"{ak}_count"] == esp, (sm[f"{ak}_count"], esp))
            lima = await DL.list_deliveries(**kw(DL.list_deliveries, r, per_page=5))
            nxt = await DL.list_deliveries(**kw(DL.list_deliveries, r, per_page=5, skip=5))
            p2 = await DL.list_deliveries(**kw(DL.list_deliveries, r, per_page=5, page=2))
            off = await DL.list_deliveries(**kw(DL.list_deliveries, r, per_page=5, offset=5, page=1))
            cek(f"{t} skip==page2==offset", [i["id"] for i in nxt["items"]] == [i["id"] for i in p2["items"]] == [i["id"] for i in off["items"]])
            cek(f"{t} has_more", lima["has_more"] == (lima["total"] > 5) and (await DL.list_deliveries(**kw(DL.list_deliveries, r, per_page=100)))["has_more"] is False)
            for cari in ("a", "SJ"):
                lc = await DL.list_deliveries(**kw(DL.list_deliveries, r, per_page=1, search=cari))
                sc = await DL.get_deliveries_summary(**kw(DL.get_deliveries_summary, r, search=cari))
                cek(f"{t} summary?search={cari}", sc["total_all"] == lc["total"], (sc["total_all"], lc["total"]))
            print(f"[baru] {t}: {len(semua)} surat jalan dicek, perlu_resi={n_pr}, belum_dicatat={sum(1 for x in semua if x['aksi']=='belum_dicatat')} (serah aktif={aktif})")
    if mode in ("lama", "baru"):
        json.dump(out, open(sys.argv[2], "w"), default=str, sort_keys=True); print(mode, len(out), "variant")
    if mode == "baru":
        print("RINGKAS KONSISTENSI gagal=%d %s" % (len(gagal), gagal[:4])); sys.exit(1 if gagal else 0)
    await c.close()


async def banding():
    a, b = json.load(open(sys.argv[2])), json.load(open(sys.argv[3]))
    BARU = {"serah_keadaan", "serah_teks", "aksi", "documents", "doc_count"}
    beda, catatan = [], []
    for k in a:
        x, y = a[k], b.get(k)
        if "|DL|" in k:
            if {kk: v for kk, v in x.items() if kk != "items"} != {kk: v for kk, v in y.items() if kk not in ("items", "has_more")}: beda.append((k, "kepala"))
            if len(x["items"]) != len(y["items"]): beda.append((k, "jumlah"))
            # urutan seri (tanggal sama) tak deterministik ANTAR proses di kode lama juga -> bandingkan per id; urutan beda hanya dicatat
            py = {q["id"]: {kk: v for kk, v in q.items() if kk not in BARU} for q in y["items"]}
            for i, p in enumerate(x["items"]):
                if py.get(p["id"]) != p: beda.append((k, f"item{i}"))
            if [p["id"] for p in x["items"]] != [q["id"] for q in y["items"]]: catatan.append(k)
        elif "|DD|" in k:
            if x != {kk: v for kk, v in y.items() if kk not in BARU}: beda.append((k, "detail"))
        else:
            if x != {kk: v for kk, v in y.items() if not kk.endswith("_count") or kk in ("today_count",)}: beda.append((k, "summary"))
    print("BANDING: %d variant, BEDA=%d %s | urutan-seri-beda(informasi)=%s" % (len(a), len(beda), beda[:5], catatan)); sys.exit(1 if beda else 0)

if sys.argv[1] == "banding": asyncio.run(banding())
else: asyncio.run(main())
