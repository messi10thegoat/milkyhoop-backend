"""UJI NYATA Pembayaran Keluar berbasis WRAPPER + turunan alokasi (PEMBELIAN-SERAGAM unit (2), 22 Okt 2026). BACA-SAJA (READ ONLY + ROLLBACK),
grapgrap + kaos. argv: lama <out.json> | baru <out.json> | banding <lama.json> <baru.json>
  lama/baru : daftar (status=all, semua halaman) + detail per baris + summary -> json
  baru      : + penegasan
     (a) himpunan baris daftar == SEMUA wrapper bill_payments_v2 tenant (tanpa baris jurnal hantu; voided/draf ikut)
     (b) baris daftar == detail (total, allocated, unapplied, status, dialokasikan, belum, keadaan, aksi)
     (c) posted: dialokasikan == Σ bill_payment_allocations; belum == max(total+diskon−Σ, 0); allocated+unapplied == total+diskon
         non-posted: keadaan 'tanpa', dialokasikan/belum 0
     (d) summary: total_count/total_paid/posted/draft == baris daftar non-voided; by_method Σ == total
     (e) urutan deterministik (dua panggilan identik; tiebreak q.id di ORDER BY, dicek di tes unit)
     (f) UKUR: pembayaran ber-alokasi ke tagihan void (status_v2 void) -> dicetak (tiket BILL-2609-0002)
  banding   : setiap beda lama -> baru dicetak; ringkas jumlah.
MERAH=1 : harapan satu baris dibalik -> wajib gagal."""
import asyncio, inspect, json, os, sys
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from fastapi.params import Query as QP
from starlette.requests import Request

TEN = ["grapgrap-manado", "kaos-biru-konveksi"]
MERAH = os.environ.get("MERAH") == "1"
gagal = []


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


def D(v): return Decimal(str(v if v is not None else 0))


