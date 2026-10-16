"""Harness NYATA LISTPAGE-DATA (16 Okt 2026), BACA saja (transaksi read-only, ROLLBACK), grapgrap + kaos.
MODE=lama|baru (argv[1]); OUT=berkas JSON (argv[2]). Pohon kode dipasang di /app/backend (lama = master, baru = cabang).
Keduanya membuang respons daftar SO/Faktur (semua halaman, beberapa filter) + /summary tanpa search ke OUT.
MODE=baru juga memeriksa pasangan:
  SO  doc_count == len(documents); documents == kind bundel /documents (susun_dokumen, tanpa rekap) per SO;
      kirim_hari <0/0/1 == ship_text telat/Hari ini/Besok; non-draf: aksi=='telat_kirim' == (kirim_hari < 0);
      sum_total/sum_sisa (with_totals) == Σ kolom Total/Sisa seluruh halaman, per filter;
      /summary?search=w: total_orders == daftar(search=w).total, <aksi>_count == daftar(search=w, aksi).total.
  SI  is_overdue == terbuka AND jatuh_tempo_hari < 0; documents[0] = invoice; sum_* == Σ kolom; summary?search sama.
Bandingkan lama vs baru (kunci lama identik) dengan argv[1]=banding lama.json baru.json."""
import asyncio, inspect, json, os, sys
sys.path.insert(0, "/app"); os.environ.setdefault("OPENAI_API_KEY", "x")
from decimal import Decimal
from types import SimpleNamespace

gagal = 0
def cek(ok, label, info=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + label + (f" | {info}" if info else "")); gagal += 0 if ok else 1

def js(o):
    return json.loads(json.dumps(o, sort_keys=True, default=str))

if sys.argv[1] == "banding":
    a, b = json.load(open(sys.argv[2])), json.load(open(sys.argv[3]))
    cek(sorted(a) == sorted(b), "kunci panggilan sama", f"{len(a)} panggilan")
    beda = 0
    for k in a:
        x, y = a[k], b.get(k)
        if y is None:
            beda += 1; continue
        if isinstance(x, dict) and "items" in x:
            yy = dict(y); yy["items"] = [{kk: v for kk, v in it.items() if kk in xi} for it, xi in zip(y["items"], x["items"])]
            for kk in ("sum_total", "sum_sisa"):
                if kk not in x:
                    yy.pop(kk, None)
            if yy != x or len(y["items"]) != len(x["items"]):
                beda += 1; print("BEDA", k)
        elif x != y:
            beda += 1; print("BEDA", k)
    cek(beda == 0, "lama == baru (kunci lama, semua panggilan)", f"beda {beda}")
    print("RINGKAS gagal=%d" % gagal); sys.exit(0)

import asyncpg
from backend.api_gateway.app.routers import sales_orders as SO, sales_invoices as SI

MODE, OUT = sys.argv[1], sys.argv[2]
BARU = MODE == "baru"

def kw(fn, **over):
    out = {}
    for n, p in inspect.signature(fn).parameters.items():
        if n == "request":
            continue
        d = p.default
        out[n] = getattr(d, "default", d)
    out.update({k: v for k, v in over.items() if k in out})
    return out

async def semua_halaman(fn, req, **over):
    hal, skip, tot, extra = [], 0, None, {}
    while True:
        r = await fn(req, **kw(fn, skip=skip, limit=100, **over))
        r = r.model_dump() if hasattr(r, "model_dump") else r
        r = js(r)
        if skip == 0:
            extra = {k: r[k] for k in ("sum_total", "sum_sisa") if k in r}
        hal += r["items"]; tot = r["total"]; skip += 100
        if skip >= tot:
            break
    return {"items": hal, "total": tot, **extra}

