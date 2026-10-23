"""UJI NYATA paritas pengingat dashboard (22 Okt 2026, KONTRAK-dashboard-pengingat §4): BACA-SAJA. Mode: baru [merah]
Untuk kaos + grapgrap, TIAP pengingat registri (services/pengingat.PENGINGAT, termasuk yang count 0):
  count == /summary daftar modul `{kunci}_count` (bila summary memuatnya) == `total` daftar ?aksi={kunci};
  amount == /summary `{kunci}_value` (arah uang, 2dp).
Juga: alias faktur ?aksi=jatuh_tempo_minggu_ini == segera_tempo. Butuh kolom V450 (accounting_settings.pengingat_segera_hari).
Mode `merah`: ambang pengingat dipaksa 3 sementara daftar memakai N tenant -> segera_tempo HARUS beda (kontrol merah alat ukur)."""
import asyncio, inspect, os, sys
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
from decimal import Decimal
import asyncpg
from fastapi.params import Query as QP
from starlette.requests import Request

from app.routers import bills as RB, proformas as RP, purchase_orders as RPO, sales_invoices as RSI, sales_orders as RSO
from app.services import pengingat as P
from app.utils.tanggal_tenant import tanggal_dokumen

gagal = []
MERAH = len(sys.argv) > 1 and sys.argv[1] == "merah"
MERAH_TAGIHAN = len(sys.argv) > 1 and sys.argv[1] == "merah_tagihan"  # kontrol merah bills segera_tempo: count +1 -> WAJIB FAIL
MERAH_DP = len(sys.argv) > 1 and sys.argv[1] == "merah_dp"  # kontrol merah sum_in pecah: KUNCI_BELUM_DITAGIH dikosongkan -> WAJIB FAIL


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:240]}"))
    if not ok: gagal.append(nama)


