"""UJI NYATA Pengiriman / pill perlu_difaktur (21 Okt 2026): BACA-SAJA (default_transaction_read_only=on). Mode: baru [out.json]
Data nyata hari ini 0 baris -> predikat SQL dibuktikan pada data SINTETIS: relasi tiruan (CTE yang menutupi nama tabel) dijalankan pada
fragmen SQL YANG SAMA dengan produksi (services.pengiriman_daftar.sql_perlu_difaktur), dibandingkan dengan acuan Python independen.
  A. sintetis  : 13 skenario (belum difaktur, lunas, sebagian, faktur draf/void tak dihitung, SJ batal, asal faktur, non-stok, perlu_kirim NULL
                 mengikuti track_inventory, dua SJ satu baris, kelebihan faktur, faktur tenant lain) -> himpunan SQL == acuan.
  B. nyata     : per tenant, himpunan SQL pada tabel asli == acuan Python dari baris mentah (independen); ?aksi=perlu_difaktur total ==
                 hitungan baris == summary.perlu_difaktur_count; aksi baris == gabung_aksi(prioritas); invarian: SJ perlu_difaktur => SO induknya
                 punya perlu_ditagih (so_aksi); detail == baris.
"""
import asyncio, inspect, os, sys, uuid
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
from decimal import Decimal
import asyncpg
from fastapi.params import Query as QP
from starlette.requests import Request
from app.routers import deliveries as DL
from app.services import pengiriman_daftar as PG

TEN = ["kaos-biru-konveksi", "grapgrap-manado"]
gagal = []


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:200]}"))
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


def acuan(sj, ifi, soi, prod, fak_item, fak, tenant):
    """Acuan Python INDEPENDEN (tanpa SQL): himpunan id SJ yang perlu difaktur."""
    aktif = lambda f: f["status"] != "voided" and f["voided_at"] is None
    sj_by = {f["id"]: f for f in sj}
    hasil = set()
    for f in sj:
        if not (f["status"] == "posted" and f["voided_at"] is None and f["asal"] == "so"): continue
        for i in [x for x in ifi if x["fulfillment_id"] == f["id"] and x["soi"]]:
            s = next((x for x in soi if x["id"] == i["soi"]), None)
            if not s: continue
            p = next((x for x in prod if x["id"] == s["item_id"] and x["tenant_id"] == f["tenant_id"]), None)
            perlu = s["perlu_kirim"] if s["perlu_kirim"] is not None else (p["track"] if p else False)
            if not perlu: continue
            terkirim = Decimal(0)
            for x in ifi:
                g = sj_by.get(x["fulfillment_id"])
                if not g or g["tenant_id"] != tenant or not aktif(g): continue
                if x["inv_item"]:   # jalur faktur
                    fi = next((y for y in fak_item if y["id"] == x["inv_item"]), None)
                    if fi and fi["soi"] == s["id"]: terkirim += x["q"]
                elif g["asal"] == "so" and x["soi"] == s["id"]:   # jalur asal-so
                    terkirim += x["q"]
            difaktur = sum((y["q"] for y in fak_item if y["soi"] == s["id"] and any(
                z["id"] == y["inv"] and z["tenant_id"] == tenant and z["status"] not in ("draft", "void") for z in fak)), Decimal(0))
            if terkirim - difaktur > 0: hasil.add(f["id"])
    return hasil


def cte(sj, ifi, soi, prod, fak_item, fak):
    def vals(nama, kolom, baris):
        if not baris:
            return f"{nama} AS (SELECT {', '.join(f'NULL::{t} AS {c}' for c, t in kolom)} WHERE false)"
        rows = ", ".join("(" + ", ".join(f"NULL::{t}" if r[c] is None else (f"'{r[c]}'::{t}" if t in ("uuid", "text", "date") else (f"{str(r[c]).lower()}::boolean" if t == "boolean" else f"{r[c]}::{t}")) for c, t in kolom) + ")" for r in baris)
        return f"{nama} AS (SELECT * FROM (VALUES {rows}) v({', '.join(c for c, _ in kolom)}))"
    return "WITH " + ", ".join([
        vals("invoice_fulfillments", [("id", "uuid"), ("tenant_id", "text"), ("status", "text"), ("voided_at", "text"), ("asal", "text")], sj),
        vals("invoice_fulfillment_items", [("fulfillment_id", "uuid"), ("invoice_item_id", "uuid"), ("sales_order_item_id", "uuid"), ("quantity", "numeric")],
             [{"fulfillment_id": x["fulfillment_id"], "invoice_item_id": x["inv_item"], "sales_order_item_id": x["soi"], "quantity": x["q"]} for x in ifi]),
        vals("sales_order_items", [("id", "uuid"), ("item_id", "uuid"), ("perlu_kirim", "boolean")], soi),
        vals("products", [("id", "uuid"), ("tenant_id", "text"), ("track_inventory", "boolean")],
             [{"id": p["id"], "tenant_id": p["tenant_id"], "track_inventory": p["track"]} for p in prod]),
        vals("sales_invoice_items", [("id", "uuid"), ("invoice_id", "uuid"), ("sales_order_item_id", "uuid"), ("quantity", "numeric")],
             [{"id": y["id"], "invoice_id": y["inv"], "sales_order_item_id": y["soi"], "quantity": y["q"]} for y in fak_item]),
        vals("sales_invoices", [("id", "uuid"), ("tenant_id", "text"), ("status", "text")], fak),
    ])


