"""Harness NYATA detailpage data (17 Okt 2026), BACA saja (read-only ROLLBACK), grapgrap + kaos, SEMUA SO.
argv: dump <out.json> (kode pohon terpasang) | banding lama.json baru.json.
banding: bundel /documents kunci LAMA identik + aturan medan baru (status dari batal/paid; kwitansi subtype == jenis efektif
DB dibaca terpisah; account_label = awalan account_name detail SO deposits[] ('BCA 123 a.n. ..') untuk uang muka) +
detail SO position_text/ship_text/kirim_* == baris daftar SO."""
import asyncio, inspect, json, os, sys
sys.path.insert(0, "/app"); os.environ.setdefault("OPENAI_API_KEY", "x")
from types import SimpleNamespace

def js(o): return json.loads(json.dumps(o, sort_keys=True, default=str))

if sys.argv[1] == "banding":
    a, b = json.load(open(sys.argv[2])), json.load(open(sys.argv[3]))
    gagal = 0
    def cek(ok, l, i=""):
        global gagal
        print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else "")); gagal += 0 if ok else 1
    BARU = {"status", "receipt_kind", "account_label"}
    beda = []
    for k, v in a.items():
        if not k.endswith("|dok"): continue
        w = b[k]
        sv = json.dumps(v, sort_keys=True)
        wl = json.loads(json.dumps(w))
        for g in wl["groups"]:
            for d in g["docs"]:
                for x in BARU:   # kunci LAMA (termasuk subtype proforma) harus identik; kwitansi tak boleh dapat subtype
                    d.pop(x, None)
        if json.dumps(wl, sort_keys=True) != sv: beda.append(k)
    cek(not beda, f"bundel /documents kunci lama identik ({sum(1 for k in a if k.endswith('|dok'))} SO)", str(beda[:3]))
    salah_st, salah_sub, salah_ak, n_kw, n_ak = [], [], [], 0, 0
    for k, w in b.items():
        if not k.endswith("|dok"): continue
        jenis = b.get(k.replace("|dok", "|jenis"), {})
        dep_label = b.get(k.replace("|dok", "|dep_label"), {})
        for g in w["groups"]:
            for d in g["docs"]:
                harap = ("batal" if d["batal"] else
                         ("lunas" if d.get("paid") else ("terbit" if d["kind"] == "proforma" else "diposting"))
                         if d["kind"] in ("proforma", "invoice") else
                         {"receipt": "diterima", "credit_note": "diposting"}.get(d["kind"], "terbit"))
                if d.get("status") != harap: salah_st.append((k, d["kind"], d.get("status"), harap))
                if d["kind"] == "receipt":
                    n_kw += 1
                    j = jenis.get(d["id"])  # None = penerimaan faktur (bukan uang muka)
                    h = {"DP": "uang_muka", "PELUNASAN": "pelunasan"}.get(j, "pelunasan") if j else "pelunasan"
                    if d.get("receipt_kind") != h or "subtype" in d: salah_sub.append((k, d["number"], d.get("receipt_kind"), j))
                    lab = dep_label.get(d["id"])
                    if lab and d.get("account_label"):
                        n_ak += 1
                        if not lab.startswith(d["account_label"]): salah_ak.append((k, d["number"], d["account_label"], lab))
    cek(not salah_st, "status == turunan batal/paid untuk semua dokumen", str(salah_st[:3]))
    cek(not salah_sub, f"kwitansi receipt_kind == jenis efektif DB (tanpa subtype) ({n_kw} kwitansi)", str(salah_sub[:3]))
    cek(not salah_ak and n_ak > 0, f"account_label = awalan account_name detail SO ({n_ak} uang muka berrekening)", str(salah_ak[:3]))
    sl = []
    for k, w in b.items():
        if not k.endswith("|detail"): continue
        row = b[k.replace("|detail", "|baris")]
        if any(w.get(f) != row.get(f) for f in ("position_text", "ship_text", "kirim_hari", "kirim_terbuka", "kirim_keadaan")):
            sl.append(k)
    cek(not sl, f"detail SO Posisi/Kirim == baris daftar ({sum(1 for k in b if k.endswith('|detail'))} SO)", str(sl[:3]))
    print("RINGKAS gagal=%d" % gagal); sys.exit(0)

import asyncpg
from uuid import UUID
from backend.api_gateway.app.routers import sales_orders as SO
from backend.api_gateway.app.routers.dokumen import susun_dokumen

def kw(fn, **o):
    d = {n: getattr(p.default, "default", p.default) for n, p in inspect.signature(fn).parameters.items()
         if n != "request" and p.default is not inspect.Parameter.empty}
    d.update(o); return d

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
    SO.get_pool = gp
    out = {}
    try:
        for t in ("grapgrap-manado", "kaos-biru-konveksi"):
            uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId"=$1 ORDER BY "createdAt" LIMIT 1', t)
            req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": t, "user_id": str(uid)}), headers={})
            baris, skip = {}, 0
            while True:
                r = (await SO.list_sales_orders(req, **kw(SO.list_sales_orders, skip=skip, limit=100))).model_dump()
                for x in r["items"]: baris[x["id"]] = x
                skip += 100
                if skip >= r["total"]: break
            jenis_all = {str(r["id"]): r["j"] for r in await c.fetch(
                "SELECT cd.id, COALESCE(cd.jenis, (SELECT CASE pf.purpose WHEN 'DP' THEN 'DP' WHEN 'TERMIN' THEN 'DP' WHEN 'PELUNASAN' THEN 'PELUNASAN' END FROM proformas pf WHERE pf.id=cd.proforma_id AND pf.tenant_id=cd.tenant_id), 'DP') j FROM customer_deposits cd WHERE cd.tenant_id=$1", t)}
            for sid, x in baris.items():
                b = js(await susun_dokumen(c, {"tenant_id": t}, UUID(sid)))
                out[f"{t}|{sid}|dok"] = b
                out[f"{t}|{sid}|jenis"] = {d["id"]: jenis_all.get(d["id"]) for g in b["groups"] for d in g["docs"] if d["kind"] == "receipt"}
                det = js((await SO.get_sales_order_detail(req, sid)).model_dump())["data"]
                out[f"{t}|{sid}|dep_label"] = {d["id"]: d.get("account_name") for d in det.get("deposits", [])}
                out[f"{t}|{sid}|detail"] = {f: det.get(f) for f in ("position_text", "ship_text", "kirim_hari", "kirim_terbuka", "kirim_keadaan")}
                out[f"{t}|{sid}|baris"] = {f: x.get(f) for f in ("position_text", "ship_text", "kirim_hari", "kirim_terbuka", "kirim_keadaan")}
    finally:
        await tx.rollback(); await c.close()
    json.dump(out, open(sys.argv[2], "w"), sort_keys=True)
    print("dump", len(out))
asyncio.run(main())
