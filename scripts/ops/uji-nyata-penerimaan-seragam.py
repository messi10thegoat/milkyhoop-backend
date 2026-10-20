"""UJI NYATA PENJUALAN-SERAGAM / Penerimaan (20 Okt 2026): BACA-SAJA (default_transaction_read_only=on), kaos+grapgrap.
Mode: lama <out.json> (pohon master) | baru <out.json> (pohon cabang; + cek konsistensi) | banding <lama.json> <baru.json>.
Syarat: field lama IDENTIK; field baru konsisten (documents baris == bundel tanpa rekap; belum_dialokasikan == total − Σ alokasi aktif;
penerimaan_saja == filter klien lama; sum_total/sum_sisa == Σ semua halaman; summary belum_dialokasikan_count == ?aksi= total;
summary?search.posted_count == daftar?search&posted&penerimaan_saja total)."""
import asyncio, inspect, json, os, sys
from decimal import Decimal
from uuid import UUID
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from fastapi.params import Query as QP
from starlette.requests import Request
from app.routers import receive_payments as RP

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


def d(x):
    if hasattr(x, "model_dump"): return x.model_dump(mode="json")
    if hasattr(x, "body"): return json.loads(x.body)
    return x


def cek(nama, ok, rinci=""):
    if not ok: gagal.append((nama, str(rinci)[:220]))


async def semua(r, **ov):
    items, skip = [], 0
    while True:
        res = d(await RP.list_receive_payments(**kw(RP.list_receive_payments, r, limit=100, skip=skip, **ov)))
        items += res["items"]
        if not res.get("has_more") or skip > 2000: return items, res
        skip += 100