def req(t, uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": t, "user_id": uid, "role": "OWNER"}
    return r


def kw(fn, r, **ov):
    o = {}
    for n, p in inspect.signature(fn).parameters.items():
        if n == "request": o[n] = r
        elif n in ov: o[n] = ov[n]
        elif isinstance(p.default, QP): o[n] = p.default.default
        elif p.default is not inspect._empty: o[n] = p.default
    return o


def data(x):
    if hasattr(x, "model_dump"): x = x.model_dump()
    return x.get("data", x) if isinstance(x, dict) else x


DAFTAR = {"sales_invoices": (RSI.get_invoice_summary, RSI.list_invoices), "proformas": (RP.get_proforma_summary, RP.list_proformas),
          "sales_orders": (RSO.get_sales_order_summary, RSO.list_sales_orders), "bills": (RB.get_bills_summary, RB.list_bills),
          "purchase_orders": (RPO.get_purchase_orders_summary, RPO.list_purchase_orders)}


def total_daftar(x):
    x = x.model_dump() if hasattr(x, "model_dump") else x
    for k in ("total", "total_count"):
        if isinstance(x, dict) and k in x: return int(x[k])
    d = x.get("data") if isinstance(x, dict) else None
    if isinstance(d, dict):
        for k in ("total", "total_count"):
            if k in d: return int(d[k])
    raise ValueError(f"bentuk daftar tak dikenal: {list(x)[:8]}")


async def main():
    c = await asyncpg.connect(os.environ["URL"]); await c.execute("SET default_transaction_read_only = on")
    class Pool:
        def acquire(s):
            class CM:
                async def __aenter__(x): return c
                async def __aexit__(x, *a): return False
            return CM()
        async def release(s, x): return None
    async def gp(): return Pool()
    for m in (RSI, RP, RSO, RB, RPO):
        if hasattr(m, "get_pool"): m.get_pool = gp
    try:
        import app.services.db_pool as DBP
        DBP.get_db_pool = gp
    except Exception:
        pass
    if MERAH:  # hanya penghitung FAKTUR pengingat memakai ambang 3; daftar tetap N tenant -> segera_tempo HARUS beda
        from app.services import faktur_aksi as FA
        async def faktur3(conn, tid, hari_ini, ambang):
            return await FA.ringkas_aksi(conn, tid, hari_ini, 3)
        P.PENGHITUNG = {**P.PENGHITUNG, "sales_invoices": faktur3}
    if MERAH_DP:
        P.KUNCI_BELUM_DITAGIH = ()
    if MERAH_TAGIHAN:
        tagihan_asli = P.PENGHITUNG["bills"]
        async def tagihan_rusak(conn, tid, hari_ini, ambang):
            o = await tagihan_asli(conn, tid, hari_ini, ambang)
            s = o.get("segera_tempo") or {"count": 0, "amount": Decimal("0")}
            return {**o, "segera_tempo": {"count": s["count"] + 1, "amount": s["amount"]}}
        P.PENGHITUNG = {**P.PENGHITUNG, "bills": tagihan_rusak}
    for t in ("kaos-biru-konveksi", "grapgrap-manado"):
        uid = str(await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", t))
        r = req(t, uid)
        hari = await tanggal_dokumen(c, t)
        semua = {(x["modul"], x["kunci_aksi"]): x for x in await P.hitung(c, t, hari)}
        ringkas = {}
        for modul, kunci, label, arah, lane, izin in P.PENGINGAT:
            s_fn, l_fn = DAFTAR[modul]
            if modul not in ringkas:
                ringkas[modul] = data(await s_fn(**kw(s_fn, r)))
            sm = ringkas[modul]
            p = semua.get((modul, kunci)) or {"count": 0, "amount": None if arah == "non_uang" else "0.00"}
            if f"{kunci}_count" in sm:
                cek(f"{t} {modul}/{kunci}: count == summary ({p['count']})", p["count"] == int(sm[f"{kunci}_count"]), (p["count"], sm[f"{kunci}_count"]))
                if arah != "non_uang":
                    cek(f"{t} {modul}/{kunci}: amount == summary", Decimal(p["amount"]) == Decimal(str(sm[f"{kunci}_value"])).quantize(Decimal("0.01")),
                        (p["amount"], sm[f"{kunci}_value"]))
            else:
                print(f"INFO {t} {modul}/{kunci}: summary belum memuat {kunci}_count (prasyarat BACKEND)")
            n = total_daftar(await l_fn(**kw(l_fn, r, aksi=kunci)))
            cek(f"{t} {modul}/{kunci}: count == baris daftar ?aksi ({n})", p["count"] == n, (p["count"], n))
        # sum_in dipecah (MASTER 23 Okt): dp_belum_ditagih == summary SO perlu_minta_dp_value; ditagih == Σ summary {k}_value masuk lain
        rk = P.rangkum(list(semua.values()))
        nilai = lambda m, k: Decimal(str(ringkas[m].get(f"{k}_value") or 0)).quantize(Decimal("0.01"))
        dp = sum((nilai(m, k) for m, k, _l, arah, *_ in P.PENGINGAT if arah == "masuk" and (m, k) in (("sales_orders", "perlu_minta_dp"),)), Decimal("0"))
        tg = sum((nilai(m, k) for m, k, _l, arah, *_ in P.PENGINGAT if arah == "masuk" and (m, k) not in (("sales_orders", "perlu_minta_dp"),)), Decimal("0"))
        cek(f"{t} sum_in_dp_belum_ditagih == summary SO perlu_minta_dp ({dp})", Decimal(rk["sum_in_dp_belum_ditagih"]) == dp, (rk["sum_in_dp_belum_ditagih"], dp))
        cek(f"{t} sum_in_ditagih == Σ summary masuk lain ({tg})", Decimal(rk["sum_in_ditagih"]) == tg, (rk["sum_in_ditagih"], tg))
        cek(f"{t} sum_in == ditagih + dp_belum_ditagih", Decimal(rk["sum_in"]) == Decimal(rk["sum_in_ditagih"]) + Decimal(rk["sum_in_dp_belum_ditagih"]), rk)
        ko = sum((nilai(m, k) for m, k, _l, arah, *_ in P.PENGINGAT if arah == "keluar"), Decimal("0"))
        cek(f"{t} sum_out == Σ summary keluar (bills lewat + segera) ({ko})", Decimal(rk["sum_out"]) == ko, (rk["sum_out"], ko))
        print(f"INFO {t} sum_in={rk['sum_in']} ditagih={rk['sum_in_ditagih']} dp_belum_ditagih={rk['sum_in_dp_belum_ditagih']} sum_out={rk['sum_out']}")
        a = total_daftar(await RSI.list_invoices(**kw(RSI.list_invoices, r, aksi="jatuh_tempo_minggu_ini")))
        b = total_daftar(await RSI.list_invoices(**kw(RSI.list_invoices, r, aksi="segera_tempo")))
        cek(f"{t} faktur: alias jatuh_tempo_minggu_ini == segera_tempo ({b})", a == b, (a, b))
        cek(f"{t} faktur: summary alias == segera_tempo", ringkas["sales_invoices"]["jatuh_tempo_minggu_ini_count"] == ringkas["sales_invoices"]["segera_tempo_count"])
        print(f"INFO {t} ambang N = {await P.ambang_segera(c, t)}; pengingat aktif = {len(semua)}")
    await c.close()
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