async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    tx = c.transaction(readonly=True); await tx.start()
    class Pool:
        def acquire(self_):
            class A:
                async def __aenter__(s): return c
                async def __aexit__(s, *a): return False
            return A()
    async def gp(): return Pool()
    SO.get_pool = gp; SI.get_pool = gp
    dump = {}
    try:
        for t in ("grapgrap-manado", "kaos-biru-konveksi"):
            uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId"=$1 ORDER BY "createdAt" LIMIT 1', t)
            req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": t, "user_id": str(uid)}))
            # kata cari: nama pelanggan terbanyak (tanpa tebakan)
            w_so = (await c.fetchval("SELECT split_part(customer_name,' ',1) FROM sales_orders WHERE tenant_id=$1 "
                                     "GROUP BY 1 ORDER BY count(*) DESC LIMIT 1", t)) or "a"
            w_si = (await c.fetchval("SELECT split_part(customer_name,' ',1) FROM sales_invoices WHERE tenant_id=$1 "
                                     "GROUP BY 1 ORDER BY count(*) DESC LIMIT 1", t)) or "a"
            tot = {"with_totals": True} if BARU else {}
            filt_so = [{}, {"search": w_so}, {"status": "confirmed"}] + [{"aksi": a} for a in SO.so_aksi.AKSI]
            filt_si = [{}, {"search": w_si}, {"status": "unpaid"}, {"status": "overdue"}] + [{"aksi": a} for a in SI._fa.AKSI]
            for f in filt_so:
                dump[f"{t}|so|{json.dumps(f, sort_keys=True)}"] = await semua_halaman(SO.list_sales_orders, req, **f, **tot)
            for f in filt_si:
                dump[f"{t}|si|{json.dumps(f, sort_keys=True)}"] = await semua_halaman(SI.list_invoices, req, **f, **tot)
            dump[f"{t}|so|summary"] = js((await SO.get_sales_order_summary(req, **kw(SO.get_sales_order_summary))).model_dump())
            dump[f"{t}|si|summary"] = js(await SI.get_invoice_summary(req, **kw(SI.get_invoice_summary)))
            if not BARU:
                continue
            # ---- SO pasangan
            so_all = dump[f"{t}|so|{{}}"]["items"]
            cek(all(r["doc_count"] == len(r["documents"]) for r in so_all), f"{t} SO doc_count == len(documents) ({len(so_all)})")
            from backend.api_gateway.app.routers.dokumen import susun_dokumen
            from uuid import UUID
            beda = 0
            for r in so_all:
                b = await susun_dokumen(c, {"tenant_id": t}, UUID(r["id"]))
                kinds = [d["kind"] for g in b["groups"] for d in g["docs"] if d["kind"] != "rekap"]
                urut = ("quotation", "proforma", "receipt", "delivery", "invoice")
                if sorted(kinds, key=urut.index) != [d["type"] for d in r["documents"]]:
                    beda += 1
                    if beda <= 3: print("  dok beda", r["order_number"], kinds, [d["type"] for d in r["documents"]])
            cek(beda == 0, f"{t} SO documents == bundel /documents per SO ({len(so_all)})", f"beda {beda}")
            bad = [r["order_number"] for r in so_all if r["kirim_hari"] is not None and (
                (r["kirim_hari"] < 0) != (r["ship_text"] or "").endswith(", telat")
                or (r["kirim_hari"] == 0) != (r["ship_text"] == "Hari ini")
                or (r["kirim_hari"] == 1) != (r["ship_text"] == "Besok"))]
            cek(not bad, f"{t} SO kirim_hari == ship_text", str(bad[:5]))
            bad = [r["order_number"] for r in so_all if r["status"] != "draft" and
                   (r["aksi"] == "telat_kirim") != (r["kirim_hari"] is not None and r["kirim_hari"] < 0)]
            cek(not bad, f"{t} SO aksi telat_kirim == kirim_hari<0 (non-draf)", str(bad[:5]))
            print(f"  info {t} SO kirim_terbuka={sum(r['kirim_terbuka'] for r in so_all)} telat={sum(1 for r in so_all if (r['kirim_hari'] or 0) < 0)}")
            for k, v in dump.items():
                if not k.startswith(f"{t}|") or "summary" in k:
                    continue
                kol = "outstanding_amount"
                st = sum((Decimal(str(i["total_amount"])) for i in v["items"]), Decimal(0))
                ss = sum((Decimal(str(i[kol])) for i in v["items"] if i[kol] is not None), Decimal(0))
                cek(abs(Decimal(str(v["sum_total"])) - st) < Decimal("0.01") and abs(Decimal(str(v["sum_sisa"])) - ss) < Decimal("0.01"),
                    f"{k} sum_total/sum_sisa == Σ kolom ({len(v['items'])} baris)", f"{v['sum_total']}/{v['sum_sisa']} vs {st}/{ss}")
            # summary?search
            for w in (w_so, "zzzz-tak-ada"):
                s = js((await SO.get_sales_order_summary(req, **kw(SO.get_sales_order_summary, search=w))).model_dump())["data"]
                d = await semua_halaman(SO.list_sales_orders, req, search=w)
                ok = s["total_orders"] == d["total"]
                for a in SO.so_aksi.AKSI:
                    n = (await semua_halaman(SO.list_sales_orders, req, search=w, aksi=a))["total"]
                    ok = ok and s[f"{a}_count"] == n
                for stt in ("draft", "confirmed", "cancelled"):
                    ok = ok and s[f"{stt}_count"] == (await semua_halaman(SO.list_sales_orders, req, search=w, status=stt))["total"]
                cek(ok, f"{t} SO summary?search={w} == daftar?search (total, aksi, status)", f"total {s['total_orders']}")
            s0 = js((await SO.get_sales_order_summary(req, **kw(SO.get_sales_order_summary, search=""))).model_dump())
            cek(s0 == dump[f"{t}|so|summary"], f"{t} SO summary?search='' == tanpa search")
            # ---- SI pasangan
            si_all = dump[f"{t}|si|{{}}"]["items"]
            bad = [r["invoice_number"] for r in si_all if r["is_overdue"] != (r["jatuh_tempo_terbuka"] and r["jatuh_tempo_hari"] is not None and r["jatuh_tempo_hari"] < 0)]
            cek(not bad, f"{t} SI is_overdue == terbuka AND jatuh_tempo_hari<0 ({len(si_all)})", str(bad[:5]))
            bad = [r["invoice_number"] for r in si_all if r["jatuh_tempo_terbuka"] != (r["status"] in ("posted", "partial") and Decimal(str(r["outstanding_amount"] or 0)) > 0)]
            cek(not bad, f"{t} SI terbuka == posted/partial dgn Sisa>0", str(bad[:5]))
            cek(all(r["documents"] and r["documents"][0]["type"] == "invoice" for r in si_all), f"{t} SI documents diawali faktur")
            tanpa_kw = [r["invoice_number"] for r in si_all if r["status"] not in ("draft", "void")
                        and Decimal(str(r["amount_paid"])) > 0 and not any(d["type"] == "receipt" for d in r["documents"])]
            print(f"  info {t} SI terbayar tanpa kwitansi (bisa NK/ uang muka lain): {len(tanpa_kw)} {tanpa_kw[:5]}")
            print(f"  info {t} SI documents jenis: " + json.dumps({k: sum(1 for r in si_all for d in r['documents'] if d['type'] == k) for k in ('invoice', 'receipt', 'credit_note')}))
            for w in (w_si, "zzzz-tak-ada"):
                s = await SI.get_invoice_summary(req, **kw(SI.get_invoice_summary, search=w))
                s = js(s)["data"]
                ok = s["total_count"] == (await semua_halaman(SI.list_invoices, req, search=w))["total"]
                for a in SI._fa.AKSI:
                    ok = ok and s[f"{a}_count"] == (await semua_halaman(SI.list_invoices, req, search=w, aksi=a))["total"]
                for stt in ("draft", "paid", "partial"):
                    ok = ok and s[f"{stt}_count"] == (await semua_halaman(SI.list_invoices, req, search=w, status=stt))["total"]
                kred = js(dump[f"{t}|si|summary"])["data"]
                ok = ok and s["kredit_nota_belum_terpakai_value"] == kred["kredit_nota_belum_terpakai_value"]
                ok = ok and abs(s["faktur_belum_lunas_value"] - s["kredit_nota_belum_terpakai_value"] - s["total_outstanding"]) < 0.01
                cek(ok, f"{t} SI summary?search={w} == daftar?search (total, aksi, status) + invarian kredit", f"total {s['total_count']}")
            s0 = js(await SI.get_invoice_summary(req, **kw(SI.get_invoice_summary, search="")))
            cek(s0 == dump[f"{t}|si|summary"], f"{t} SI summary?search='' == tanpa search")
    finally:
        await tx.rollback(); await c.close()
    json.dump(dump, open(OUT, "w"), sort_keys=True, default=str)
    print("RINGKAS gagal=%d" % gagal)
asyncio.run(main())
