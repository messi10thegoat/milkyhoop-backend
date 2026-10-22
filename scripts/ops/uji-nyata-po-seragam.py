"""UJI NYATA Pesanan Pembelian seragam (22 Okt 2026, P-U2): BACA-SAJA. Mode: baru [out.json]
 A. SINTETIS (data nyata PO = 0 baris): relasi tiruan (CTE yang menutupi purchase_orders/purchase_order_items) dijalankan pada fragmen SQL YANG SAMA
    dgn produksi (po_daftar.SQL_AGREGAT + sql_aksi): himpunan per pill == aksi_po() Python; PARTISI (tiap PO paling banyak SATU pill == aksi_po);
    amount_received/billed SQL == jumlah_turunan Python (2dp HALF_UP).
 B. NYATA (kaos+grapgrap): list/summary/detail/documents dipanggil sungguhan: ?aksi=k total == len == summary k_count; sum_total==Σ; page/per_page/offset;
    has_more; paritas detail == baris; urutan deterministik. (0 baris = SQL terurai & berjalan; hitungan tetap konsisten.)"""
import asyncio, inspect, os, sys, uuid
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
from datetime import date, timedelta
from decimal import Decimal
import asyncpg
from fastapi.params import Query as QP
from starlette.requests import Request
from app.routers import purchase_orders as PO
from app.services import po_daftar as PD

gagal = []
H = date(2026, 10, 22)


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:220]}"))
    if not ok: gagal.append(nama)


def U(): return str(uuid.uuid4())


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


def vals(nama, kolom, baris):
    if not baris:
        return f"{nama} AS (SELECT {', '.join(f'NULL::{t} AS {c}' for c, t in kolom)} WHERE false)"
    def lit(v, t):
        if v is None: return f"NULL::{t}"
        return f"'{v}'::{t}" if t in ("uuid", "text", "date") else f"{v}::{t}"
    rows = ", ".join("(" + ", ".join(lit(r[c], t) for c, t in kolom) + ")" for r in baris)
    return f"{nama} AS (SELECT * FROM (VALUES {rows}) v({', '.join(c for c, _ in kolom)}))"