async def main():
    mode = sys.argv[1]
    c = await asyncpg.connect(os.environ["URL"]); await c.execute("SET default_transaction_read_only = on")
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    RP.get_pool = gp
    out = {}
    for t in TEN:
        uid = str(await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", t)); r = req(t, uid)
        V = [("default", dict()), ("limit100", dict(limit=100)), ("posted", dict(status="posted")), ("voided", dict(status="voided")), ("draft", dict(status="draft")),
             ("cash", dict(source_type="cash", limit=100)), ("kas+void", dict(source_type="cash", include_voided=True, limit=100)),
             ("cari a", dict(search="a", limit=100)), ("tgl", dict(date_from="2026-09-01", limit=100)), ("skip100", dict(skip=100, limit=100)),
             ("sort total", dict(sort_by="total_amount", sort_order="asc", limit=100))]
        for nama, ov in V:
            if "date_from" in ov:
                from datetime import date as _d; ov = dict(ov, date_from=_d(2026, 9, 1))
            out[f"{t}|RL|{nama}"] = d(await RP.list_receive_payments(**kw(RP.list_receive_payments, r, **ov)))
        out[f"{t}|RS"] = d(await RP.get_receive_payments_summary(**kw(RP.get_receive_payments_summary, r)))
        if mode == "baru":
            from app.routers import dokumen as DK
            semua_baris, _ = await semua(r, include_voided=True)
            wrap = [i for i in semua_baris if i["settlement_type"] == "RECEIVE_PAYMENT"]
            ps, resp = await semua(r, include_voided=True, penerimaan_saja=True)
            cek(f"{t} penerimaan_saja ids == filter klien", [i["id"] for i in ps] == [i["id"] for i in wrap], (len(ps), len(wrap)))
            cek(f"{t} penerimaan_saja total", resp["total"] == len(wrap), (resp["total"], len(wrap)))
            n_dok, n_belum, tot_belum = 0, 0, Decimal(0)
            for it in wrap:
                row = await c.fetchrow("SELECT total_amount, status FROM receive_payments WHERE id=$1 AND tenant_id=$2", UUID(it["id"]), t)
                dial = await c.fetchval("SELECT COALESCE(SUM(amount_applied),0) FROM receive_payment_allocations WHERE payment_id=$1 AND status='active'", UUID(it["id"]))
                esp = max(Decimal(str(row["total_amount"])) - Decimal(str(dial)), Decimal(0)) if row["status"] == "posted" else Decimal(0)
                cek(f"{t} belum_dialokasikan {it['payment_number']}", abs(Decimal(str(it["belum_dialokasikan"])) - esp) < Decimal("0.005"), (it["belum_dialokasikan"], esp))
                cek(f"{t} aksi {it['payment_number']}", (it["aksi"] == "belum_dialokasikan") == (esp > Decimal("0.005")))
                if esp > Decimal("0.005"): n_belum += 1; tot_belum += esp
                bundel = await DK.susun_dokumen_penerimaan(c, {"tenant_id": t, "user_id": None}, UUID(it["id"]))
                tipe = [{"type": x["kind"]} for g in bundel["groups"] if g["key"] != "rekap" for x in g["docs"]]
                cek(f"{t} documents==bundel {it['payment_number']}", it["documents"] == tipe and it["doc_count"] == len(tipe), (it["documents"], tipe))
                inv_ids = [x["invoice_id"] for x in await c.fetch("SELECT DISTINCT invoice_id FROM receive_payment_allocations WHERE payment_id=$1 AND status='active'", UUID(it["id"]))]
                n_nk = await c.fetchval("SELECT count(*) FROM credit_notes WHERE tenant_id=$1 AND original_invoice_id = ANY($2::uuid[]) AND status NOT IN ('draft','void')", t, inv_ids)
                kinds = [x["kind"] for g in bundel["groups"] for x in g["docs"]]
                cek(f"{t} independen faktur/kwitansi/NK {it['payment_number']}", kinds.count("invoice") == len(inv_ids) and kinds.count("receipt") == (1 if row["status"] in ("posted", "voided") else 0) and kinds.count("credit_note") == n_nk, (kinds, len(inv_ids), n_nk))
                cek(f"{t} bundel lengkap {it['payment_number']}", all({"sent", "batal", "status"} <= set(x) for g in bundel["groups"] for x in g["docs"]))
                cek(f"{t} anchor balance {it['payment_number']}", Decimal(bundel["anchor"]["balance"]) == esp.quantize(Decimal("0.01")), bundel["anchor"]["balance"])
                n_dok += 1
            tot = d(await RP.list_receive_payments(**kw(RP.list_receive_payments, r, limit=100, include_voided=True, penerimaan_saja=True, with_totals=True)))
            exp_total = sum(Decimal(str(i["total_amount"])) for i in wrap if i["status"] != "voided")
            cek(f"{t} sum_total", abs(Decimal(str(tot["sum_total"])) - exp_total) < Decimal("0.01"), (tot["sum_total"], exp_total))
            cek(f"{t} sum_sisa", abs(Decimal(str(tot["sum_sisa"])) - tot_belum) < Decimal("0.01"), (tot["sum_sisa"], tot_belum))
            ak = d(await RP.list_receive_payments(**kw(RP.list_receive_payments, r, limit=100, penerimaan_saja=True, aksi="belum_dialokasikan")))
            sm = d(await RP.get_receive_payments_summary(**kw(RP.get_receive_payments_summary, r)))["data"]
            cek(f"{t} aksi total == summary count", ak["total"] == sm["belum_dialokasikan_count"] == n_belum, (ak["total"], sm["belum_dialokasikan_count"], n_belum))
            cek(f"{t} summary value", abs(Decimal(str(sm["belum_dialokasikan_value"])) - tot_belum) < Decimal("0.01"), (sm["belum_dialokasikan_value"], tot_belum))
            for cari in ("a", "1"):
                sc = d(await RP.get_receive_payments_summary(**kw(RP.get_receive_payments_summary, r, search=cari)))["data"]
                lc = d(await RP.list_receive_payments(**kw(RP.list_receive_payments, r, limit=1, status="posted", penerimaan_saja=True, search=cari)))
                cek(f"{t} summary?search={cari} posted_count == daftar", sc["posted_count"] == lc["total"], (sc["posted_count"], lc["total"]))
            print(f"[baru] {t}: {len(wrap)} penerimaan wrapper, {n_dok} bundel dicek, belum_dialokasikan={n_belum} (Rp {tot_belum})")
    if mode in ("lama", "baru"):
        json.dump(out, open(sys.argv[2], "w"), default=str, sort_keys=True); print(mode, len(out), "variant")
    if mode == "baru":
        print("RINGKAS KONSISTENSI gagal=%d %s" % (len(gagal), gagal[:4])); sys.exit(1 if gagal else 0)
    await c.close()


async def banding():
    a, b = json.load(open(sys.argv[2])), json.load(open(sys.argv[3]))
    BARU = {"belum_dialokasikan", "aksi", "alokasi_keadaan", "alokasi_teks", "documents", "doc_count"}
    beda = []
    for k in a:
        x, y = a[k], b.get(k)
        if "|RL|" in k:
            if {kk: v for kk, v in x.items() if kk != "items"} != {kk: v for kk, v in y.items() if kk not in ("items", "sum_total", "sum_sisa")}: beda.append((k, "kepala"))
            if len(x["items"]) != len(y["items"]): beda.append((k, "jumlah"))
            for i, (p, q) in enumerate(zip(x["items"], y["items"])):
                if p != {kk: v for kk, v in q.items() if kk not in BARU}: beda.append((k, f"item{i}"))
        else:
            xx, yy = dict(x), dict(y)
            for kk in ("belum_dialokasikan_count", "belum_dialokasikan_value"):
                yy.get("data", {}).pop(kk, None)
            if xx != yy: beda.append((k, "tak identik"))
    print("BANDING: %d variant, BEDA=%d %s" % (len(a), len(beda), beda[:5])); sys.exit(1 if beda else 0)

if sys.argv[1] == "banding": asyncio.run(banding())
else: asyncio.run(main())