def req(t, uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": t, "user_id": uid}; return r


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


async def main():
    mode = sys.argv[1]
    if mode == "banding":
        return banding()
    from app.routers import bill_payments as BP
    from fastapi import HTTPException
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    tx = c.transaction(readonly=True); await tx.start()
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    BP.get_pool = gp
    out = {}
    try:
        for t in TEN:
            uid = str(await c.fetchval('SELECT id FROM "User" WHERE "tenantId"=$1 ORDER BY "createdAt" LIMIT 1', t)); r = req(t, uid)
            rows, skip = [], 0
            while True:
                res = J(await BP.list_bill_payments(**kw(BP.list_bill_payments, r, skip=skip, limit=100)))
                rows += res["items"]; skip += 100
                if not res["has_more"]: break
            det = {}
            for x in rows:
                try:
                    d = J(await BP.get_bill_payment(**kw(BP.get_bill_payment, r, payment_id=x["id"])))["data"]
                except HTTPException as e:
                    d = {"_galat": e.status_code}
                det[x["id"]] = d
                out[f"{t}|{x['id']}"] = {"baris": {k: x.get(k) for k in ("payment_number", "status", "total_amount", "allocated_amount", "unapplied_amount",
                                                                      "dialokasikan", "belum_dialokasikan", "alokasi_keadaan", "aksi")},
                                          "detail": {k: d.get(k) for k in ("_galat", "status", "total_amount", "allocated_amount", "unapplied_amount",
                                                                         "dialokasikan", "belum_dialokasikan", "alokasi_keadaan", "aksi")}}
            sm = J(await BP.get_bill_payments_summary(**kw(BP.get_bill_payments_summary, r)))["data"]
            out[f"{t}|summary"] = sm
            if mode != "baru":
                continue
            wr = {str(w["id"]): w for w in await c.fetch(
                """SELECT bp.id, bp.status, bp.total_amount, COALESCE(bp.discount_amount, 0) AS diskon, bp.payment_method,
                          COALESCE((SELECT SUM(a.amount_applied) FROM bill_payment_allocations a WHERE a.payment_id = bp.id), 0) AS sigma
                   FROM bill_payments_v2 bp WHERE bp.tenant_id = $1""", t)}
            ids = [x["id"] for x in rows]
            cek(set(ids) == set(wr) and len(ids) == len(wr), f"{t}: daftar == semua wrapper ({len(wr)}; tanpa baris jurnal hantu)",
                str((sorted(set(ids) - set(wr))[:3], sorted(set(wr) - set(ids))[:3])))
            eb, ec = [], []
            for i, x in enumerate(rows):
                d = det[x["id"]]; w = wr.get(x["id"])
                kunci = ("status", "total_amount", "allocated_amount", "unapplied_amount", "dialokasikan", "belum_dialokasikan", "alokasi_keadaan", "aksi")
                if "_galat" in d or any((D(x[k]) != D(d[k])) if isinstance(x[k], (int, float)) else (x[k] != d[k]) for k in kunci):
                    eb.append((x["payment_number"], {k: (x.get(k), d.get(k)) for k in kunci if x.get(k) != d.get(k)}))
                if not w: continue
                bruto = D(w["total_amount"]) + D(w["diskon"])
                if w["status"] == "posted":
                    h_dia, h_bel = D(w["sigma"]), max(bruto - D(w["sigma"]), Decimal(0))
                    ok = (D(x["dialokasikan"]) == h_dia and D(x["belum_dialokasikan"]) == h_bel
                          and D(x["allocated_amount"]) == h_dia and D(x["unapplied_amount"]) == h_bel and h_dia + h_bel == bruto)
                else:
                    ok = x["alokasi_keadaan"] == "tanpa" and D(x["dialokasikan"]) == 0 and D(x["belum_dialokasikan"]) == 0
                if MERAH and i == 0: ok = not ok
                if not ok:
                    ec.append((x["payment_number"], w["status"], str(w["sigma"]), str(bruto), x["dialokasikan"], x["belum_dialokasikan"], x["allocated_amount"]))
            cek(not eb, f"{t}: baris daftar == detail ({len(rows)})", str(eb[:2]))
            cek(not ec, f"{t}: dialokasikan/belum == Σ alokasi (posted) / 0 (lainnya)", str(ec[:3]))
            sebaran = {}
            for x in rows: sebaran[f"{x['status']}/{x['alokasi_keadaan']}"] = sebaran.get(f"{x['status']}/{x['alokasi_keadaan']}", 0) + 1
            print(f"  {t} sebaran: {json.dumps(sebaran, ensure_ascii=False)}")
            nv = [x for x in rows if x["status"] != "voided"]
            ok_d = (sm["total_count"] == len(nv) and D(sm["total_paid"]) == sum(D(x["total_amount"]) for x in nv)
                    and sm["by_status"]["posted"]["count"] == sum(1 for x in nv if x["status"] == "posted")
                    and sm["by_status"]["draft"]["count"] == sum(1 for x in nv if x["status"] == "draft")
                    and sum(D(v["amount"]) for v in sm["by_method"].values()) == D(sm["total_paid"]))
            cek(ok_d, f"{t}: summary == baris daftar non-voided", json.dumps(sm, default=str)[:300])
            r2 = J(await BP.list_bill_payments(**kw(BP.list_bill_payments, r, skip=0, limit=100, sort_by="total_amount")))["items"]
            r3 = J(await BP.list_bill_payments(**kw(BP.list_bill_payments, r, skip=0, limit=100, sort_by="total_amount")))["items"]
            urut_ok = [y["id"] for y in r2] == [y["id"] for y in r3]
            cek(urut_ok, f"{t}: urutan deterministik (dua panggilan sort total_amount identik)")
            void_bayar = await c.fetch(
                """SELECT bp.payment_number, bp.status AS bp_status, b.invoice_number, b.status, b.status_v2, a.amount_applied
                   FROM bill_payment_allocations a JOIN bill_payments_v2 bp ON bp.id = a.payment_id JOIN bills b ON b.id = a.bill_id
                   WHERE bp.tenant_id = $1 AND (b.status_v2 = 'void' OR b.status = 'void')""", t)
            print(f"  {t} UKUR pembayaran ber-alokasi ke tagihan void: {[dict(v) for v in void_bayar]}")
    finally:
        await tx.rollback(); await c.close()
    json.dump(out, open(sys.argv[2], "w"), sort_keys=True, default=str)
    print(mode, len(out), "entri")
    if mode == "baru":
        print("RINGKAS gagal=%d %s" % (len(gagal), gagal[:6])); sys.exit(1 if gagal else 0)


def banding():
    a, b = json.load(open(sys.argv[2])), json.load(open(sys.argv[3]))
    hilang = [k for k in a if k not in b]; baru = [k for k in b if k not in a]
    beda = [(k, a[k], b[k]) for k in sorted(a) if k in b and a[k] != b[k]]
    per = {}
    for k in a:
        t = k.split("|")[0]; per.setdefault(t, [0, 0, 0])
    for k in hilang: per[k.split("|")[0]][0] += 1
    for k in baru: per.setdefault(k.split("|")[0], [0, 0, 0])[1] += 1
    for k, _, _ in beda: per[k.split("|")[0]][2] += 1
    print("BANDING per tenant [hilang dari daftar, baru di daftar, berubah]:", json.dumps(per))
    for k in hilang: print("  HILANG", k, json.dumps(a[k]["baris"] if "baris" in a[k] else a[k], default=str))
    for k in baru: print("  BARU  ", k, json.dumps(b[k]["baris"] if "baris" in b[k] else b[k], default=str))
    for k, x, y in beda:
        if k.endswith("|summary"):
            print("  SUMMARY", k, "\n     lama", json.dumps(x, sort_keys=True), "\n     baru", json.dumps(y, sort_keys=True))
            continue
        ub = {f: (x["baris"].get(f), y["baris"].get(f)) for f in y["baris"] if x["baris"].get(f) != y["baris"].get(f)}
        ud = {f: (x["detail"].get(f), y["detail"].get(f)) for f in y["detail"] if x["detail"].get(f) != y["detail"].get(f)}
        print("  UBAH  ", k, y["baris"].get("payment_number"), "baris", json.dumps(ub, default=str), "detail", json.dumps(ud, default=str))


asyncio.run(main())