async def sintetis(c):
    T = "tenant-uji"
    po, it, nama = [], [], {}
    def buat(n, status, tgl, baris):          # baris: [(qty, recv, billed, total)]
        i = U(); nama[i] = n
        po.append(dict(id=i, tenant_id=T, status=status, expected_date=tgl.isoformat() if tgl else None,
                       total_amount=sum(Decimal(b[3]) for b in baris) if baris else 0))
        for q, rc, bl, tot in baris:
            it.append(dict(po_id=i, quantity=q, quantity_received=rc, quantity_billed=bl, total=tot))
        return i
    d = lambda n: H + timedelta(days=n)
    buat("draft", "draft", d(-9), [(10, 0, 0, "1000.00")])
    buat("sent tanpa tanggal", "sent", None, [(10, 0, 0, "1000.00")])
    buat("sent besok", "sent", d(1), [(10, 0, 0, "1000.00")])
    buat("sent hari ini", "sent", d(0), [(10, 0, 0, "1000.00")])
    buat("sent telat", "sent", d(-3), [(10, 0, 0, "1000.00")])
    buat("partial_received telat sisa", "partial_received", d(-3), [(10, 4, 0, "1000.00")])
    buat("partial_received akan, diterima>ditagih", "partial_received", d(5), [(10, 4, 1, "1000.00")])
    buat("partial_received tanpa tgl, ditagih=diterima", "partial_received", None, [(10, 4, 4, "1000.00")])
    buat("received belum ditagih", "received", d(-30), [(10, 10, 0, "1000.00")])
    buat("received sudah ditagih", "received", d(-30), [(10, 10, 10, "1000.00")])
    buat("partial_billed masih ada tagih", "partial_billed", d(-30), [(10, 10, 6, "1000.00")])
    buat("billed", "billed", d(-30), [(10, 10, 10, "1000.00")])
    buat("closed", "closed", d(-30), [(10, 10, 10, "1000.00")])
    buat("cancelled", "cancelled", d(-3), [(10, 0, 0, "1000.00")])
    buat("dua baris: satu lunas satu belum diterima", "partial_received", d(2), [(3, 3, 3, "100.00"), (7, 0, 0, "233.34")])
    buat("pecahan HALF_UP", "partial_received", d(2), [(3, 1, 0, "100.00")])
    buat("closed belum ditagih (ditutup manual)", "closed", d(-30), [(10, 10, 0, "1000.00")])
    buat("closed ditagih sebagian", "closed", d(-30), [(10, 10, 4, "1000.00")])
    cte = "WITH " + ", ".join([
        vals("purchase_orders", [("id", "uuid"), ("tenant_id", "text"), ("status", "text"), ("expected_date", "date"), ("total_amount", "numeric")], po),
        vals("purchase_order_items", [("po_id", "uuid"), ("quantity", "numeric"), ("quantity_received", "numeric"), ("quantity_billed", "numeric"), ("total", "numeric")], it)]) + " "
    # acuan Python per PO
    ref = {}
    for p in po:
        brs = [x for x in it if x["po_id"] == p["id"]]
        sisa = sum((max(Decimal(str(x["quantity"])) - Decimal(str(x["quantity_received"])), Decimal(0)) for x in brs), Decimal(0))
        belum = sum((max(Decimal(str(x["quantity_received"])) - Decimal(str(x["quantity_billed"])), Decimal(0)) for x in brs), Decimal(0))
        tgl = date.fromisoformat(p["expected_date"]) if p["expected_date"] else None
        ref[p["id"]] = (PD.aksi_po(p["status"], tgl, sisa, belum, H), PD.jumlah_turunan(brs))
    # himpunan SQL per pill == acuan; partisi
    dipakai = {}
    for k in PD.AKSI:
        sql = cte + f"SELECT po.id FROM purchase_orders po {PD.SQL_AGREGAT} WHERE po.tenant_id = $1 AND $2::date IS NOT NULL AND {PD.sql_aksi(k, '$2')}"
        dapat = {str(r['id']) for r in await c.fetch(sql, T, H)}
        harap = {i for i, (a, _) in ref.items() if a == k}
        cek(f"sintetis: pill {k} SQL == aksi_po Python ({len(dapat)} PO)", dapat == harap, (sorted(nama[x] for x in dapat ^ harap),))
        for i in dapat: dipakai.setdefault(i, []).append(k)
    cek("sintetis: PARTISI -- tiap PO paling banyak satu pill", all(len(v) == 1 for v in dipakai.values()), {nama[i]: v for i, v in dipakai.items() if len(v) > 1})
    # jumlah turunan SQL == Python
    sql = cte + f"SELECT po.id, ag.amount_received, ag.amount_billed, ag.sisa_terima, ag.belum_ditagih FROM purchase_orders po {PD.SQL_AGREGAT} WHERE po.tenant_id = $1"
    ok = True; rinci = []
    for r in await c.fetch(sql, T):
        j = ref[str(r["id"])][1]
        if PD.r2(r["amount_received"]) != j["amount_received"] or PD.r2(r["amount_billed"]) != j["amount_billed"]:
            ok = False; rinci.append((nama[str(r["id"])], r["amount_received"], j["amount_received"]))
    cek("sintetis: amount_received/billed SQL == jumlah_turunan Python (HALF_UP per baris)", ok, rinci)
    cek("sintetis: ada skenario di tiap pill", all(any(a == k for a, _ in ref.values()) for k in PD.AKSI))
    # sisa ditagih SQL (with_totals) == aturan fakta_po (closed/cancelled -> 0)
    sql = cte + f"SELECT po.id, po.status, po.total_amount, ag.amount_billed, {PD.sql_sisa_ditagih()} AS sisa FROM purchase_orders po {PD.SQL_AGREGAT} WHERE po.tenant_id = $1"
    ok = True; rinci = []
    for r in await c.fetch(sql, T):
        harap = Decimal(0) if r["status"] in PD.STATUS_TANPA_SISA_TAGIH else max(PD.r2(r["total_amount"]) - PD.r2(r["amount_billed"]), Decimal(0))
        if PD.r2(r["sisa"]) != harap:
            ok = False; rinci.append((nama[str(r["id"])], r["sisa"], harap))
    cek("sintetis: sisa ditagih SQL == aturan Python (closed/cancelled = 0)", ok, rinci)
    tutup = [i for i, n in nama.items() if n.startswith("closed belum")]
    r = await c.fetchrow(cte + f"SELECT {PD.sql_sisa_ditagih()} AS sisa FROM purchase_orders po {PD.SQL_AGREGAT} WHERE po.id = $1::uuid", tutup[0])
    cek("sintetis: PO closed belum ditagih -> sisa 0 (bukan total)", PD.r2(r["sisa"]) == 0, r["sisa"])


