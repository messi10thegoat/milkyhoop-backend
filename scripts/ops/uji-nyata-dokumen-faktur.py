"""Harness NYATA S1-U2 bundel dokumen (20 Okt 2026), BACA saja (transaksi read-only, ROLLBACK), grapgrap + kaos.
argv: so <out.json>              bundel SO semua pesanan (tanpa & dengan ?sertakan=nota_kredit) dari kode pohon terpasang
      banding lama.json baru.json   bundel SO lama == baru (ekstraksi pembentuk per-kind tak mengubah apa pun)
      faktur                        bundel FAKTUR semua faktur: ⊆ bundel SO (medan identik; amount kwitansi = bagian faktur ini),
                                    jumlah dok faktur/kwitansi/NK == documents[] daftar, nominal kwitansi == alokasi/penerapan
                                    di detail, kepala anchor == detail (posisi/dibayar/sisa), default = faktur ini; faktur lepas ikut."""
import asyncio, inspect, json, os, sys
from collections import Counter
from decimal import Decimal
sys.path.insert(0, "/app"); os.environ.setdefault("OPENAI_API_KEY", "x")
from types import SimpleNamespace

gagal = 0


def cek(ok, l, i=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else "")); gagal += 0 if ok else 1


def js(o): return json.loads(json.dumps(o, sort_keys=True, default=str))


if sys.argv[1] == "banding":
    a, b = json.load(open(sys.argv[2])), json.load(open(sys.argv[3]))
    cek(sorted(a) == sorted(b), f"kunci sama ({len(a)} bundel)")
    beda = [k for k in a if json.dumps(a[k], sort_keys=True) != json.dumps(b.get(k), sort_keys=True)]
    cek(not beda, f"bundel SO lama == baru ({len(a)} bundel = SO × 2 mode)", str(beda[:3]))
    print("RINGKAS gagal=%d" % gagal); sys.exit(0)

import asyncpg
from uuid import UUID
from backend.api_gateway.app.routers import dokumen as DK
from backend.api_gateway.app.routers import sales_invoices as SI

