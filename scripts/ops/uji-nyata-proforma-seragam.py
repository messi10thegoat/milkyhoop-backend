"""UJI NYATA PENJUALAN-SERAGAM / Proforma (21 Okt 2026): BACA-SAJA, kaos+grapgrap. Mode lama|baru|banding.
Syarat: field lama IDENTIK (daftar/summary/detail); baru: telat==aksi lewat_tempo, documents==bundel tanpa rekap + hitungan independen (pro=1, kw=uang muka
bertaut ber-jurnal), status dokumen jangkar (draf/batal/lunas/terbit), sum_total/sum_sisa == SQL independen & summary, summary?search.total_count==daftar?search.total."""
import asyncio, inspect, json, os, sys
from decimal import Decimal
from uuid import UUID
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from fastapi.params import Query as QP
from starlette.requests import Request
from app.routers import proformas as PF

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


async def semua(r, **ov):
    items, skip = [], 0
    while True:
        res = await PF.list_proformas(**kw(PF.list_proformas, r, limit=100, skip=skip, **ov))
        items += res["items"]
        if not res["has_more"] or skip > 2000: return items, res
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
    PF.get_pool = gp
    out = {}
    for t in TEN:
        uid = str(await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", t)); r = req(t, uid)
        V = [("all", dict(limit=100))] + [(f"status={s}", dict(status=s, limit=100)) for s in ("draft", "issued", "cancelled", "expired")] \
            + [(f"aksi={a}", dict(aksi=a, limit=100)) for a in ("perlu_diterbitkan", "menunggu_bayar", "segera_tempo", "lewat_tempo")] \
            + [("tugas", dict(tugas="terbit_belum_lunas", limit=100)), ("cari a", dict(search="a", limit=100)), ("cari 2", dict(search="kaos biru", limit=100)), ("skip", dict(skip=20, limit=20))]
        for nama, ov in V:
            out[f"{t}|PL|{nama}"] = await PF.list_proformas(**kw(PF.list_proformas, r, **ov))
        out[f"{t}|PS"] = await PF.get_proforma_summary(**kw(PF.get_proforma_summary, r))
        base = (await PF.list_proformas(**kw(PF.list_proformas, r, limit=100)))["items"]
        for it in base[:12]:
            out[f"{t}|PD|{it['id']}"] = await PF.get_proforma_detail(**kw(PF.get_proforma_detail, r, proforma_id=it["id"]))
        if mode == "baru":
            from app.routers import dokumen as DK
            semua_p, resp = await semua(r)
            summ = (await PF.get_proforma_summary(**kw(PF.get_proforma_summary, r)))["data"]
            n_ok = 0
            for it in semua_p:
                cek(f"{t} telat==lewat_tempo {it['proforma_number']}", (it["jatuh_tempo_keadaan"] == "telat") == (it["aksi"] == "lewat_tempo"), (it["jatuh_tempo_keadaan"], it["aksi"]))
                b = await DK.susun_dokumen_proforma(c, {"tenant_id": t, "user_id": None}, UUID(it["id"]))
                tipe = [{"type": x["kind"]} for g in b["groups"] for x in g["docs"] if g["key"] != "rekap"]
                cek(f"{t} documents==bundel {it['proforma_number']}", it["documents"] == tipe and it["doc_count"] == len(tipe), (it["documents"], tipe))
                pro = [x for g in b["groups"] if g["key"] == "pro" for x in g["docs"]]
                cek(f"{t} pro=1 {it['proforma_number']}", len(pro) == 1 and pro[0]["id"] == it["id"] and pro[0]["number"] == it["proforma_number"], pro)
                st = pro[0]["status"] if pro else None
                lunas = bool(it.get("is_fully_paid")) and it["status"] == "issued"
                esp = "draf" if it["status"] == "draft" else "batal" if it["status"] == "cancelled" else ("lunas" if lunas else "terbit")
                if it["status"] == "expired": esp = "terbit"
                cek(f"{t} status jangkar {it['proforma_number']}", st == esp, (st, esp, it["status"]))
                n_dep = await c.fetchval("SELECT count(*) FROM customer_deposits WHERE tenant_id=$1 AND proforma_id=$2 AND status<>'void' AND journal_id IS NOT NULL", t, UUID(it["id"]))
                kw_n = sum(1 for g in b["groups"] if g["key"] == "kw" for x in g["docs"])
                cek(f"{t} kwitansi independen {it['proforma_number']}", kw_n == n_dep, (kw_n, n_dep))
                cek(f"{t} bundel lengkap {it['proforma_number']}", all({"sent", "batal", "status"} <= set(x) for g in b["groups"] for x in g["docs"]))
                cek(f"{t} anchor {it['proforma_number']}", b["anchor"]["kind"] == "proforma" and b["anchor"]["total"] == f"{Decimal(str(it['amount'])):.2f}", b["anchor"])
                n_ok += 1
            tot = await PF.list_proformas(**kw(PF.list_proformas, r, limit=100, with_totals=True))
            exp_total = await c.fetchval("SELECT COALESCE(SUM(amount),0) FROM proformas WHERE tenant_id=$1 AND status<>'cancelled'", t)
            cek(f"{t} sum_total", abs(Decimal(str(tot["sum_total"])) - Decimal(str(exp_total))) < Decimal("0.01"), (tot["sum_total"], exp_total))
            cek(f"{t} sum_sisa == summary.issued_outstanding", abs(tot["sum_sisa"] - summ["issued_outstanding_amount"]) < 0.01, (tot["sum_sisa"], summ["issued_outstanding_amount"]))
            sub = await PF.list_proformas(**kw(PF.list_proformas, r, limit=100, status="issued", with_totals=True))
            cek(f"{t} sum_sisa(issued)==sum_sisa(all)", abs(sub["sum_sisa"] - tot["sum_sisa"]) < 0.01)
            for cari in ("a", "kaos biru"):
                lc = await PF.list_proformas(**kw(PF.list_proformas, r, limit=1, search=cari))
                sc = (await PF.get_proforma_summary(**kw(PF.get_proforma_summary, r, search=cari)))["data"]
                cek(f"{t} summary?search={cari}", sc["total_count"] == lc["total"], (sc["total_count"], lc["total"]))
            print(f"[baru] {t}: {n_ok} proforma, bundel dicek; sum_total={tot['sum_total']} sum_sisa={tot['sum_sisa']}")
    if mode in ("lama", "baru"):
        json.dump(out, open(sys.argv[2], "w"), default=str, sort_keys=True); print(mode, len(out), "variant")
    if mode == "baru":
        print("RINGKAS KONSISTENSI gagal=%d %s" % (len(gagal), gagal[:4])); sys.exit(1 if gagal else 0)
    await c.close()


async def banding():
    a, b = json.load(open(sys.argv[2])), json.load(open(sys.argv[3]))
    BARU = {"jatuh_tempo_terbuka", "jatuh_tempo_hari", "jatuh_tempo_keadaan", "jatuh_tempo_teks", "documents", "doc_count", "position_text"}
    beda = []
    for k, x in a.items():
        y = b.get(k)
        if "|PL|" in k:
            if {kk: v for kk, v in x.items() if kk != "items"} != {kk: v for kk, v in y.items() if kk not in ("items", "sum_total", "sum_sisa")}: beda.append((k, "kepala"))
            if len(x["items"]) != len(y["items"]): beda.append((k, "jumlah"))
            for i, (p, q) in enumerate(zip(x["items"], y["items"])):
                if p != {kk: v for kk, v in q.items() if kk not in BARU}: beda.append((k, f"item{i}"))
        elif "|PD|" in k:
            # aksi di DETAIL ditambah unit paritas detail==baris (21 Okt) -> bukan medan lama
            if x["data"] != {kk: v for kk, v in y["data"].items() if kk not in BARU | {"aksi"}}: beda.append((k, "detail"))
        elif x != y: beda.append((k, "summary"))
    print("BANDING: %d variant, BEDA=%d %s" % (len(a), len(beda), beda[:5])); sys.exit(1 if beda else 0)

if sys.argv[1] == "banding": asyncio.run(banding())
else: asyncio.run(main())