async def nyata(c):
    for t in ("kaos-biru-konveksi", "grapgrap-manado"):
        uid = str(await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", t)); r = req(t, uid)
        sm = (await PO.get_purchase_orders_summary(**kw(PO.get_purchase_orders_summary, r)))["data"]
        semua = await PO.list_purchase_orders(**kw(PO.list_purchase_orders, r, per_page=100, with_totals=True))
        cek(f"{t}: list total == summary.total ({semua['total']})", semua["total"] == sm["total"], (semua["total"], sm["total"]))
        cek(f"{t}: sum_sisa == Σ sisa_ditagih baris", semua["total"] > 100 or Decimal(str(semua["sum_sisa"])) == sum((Decimal(str(i["sisa_ditagih"])) for i in semua["items"]), Decimal(0)),
            (semua["sum_sisa"], [(i["po_number"], i["status"], i["sisa_ditagih"]) for i in semua["items"]][:5]))
        cek(f"{t}: PO closed/cancelled sisa_ditagih 0", all(i["sisa_ditagih"] == 0 for i in semua["items"] if i["status"] in PD.STATUS_TANPA_SISA_TAGIH))
        cek(f"{t}: sum_total == Σ baris non-cancelled", Decimal(str(semua["sum_total"])) == sum((Decimal(str(i['total_amount'])) for i in semua["items"] if i["status"] != "cancelled"), Decimal(0)) or semua["total"] > 100)
        for k in PD.AKSI:
            lst = await PO.list_purchase_orders(**kw(PO.list_purchase_orders, r, per_page=100, aksi=k))
            cek(f"{t}: ?aksi={k} total == len == summary {k}_count", lst["total"] == len(lst["items"]) == sm[f"{k}_count"] and all(i["aksi"] == k for i in lst["items"]), (lst["total"], sm[f"{k}_count"]))
        for i in semua["items"][:20]:
            dt = (await PO.get_purchase_order(**kw(PO.get_purchase_order, r, po_id=uuid.UUID(i["id"]))))["data"]
            beda = [x for x in ("aksi", "position_text", "terima_keadaan", "terima_teks", "terima_hari", "terima_terbuka", "documents", "doc_count", "amount_received", "amount_billed", "sisa_ditagih") if dt.get(x) != i.get(x)]
            cek(f"{t}: detail == baris {i['po_number']}", not beda, beda)
            b = await PO.get_purchase_order_documents(**kw(PO.get_purchase_order_documents, r, po_id=uuid.UUID(i["id"])))
            tipe = [{"type": d["kind"]} for g in b["groups"] for d in g["docs"]]
            cek(f"{t}: documents == bundel {i['po_number']}", i["documents"] == tipe and i["doc_count"] == len(tipe), (i["documents"], tipe))
        p1 = await PO.list_purchase_orders(**kw(PO.list_purchase_orders, r, per_page=5, page=2))
        p2 = await PO.list_purchase_orders(**kw(PO.list_purchase_orders, r, per_page=5, offset=5))
        p3 = await PO.list_purchase_orders(**kw(PO.list_purchase_orders, r, limit=5, skip=5))
        cek(f"{t}: page2 == offset5 == skip5", [i["id"] for i in p1["items"]] == [i["id"] for i in p2["items"]] == [i["id"] for i in p3["items"]])
        for cari in ("PO", "a b"):
            lc = await PO.list_purchase_orders(**kw(PO.list_purchase_orders, r, per_page=1, search=cari))
            sc = (await PO.get_purchase_orders_summary(**kw(PO.get_purchase_orders_summary, r, search=cari)))["data"]
            cek(f"{t}: summary?search={cari} total == daftar total", sc["total"] == lc["total"], (sc["total"], lc["total"]))
    # SQL bundel dijalankan sungguhan (id tak ada -> kosong): bills/vendor_deposits lewat dokumen_po, bayar lewat SQL_BAYAR
    await PD.dokumen_po(c, "kaos-biru-konveksi", [uuid.uuid4()])
    await c.fetch(PD.SQL_BAYAR, "kaos-biru-konveksi", [uuid.uuid4()])
    cek("SQL bundel (po/faktur/uang_muka/bayar) terurai & berjalan di skema nyata", True)
    # riwayat PO: SQL siklus + tagihan bertaut terurai di skema nyata; PO tak ada -> 404
    import inspect as _ins, re as _re
    from app.services import po_riwayat as PRW
    for _s in _re.findall(r'"""(.*?)"""', _ins.getsource(PRW.riwayat_po), flags=_re.S):
        if _re.match(r"\s*SELECT", _s):
            await c.prepare(_s)
    r0 = req("kaos-biru-konveksi", str(uuid.uuid4()))
    try:
        await PO.get_purchase_order_history(**kw(PO.get_purchase_order_history, r0, po_id=uuid.uuid4()))
        cek("riwayat PO tak ada -> 404", False, "tidak 404")
    except Exception as e:
        cek("riwayat PO: SQL terurai di skema nyata; PO tak ada -> 404", getattr(e, "status_code", None) == 404, e)
    print("INFO PO nyata = %d baris (0 = pembuktian andalkan sintetis + uji tulis TES E2E)" % await c.fetchval("SELECT count(*) FROM purchase_orders"))


async def main():
    c = await asyncpg.connect(os.environ["URL"]); await c.execute("SET default_transaction_read_only = on")
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    PO.get_pool = gp
    await sintetis(c)
    await nyata(c)
    print("RINGKAS PO-SERAGAM gagal=%d %s" % (len(gagal), gagal[:4]))
    await c.close(); sys.exit(1 if gagal else 0)

asyncio.run(main())