TENANT = ("grapgrap-manado", "kaos-biru-konveksi")


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
    SI.get_pool = gp
    out = {}
    try:
        sah_dp = {str(r["id"]) for r in await c.fetch(
            "SELECT id FROM customer_deposits WHERE status <> 'void' AND journal_id IS NOT NULL")}
        for t in TENANT:
            ctx = {"tenant_id": t}
            so_ids = [r["id"] for r in await c.fetch("SELECT id FROM sales_orders WHERE tenant_id=$1 ORDER BY id", t)]
            if sys.argv[1] == "so":
                for sid in so_ids:
                    for nk in (False, True):
                        out[f"{t}|{sid}|nk{int(nk)}"] = js(await DK.susun_dokumen(c, ctx, sid, sertakan_nk=nk))
                continue
            # ---- faktur
            uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId"=$1 ORDER BY "createdAt" LIMIT 1', t)
            req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": t, "user_id": str(uid)}), headers={})
            baris, skip = {}, 0
            while True:
                r = await SI.list_invoices(req, **kw(SI.list_invoices, skip=skip, limit=100))
                for x in r["items"]: baris[x["id"]] = x
                skip += 100
                if skip >= r["total"]: break
            bundel_so = {}
            e_sub, e_hit, e_nom, e_kep, e_def, n_dok, n_lepas, n_pro = [], [], [], [], [], 0, 0, 0
            for iid, x in baris.items():
                B = js(await DK.susun_dokumen_faktur(c, ctx, UUID(iid)))
                det = js((await SI.get_invoice(req, iid))["data"])
                docs = [(g["key"], d) for g in B["groups"] for d in g["docs"]]
                n_dok += len(docs)
                n_pro += sum(1 for _, d in docs if d["kind"] == "proforma")
                # (a) ⊆ bundel SO (medan identik; kwitansi tanpa amount; faktur draf/void bukan milik panel SO)
                sid = x.get("sales_order_id")
                if sid:
                    if sid not in bundel_so:
                        bundel_so[sid] = js(await DK.susun_dokumen(c, ctx, UUID(sid), sertakan_nk=True))
                    so_dok = {(d["kind"], d["id"]): (g["key"], d) for g in bundel_so[sid]["groups"] for d in g["docs"]}
                    for gk, d in docs:
                        if d["kind"] == "invoice" and x["status"] in ("draft", "void"):
                            continue
                        hit = so_dok.get((d["kind"], d["id"]))
                        if hit is None:
                            e_sub.append((x["invoice_number"], d["kind"], d.get("number"), "tak ada di bundel SO")); continue
                        abaikan = {"amount"} if d["kind"] == "receipt" else set()
                        if hit[0] != gk or {k: v for k, v in d.items() if k not in abaikan} != \
                                {k: v for k, v in hit[1].items() if k not in abaikan}:
                            e_sub.append((x["invoice_number"], d["kind"], d.get("number"), gk, hit[0]))
                else:
                    n_lepas += 1
                    if any(gk in ("rekap", "pnw", "pro") for gk, _ in docs):
                        e_sub.append((x["invoice_number"], "faktur lepas memuat asal SO"))
                # (b) jumlah per jenis == documents[] daftar
                hit_b = Counter(d["kind"] for _, d in docs if d["kind"] in ("invoice", "receipt", "credit_note"))
                hit_l = Counter(d["type"] for d in x["documents"])
                if hit_b != hit_l:
                    e_hit.append((x["invoice_number"], dict(hit_b), dict(hit_l)))
                # (c) nominal kwitansi == alokasi aktif (penerimaan) / penerapan aktif (uang muka) di detail
                harap = Counter()
                for p in det["payments"]:
                    if p["allocation_status"] == "active":
                        harap[p["id"]] += Decimal(str(p["allocated_amount"]))
                for a in det["deposit_applications"]:
                    # kwitansi uang muka = uang muka ber-jurnal tak void (SAMA dgn SQL_KWITANSI_FAKTUR daftar)
                    if a["status"] == "active" and a["deposit_id"] in sah_dp:
                        harap[a["deposit_id"]] += Decimal(str(a["amount_applied"]))
                dapat = {d["id"]: Decimal(d["amount"]) for _, d in docs if d["kind"] == "receipt"}
                if {k: v for k, v in harap.items() if v} != dapat:
                    e_nom.append((x["invoice_number"], {k[:8]: str(v) for k, v in harap.items()},
                                  {k[:8]: str(v) for k, v in dapat.items()}))
                # (d) kepala == detail
                A = B["anchor"]
                if (A["position_text"], Decimal(A["paid"]), Decimal(A["balance"]), A["number"]) != \
                        (det["position_text"], Decimal(str(det["amount_paid"])), Decimal(str(det["amount_due"])),
                         det["invoice_number"]):
                    e_kep.append((x["invoice_number"], A["position_text"], A["paid"], A["balance"],
                                  det["position_text"], det["amount_paid"], det["amount_due"]))
                # (e) default = faktur ini
                g = next(g for g in B["groups"] if g["key"] == B["default"]["group"])
                dd = g["docs"][B["default"]["index"]]
                if (dd["kind"], dd["id"]) != ("invoice", iid):
                    e_def.append(x["invoice_number"])
            cek(not e_sub, f"{t}: bundel faktur ⊆ bundel SO, medan identik ({len(baris)} faktur, {n_dok} dok, {n_pro} proforma asal)",
                str(e_sub[:3]))
            cek(not e_hit, f"{t}: jumlah faktur/kwitansi/NK == documents[] daftar", str(e_hit[:3]))
            cek(not e_nom, f"{t}: nominal kwitansi == bagian faktur ini (alokasi/penerapan aktif detail)", str(e_nom[:3]))
            cek(not e_kep, f"{t}: kepala anchor == detail (posisi, dibayar, sisa)", str(e_kep[:3]))
            cek(not e_def, f"{t}: default = faktur ini", str(e_def[:3]))
            print(f"  {t}: faktur lepas (tanpa SO) diuji = {n_lepas}")
    finally:
        await tx.rollback(); await c.close()
    if sys.argv[1] == "so":
        json.dump(out, open(sys.argv[2], "w"), sort_keys=True)
        print("dump", len(out))
    print("RINGKAS gagal=%d" % gagal)

asyncio.run(main())
