"""Harness NYATA S2 b: bundel /customer-deposits/{id}/documents + /credit-notes/{id}/documents (20 Okt 2026). BACA saja
(transaksi READ ONLY, ROLLBACK), grapgrap + kaos, SEMUA uang muka & nota kredit.
 - documents[]/doc_count baris daftar == bundel tanpa rekap (SATU definisi kelompok_untuk)
 - jangkar SELALU tampil sebagai dirinya (juga draf/void); default menunjuk jangkar
 - dokumen SO (rekap/penawaran/proforma/faktur/surat jalan) ⊆ bundel SO-nya (?sertakan=nota_kredit), medan identik
 - kepala anchor: position_text == status_text baris; balance == sisa baris (UM remaining_amount jurnal / NK sisa jurnal)"""
import asyncio, inspect, json, os, sys
from decimal import Decimal
from uuid import UUID
sys.path.insert(0, "/app"); os.environ.setdefault("OPENAI_API_KEY", "x")
from types import SimpleNamespace
import asyncpg
from backend.api_gateway.app.routers import dokumen as DK
from backend.api_gateway.app.routers import customer_deposits as CD
from backend.api_gateway.app.routers import credit_notes as CN
from backend.api_gateway.app.services import nota_kredit_daftar as NKD

gagal = 0
SO_KIND = ("rekap", "quotation", "proforma", "invoice", "delivery")


def cek(ok, l, i=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else "")); gagal += 0 if ok else 1


def kw(fn, **o):
    d = {n: getattr(p.default, "default", p.default) for n, p in inspect.signature(fn).parameters.items()
         if n != "request" and p.default is not inspect.Parameter.empty}
    d.update(o); return d


def js(o): return json.loads(json.dumps(o, sort_keys=True, default=str))


async def semua(fn, req):
    out, skip = [], 0
    while True:
        r = await fn(req, **kw(fn, skip=skip, limit=100))
        r = r if isinstance(r, dict) else r.model_dump()
        out += r["items"]; skip += 100
        if skip >= r["total"]: break
    return out


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
    CD.get_pool = gp; CN.get_pool = gp
    try:
        for t in ("grapgrap-manado", "kaos-biru-konveksi"):
            uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId"=$1 ORDER BY "createdAt" LIMIT 1', t)
            req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": t, "user_id": str(uid)}), headers={})
            ctx = {"tenant_id": t}
            so_cache = {}
            for jenis, fn, susun, grup, akind, nomor in (
                    ("UM", CD.list_customer_deposits, DK.susun_dokumen_uang_muka, "kw", "receipt", "deposit_number"),
                    ("NK", CN.list_credit_notes, DK.susun_dokumen_nota_kredit, "nk", "credit_note", "credit_note_number")):
                rows = await semua(fn, req)
                sisa_nk = await NKD.sisa_nk_jurnal_many(c, t, [r["id"] for r in rows]) if jenis == "NK" else {}
                pf_um = {str(r["id"]): str(r["proforma_id"]) for r in await c.fetch(
                    "SELECT id, proforma_id FROM customer_deposits WHERE tenant_id=$1 AND proforma_id IS NOT NULL", t)}
                e_doc, e_jkr, e_sub, e_kep, e_pro, e_st, n_dok, n_so = [], [], [], [], [], [], 0, 0
                for x in rows:
                    B = js(await susun(c, ctx, UUID(x["id"])))
                    docs = [(g["key"], d) for g in B["groups"] for d in g["docs"]]
                    n_dok += len(docs)
                    tanpa_rekap = [{"type": d["kind"]} for k, d in docs if k != "rekap"]
                    if x["documents"] != tanpa_rekap or x["doc_count"] != len(tanpa_rekap):
                        e_doc.append((x[nomor], x["doc_count"], len(tanpa_rekap)))
                    df = B["default"]
                    dd = next((g for g in B["groups"] if g["key"] == (df or {}).get("group")), None)
                    if not df or not dd or (dd["docs"][df["index"]]["kind"], dd["docs"][df["index"]]["id"]) != (akind, x["id"]):
                        e_jkr.append((x[nomor], x["status"], df))
                    # proforma = HANYA proforma asal uang muka ini (UM); NK tak punya kelompok proforma
                    pro = {d["id"] for k, d in docs if d["kind"] == "proforma"}
                    if pro and (jenis == "NK" or pro != {pf_um.get(x["id"])}):
                        e_pro.append((x[nomor], sorted(pro), pf_um.get(x["id"])))
                    # status dokumen jangkar: draf -> "draf", void -> "batal"
                    jd = dd["docs"][df["index"]] if (df and dd) else None
                    harap_st = {"draft": "draf", "void": "batal"}.get(x["status"])
                    if jd and harap_st and jd["status"] != harap_st:
                        e_st.append((x[nomor], x["status"], jd["status"]))
                    so_id = B["anchor"]["sales_order_id"]
                    if so_id:
                        n_so += 1
                        if so_id not in so_cache:
                            so_cache[so_id] = js(await DK.susun_dokumen(c, ctx, UUID(so_id), sertakan_nk=True))
                        so_dok = {(d["kind"], d["id"]): (g["key"], d) for g in so_cache[so_id]["groups"] for d in g["docs"]}
                        for k, d in docs:
                            if d["kind"] not in SO_KIND:
                                continue
                            hit = so_dok.get((d["kind"], d["id"]))
                            if hit is None or hit[0] != k or hit[1] != d:
                                e_sub.append((x[nomor], d["kind"], d.get("number"), k, hit[0] if hit else None))
                    elif any(k in ("rekap", "pnw", "pro") for k, _ in docs):
                        e_sub.append((x[nomor], "tanpa SO tapi ada asal SO"))
                    A = B["anchor"]
                    harap_bal = (x["remaining_amount"] if jenis == "UM" else
                                 (None if x["status"] == "draft" else sisa_nk[x["id"]]))
                    bal = None if A["balance"] is None else Decimal(A["balance"])
                    if A["position_text"] != x["status_text"] or (bal != (None if harap_bal is None else Decimal(str(harap_bal)))):
                        e_kep.append((x[nomor], A["position_text"], x["status_text"], A["balance"], harap_bal))
                cek(not e_doc, f"{t} {jenis}: documents[]/doc_count daftar == bundel tanpa rekap ({len(rows)} baris, {n_dok} dok)", str(e_doc[:3]))
                cek(not e_jkr, f"{t} {jenis}: jangkar selalu tampil sebagai dirinya + default menunjuknya", str(e_jkr[:3]))
                cek(not e_sub, f"{t} {jenis}: dokumen SO ⊆ bundel SO, medan identik ({n_so} ber-SO)", str(e_sub[:3]))
                cek(not e_kep, f"{t} {jenis}: kepala anchor == baris (posisi, sisa)", str(e_kep[:3]))
                cek(not e_pro, f"{t} {jenis}: proforma = HANYA proforma asal uang muka", str(e_pro[:3]))
                cek(not e_st, f"{t} {jenis}: status jangkar draf/void = draf/batal", str(e_st[:3]))
    finally:
        await tx.rollback(); await c.close()
    print("RINGKAS gagal=%d" % gagal)

asyncio.run(main())