async def sintetis(c):
    T = "tenant-uji"
    sj, ifi, soi, prod, fak_item, fak = [], [], [], [], [], []
    stok, jasa = U(), U()
    prod += [{"id": stok, "tenant_id": T, "track": True}, {"id": jasa, "tenant_id": T, "track": False}]
    nama = {}

    def kasus(n, asal="so", status="posted", voided=None, item=None, perlu=True, q=10, faktur=(), tenant=T, sj_lain=()):
        f, s = U(), U(); nama[f] = n
        sj.append({"id": f, "tenant_id": T, "status": status, "voided_at": voided, "asal": asal})
        soi.append({"id": s, "item_id": item or stok, "perlu_kirim": perlu})
        ifi.append({"fulfillment_id": f, "inv_item": None, "soi": s, "q": Decimal(q)})
        for (st, qq, ten) in faktur:
            i = U(); fak.append({"id": i, "tenant_id": ten or T, "status": st})
            fak_item.append({"id": U(), "inv": i, "soi": s, "q": Decimal(qq)})
        for (st2, vd2, q2) in sj_lain:   # SJ lain untuk baris SO yang sama
            g = U(); nama[g] = n + "-lain"
            sj.append({"id": g, "tenant_id": T, "status": st2, "voided_at": vd2, "asal": "so"})
            ifi.append({"fulfillment_id": g, "inv_item": None, "soi": s, "q": Decimal(q2)})
        return f
    kasus("belum difaktur")
    kasus("lunas", faktur=[("posted", 10, None)])
    kasus("sebagian", faktur=[("posted", 4, None)])
    kasus("faktur draf tak dihitung", faktur=[("draft", 10, None)])
    kasus("faktur void tak dihitung", faktur=[("void", 10, None)])
    kasus("SJ batal", status="voided")
    kasus("asal faktur", asal="faktur")
    kasus("jasa (perlu_kirim false)", perlu=False, item=jasa)
    kasus("perlu_kirim NULL ikut stok", perlu=None)
    kasus("perlu_kirim NULL ikut jasa", perlu=None, item=jasa)
    kasus("dua SJ satu baris: satu batal", q=5, faktur=[("posted", 5, None)], sj_lain=[("voided", None, 5)])
    kasus("kelebihan faktur", faktur=[("posted", 12, None)])
    kasus("faktur tenant lain tak dihitung", faktur=[("posted", 10, "tenant-lain")])
    harap = acuan(sj, ifi, soi, prod, fak_item, fak, T)
    sql = (cte(sj, ifi, soi, prod, fak_item, fak) + " SELECT f.id FROM invoice_fulfillments f WHERE f.tenant_id = $1 AND " + PG.sql_perlu_difaktur("f"))
    dapat = {str(r["id"]) for r in await c.fetch(sql, T)}
    cek("sintetis: himpunan SQL == acuan Python", dapat == {str(x) for x in harap}, (sorted(nama[x] for x in dapat - {str(y) for y in harap}), sorted(nama[y] for y in harap if str(y) not in dapat)))
    nama_dapat = sorted(nama[u] for u in dapat if u in nama)
    harapan_nama = sorted(["belum difaktur", "sebagian", "faktur draf tak dihitung", "faktur void tak dihitung", "perlu_kirim NULL ikut stok", "faktur tenant lain tak dihitung"])
    cek("sintetis: himpunan == yang dimaksud skenario", nama_dapat == harapan_nama, (nama_dapat, harapan_nama))
    return acuan, harap


