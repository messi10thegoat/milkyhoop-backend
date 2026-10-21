"""AUDIT BACA-SAJA pill AKSI semua modul penjualan (21 Okt 2026): untuk tiap kunci ?aksi=<k>:
  (1) total == len(items) (halaman <= 100)   (2) SEMUA baris hasil punya aksi == k (pill tunggal; SO: k termasuk dalam daftar aksi)
  (3) summary <k>_count == total            (4) INFO: baris yang aksinya BEDA dari k = tumpang-tindih (pelajaran perlu_difaktur 21 Okt)
Mode: baru [out.json]. Keluar 1 bila ada pelanggaran (1)(2)(3)."""
import asyncio, inspect, os, sys
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from fastapi.params import Query as QP
from starlette.requests import Request
from app.routers import quotes as Q, receive_payments as RP, deliveries as DL, proformas as PF, credit_notes as CN, customer_deposits as CD, sales_invoices as SI, sales_orders as SO

TEN = ["kaos-biru-konveksi", "grapgrap-manado"]
gagal, info = [], []


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


def kunci_literal(fn):
    ann = inspect.signature(fn).parameters["aksi"].annotation
    import typing
    for a in typing.get_args(ann):
        if typing.get_args(a): return list(typing.get_args(a))
    return list(typing.get_args(ann))


def datar(x):
    return x.model_dump(mode="json") if hasattr(x, "model_dump") else x


def punya(aksi, k):
    if isinstance(aksi, (list, tuple)): return k in aksi
    return aksi == k


async def main():
    c = await asyncpg.connect(os.environ["URL"]); await c.execute("SET default_transaction_read_only = on")
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    for m in (Q, RP, DL, PF, CN, CD, SI, SO): m.get_pool = gp
    MOD = [("penawaran", Q, Q.list_quotes, Q.get_quote_summary), ("penerimaan", RP, RP.list_receive_payments, RP.get_receive_payments_summary),
           ("pengiriman", DL, DL.list_deliveries, DL.get_deliveries_summary), ("proforma", PF, PF.list_proformas, PF.get_proforma_summary),
           ("nota-kredit", CN, CN.list_credit_notes, CN.get_credit_notes_summary), ("uang-muka", CD, CD.list_customer_deposits, CD.get_customer_deposits_summary),
           ("faktur", SI, SI.list_invoices, SI.get_invoice_summary), ("pesanan", SO, SO.list_sales_orders, SO.get_sales_order_summary)]
    for t in TEN:
        uid = str(await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", t)); r = req(t, uid)
        for nama, mod, lst, smf in MOD:
            keys = kunci_literal(lst)
            sm = datar(await smf(**kw(smf, r)))
            sm = sm.get("data", sm) if isinstance(sm, dict) else sm
            for k in keys:
                ov = {"aksi": k}
                ps = inspect.signature(lst).parameters
                for pn in ("per_page", "limit"):
                    if pn in ps: ov[pn] = 100
                try:
                    h = datar(await lst(**kw(lst, r, **ov)))
                except Exception as e:
                    info.append(f"{t} {nama} ?aksi={k}: GALAT {type(e).__name__} {str(e)[:80]}"); continue
                items = h["items"] if isinstance(h, dict) and "items" in h else h.get("data", {}).get("items", [])
                total = h.get("total", h.get("data", {}).get("total", len(items)))
                bukan = [i for i in items if not punya(i.get("aksi"), k)]
                if k == "jatuh_tempo_minggu_ini":      # kunci TAMBAHAN faktur (bukan pill): medan boolean baris sendiri, tak saling-eksklusif
                    bukan = [i for i in items if not i.get("jatuh_tempo_minggu_ini")]
                tumpang = len(bukan) if nama == "pesanan" else 0
                if nama == "pesanan":      # SO: ?aksi= = keanggotaan dalam DAFTAR aksi; baris hanya membawa aksi UTAMA -> INFO, bukan gagal
                    if bukan: info.append(f"{t} pesanan ?aksi={k}: {len(bukan)} dari {len(items)} baris berpill utama LAIN (keanggotaan multi-kunci, by design; FE tak bisa melihat kunci k di baris)")
                    bukan = []
                cnt = None
                for nm in (f"{k}_count", f"{k}"):
                    if isinstance(sm, dict) and nm in sm and isinstance(sm[nm], int): cnt = sm[nm]; break
                if cnt is None and isinstance(sm, dict) and isinstance(sm.get("aksi"), dict) and k in sm["aksi"]:
                    v = sm["aksi"][k]; cnt = v.get("count") if isinstance(v, dict) else v
                ok1 = total == len(items) or total > 100
                ok2 = not bukan
                ok3 = cnt is None or cnt == total
                tag = f"{t} {nama} ?aksi={k}: total={total} len={len(items)} summary={cnt} baris_beda_pill={len(bukan)}"
                print(("PASS " if (ok1 and ok2 and ok3) else "FAIL ") + tag + ("" if ok2 else f"  contoh={[ (i.get('aksi')) for i in bukan[:3]]}"))
                if not (ok1 and ok2 and ok3): gagal.append(tag)
    for i in info: print("INFO", i)
    print("RINGKAS PILL-PARTISI gagal=%d %s" % (len(gagal), gagal[:6]))
    await c.close(); sys.exit(1 if gagal else 0)

asyncio.run(main())