async def nyata(c):
    for t in TEN:
        uid = str(await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", t)); r = req(t, uid)
        tagih = await PG.tagih_sesudah_kirim(c, t)
        # acuan independen dari baris mentah
        sj = [dict(x) | {"voided_at": x["voided_at"]} for x in await c.fetch("SELECT id, tenant_id, status, voided_at, asal FROM invoice_fulfillments WHERE tenant_id=$1", t)]
        ifi = [{"fulfillment_id": x["fulfillment_id"], "inv_item": x["invoice_item_id"], "soi": x["sales_order_item_id"], "q": Decimal(str(x["quantity"]))} for x in await c.fetch(
            "SELECT i.fulfillment_id, i.invoice_item_id, i.sales_order_item_id, i.quantity FROM invoice_fulfillment_items i JOIN invoice_fulfillments f ON f.id=i.fulfillment_id WHERE f.tenant_id=$1", t)]
        soi = [dict(x) for x in await c.fetch("SELECT soi.id, soi.item_id, soi.perlu_kirim FROM sales_order_items soi JOIN sales_orders so ON so.id=soi.sales_order_id WHERE so.tenant_id=$1", t)]
        prod = [{"id": x["id"], "tenant_id": x["tenant_id"], "track": bool(x["track_inventory"])} for x in await c.fetch("SELECT id, tenant_id, track_inventory FROM products WHERE tenant_id=$1", t)]
        fak = [dict(x) for x in await c.fetch("SELECT id, tenant_id, status FROM sales_invoices WHERE tenant_id=$1", t)]
        fak_item = [{"id": x["id"], "inv": x["invoice_id"], "soi": x["sales_order_item_id"], "q": Decimal(str(x["quantity"]))} for x in await c.fetch(
            "SELECT sii.id, sii.invoice_id, sii.sales_order_item_id, sii.quantity FROM sales_invoice_items sii JOIN sales_invoices si ON si.id=sii.invoice_id WHERE si.tenant_id=$1", t)]
        harap = set() if not tagih else acuan(sj, ifi, soi, prod, fak_item, fak, t)
        sql = {x["id"] for x in await c.fetch(f"SELECT f.id FROM invoice_fulfillments f WHERE f.tenant_id=$1 AND {PG.sql_perlu_difaktur('f')}", t)} if tagih else set()
        cek(f"{t} nyata: SQL == acuan Python (tagih_sesudah_kirim={tagih}) n={len(harap)}", sql == harap, (len(sql), len(harap)))
        semua = (await DL.list_deliveries(**kw(DL.list_deliveries, r, per_page=100)))["items"]
        ids_baris = {x["id"] for x in semua if x["aksi"] == "perlu_difaktur"}
        cek(f"{t} nyata: aksi baris == himpunan acuan (yang tak berserah)", ids_baris <= {str(x) for x in harap}, (ids_baris,))
        lst = await DL.list_deliveries(**kw(DL.list_deliveries, r, per_page=100, aksi="perlu_difaktur"))
        sm = await DL.get_deliveries_summary(**kw(DL.get_deliveries_summary, r))
        cek(f"{t} nyata: ?aksi total == len == summary == acuan", lst["total"] == len(lst["items"]) == sm["perlu_difaktur_count"] == len(harap), (lst["total"], len(lst["items"]), sm["perlu_difaktur_count"], len(harap)))
        for it in lst["items"][:20]:
            d = await DL.get_delivery_detail(**kw(DL.get_delivery_detail, r, delivery_id=it["id"]))
            cek(f"{t} detail==baris {it['delivery_number']}", d["aksi"] == it["aksi"], (d["aksi"], it["aksi"]))
        # prioritas: aksi serah menang
        for it in semua:
            if it["aksi"] == "perlu_difaktur":
                cek(f"{t} prioritas {it['delivery_number']}", PG.gabung_aksi("perlu_resi", True) == "perlu_resi")
        # invarian SO: SJ perlu_difaktur => SO induknya perlu_ditagih
        from app.services import so_aksi
        for it in lst["items"]:
            f = await so_aksi.fakta(c, t, __import__("datetime").date.today(), [UUID_(it["sales_order_id"])])
            cek(f"{t} invarian SO perlu_ditagih {it['delivery_number']}", "perlu_ditagih" in next(iter(f.values()))["aksi"])
    cek("prioritas gabung_aksi", PG.gabung_aksi(None, True) == "perlu_difaktur" and PG.gabung_aksi("belum_dicatat", True) == "belum_dicatat" and PG.gabung_aksi(None, False) is None)


def UUID_(x): return uuid.UUID(str(x))


async def main():
    c = await asyncpg.connect(os.environ["URL"]); await c.execute("SET default_transaction_read_only = on")
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    DL.get_pool = gp
    await sintetis(c)
    await nyata(c)
    print("RINGKAS PERLU-DIFAKTUR gagal=%d %s" % (len(gagal), gagal[:4]))
    await c.close()
    sys.exit(1 if gagal else 0)

asyncio.run(main())
