"""Harness NYATA pill AKSI Faktur & Proforma (14 Okt 2026): handler asli (list_invoices, get_invoice_summary,
list_proformas, get_proforma_summary) di atas DB sungguhan, sesi READ ONLY (default_transaction_read_only=on).
Harapan dihitung INDEPENDEN (SQL compute_ar_outstanding + aritmetika tanggal polos, tanpa mengimpor aturan aplikasi)
lalu dibandingkan dengan ?aksi=, aksi per baris, dan ringkasan -- di tanggal bisnis NYATA dan 12 tanggal geser.
Jalankan: URL=<postgres url> PYTHONPATH=/repo:/repo/backend/api_gateway/app:... python uji-nyata-aksi-faktur-proforma.py"""
import asyncio, inspect, os
from datetime import timedelta
from decimal import Decimal
import asyncpg
os.environ.setdefault("OPENAI_API_KEY", "boneka")
from starlette.requests import Request
from backend.api_gateway.app.routers import sales_invoices as SI, proformas as PR
from backend.api_gateway.app.services.proforma_terbayar import terbayar_proforma

TENANTS = ("kaos-biru-konveksi", "grapgrap-manado")
GESER = (0, 1, 2, 3, 4, 7, -1, -3, -10, 30, 60, 120)
KUNCI_F = ("perlu_diterbitkan", "belum_lunas", "segera_tempo", "jatuh_tempo")
KUNCI_P = ("perlu_diterbitkan", "menunggu_bayar", "segera_tempo", "lewat_tempo")
gagal = []


def cek(nama, ok, rinci=""):
    if not ok:
        gagal.append(nama)
        print("  GAGAL", nama, rinci)


def req(tid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": tid, "user_id": None}
    return r


def argumen(fn, **kw):
    out = {}
    for n, p in inspect.signature(fn).parameters.items():
        if n == "request":
            continue
        d = p.default
        out[n] = d.default if hasattr(d, "default") and not isinstance(d, (str, int, float, bool)) else d
    out.update(kw)
    return out


async def semua(fn, tid, **kw):
    items, skip = [], 0
    while True:
        r = await fn(req(tid), **argumen(fn, skip=skip, limit=100, **kw))
        items += r["items"]
        if not r["has_more"]:
            return items
        skip += 100


async def main():
    conn = await asyncpg.connect(os.environ["URL"], server_settings={"default_transaction_read_only": "on"})

    class CM:
        async def __aenter__(s): return conn
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    SI.get_pool = gp
    PR.get_pool = gp
    total = 0
    for T in TENANTS:
        hari0 = await conn.fetchval("""SELECT (now() at time zone timezone)::date FROM "Tenant" WHERE id=$1""", T)
        # --- harapan independen
        fak = await conn.fetch("""SELECT si.id, si.status, si.due_date, si.invoice_date, si.total_amount, ar.outstanding
                                  FROM sales_invoices si LEFT JOIN compute_ar_outstanding($1) ar ON ar.invoice_id = si.id
                                  WHERE si.tenant_id = $1""", T)
        pro = await conn.fetch("""SELECT p.id, p.status, p.amount, p.due_date, p.sales_order_id, so.status so_status
                                  FROM proformas p LEFT JOIN sales_orders so ON so.id=p.sales_order_id AND so.tenant_id=p.tenant_id
                                  WHERE p.tenant_id=$1""", T)
        tb = await terbayar_proforma(conn, T, [r["sales_order_id"] for r in pro])
        print(f"== {T} faktur={len(fak)} proforma={len(pro)} hari_nyata={hari0}")
        for g in GESER:
            hari = hari0 + timedelta(days=g)

            async def th(c, t, _h=hari): return _h
            SI.tanggal_dokumen = th
            PR.tanggal_dokumen = th
            # faktur
            exp, nilai = {}, {k: Decimal(0) for k in KUNCI_F}
            for r in fak:
                k = None
                if r["status"] == "draft":
                    k, v = "perlu_diterbitkan", r["total_amount"]
                elif r["status"] != "void" and r["outstanding"] is not None and r["outstanding"] > 0:
                    eff = r["due_date"] or r["invoice_date"]
                    k = "jatuh_tempo" if eff < hari else ("segera_tempo" if eff <= hari + timedelta(days=3) else "belum_lunas")
                    v = r["outstanding"]
                if k:
                    exp[r["id"]] = k
                    nilai[k] += Decimal(str(v))
            daftar = await semua(SI.list_invoices, T)
            cek(f"{T} g={g} faktur baris aksi", {i["id"]: i["aksi"] for i in daftar if i["aksi"]} ==
                {str(i): k for i, k in exp.items()},
                (len(daftar),))
            for k in KUNCI_F:
                ids = {i["id"] for i in await semua(SI.list_invoices, T, aksi=k)}
                cek(f"{T} g={g} faktur ?aksi={k}", ids == {str(i) for i, x in exp.items() if x == k},
                    (len(ids), sum(1 for x in exp.values() if x == k)))
            sm = (await SI.get_invoice_summary(req(T)))["data"]
            for k in KUNCI_F:
                n = sum(1 for x in exp.values() if x == k)
                cek(f"{T} g={g} faktur ringkasan {k}_count", sm[f"{k}_count"] == n, (sm[f"{k}_count"], n))
                cek(f"{T} g={g} faktur ringkasan {k}_value", abs(Decimal(str(sm[f"{k}_value"])) - nilai[k]) < Decimal("0.01"),
                    (sm[f"{k}_value"], nilai[k]))
            ids_bl = {i["id"] for i in await semua(SI.list_invoices, T, tugas="belum_lunas")}
            cek(f"{T} g={g} faktur ?tugas=belum_lunas == gabungan 3 kunci terbit",
                ids_bl == {str(i) for i, x in exp.items() if x != "perlu_diterbitkan"})
            ids_telat = {i["id"] for i in await semua(SI.list_invoices, T, tugas="telat")}
            jt = {str(i) for i, x in exp.items() if x == "jatuh_tempo"}
            print(f"   g={g:>4} hari={hari} faktur: " + " ".join(f"{k}={sum(1 for x in exp.values() if x == k)}" for k in KUNCI_F)
                  + f" | telat_lama={len(ids_telat)} jatuh_tempo={len(jt)}" + ("" if ids_telat == jt else "  (SELISIH due kosong)"))
            # proforma
            expp, nilaip = {}, {k: Decimal(0) for k in KUNCI_P}
            for r in pro:
                k = None
                if r["status"] == "draft":
                    k, v = "perlu_diterbitkan", Decimal(str(r["amount"]))
                elif r["status"] == "issued" and r["so_status"] != "cancelled":
                    paid = Decimal(str(tb[r["id"]]["paid"])) if r["id"] in tb else Decimal(0)
                    nom = Decimal(str(r["amount"]))
                    if paid + Decimal("0.005") < nom:
                        d = r["due_date"]
                        k = ("menunggu_bayar" if d is None else "lewat_tempo" if d < hari
                             else "segera_tempo" if d <= hari + timedelta(days=3) else "menunggu_bayar")
                        v = nom - paid
                if k:
                    expp[r["id"]] = k
                    nilaip[k] += v
            dp = await semua(PR.list_proformas, T)
            cek(f"{T} g={g} proforma baris aksi", {i["id"]: i["aksi"] for i in dp if i["aksi"]} ==
                {str(i): k for i, k in expp.items()}, (len(dp),))
            for k in KUNCI_P:
                ids = {i["id"] for i in await semua(PR.list_proformas, T, aksi=k)}
                cek(f"{T} g={g} proforma ?aksi={k}", ids == {str(i) for i, x in expp.items() if x == k},
                    (len(ids), sum(1 for x in expp.values() if x == k)))
            sp = (await PR.get_proforma_summary(req(T)))["data"]
            for k in KUNCI_P:
                n = sum(1 for x in expp.values() if x == k)
                cek(f"{T} g={g} proforma ringkasan {k}_count", sp[f"{k}_count"] == n, (sp[f"{k}_count"], n))
                cek(f"{T} g={g} proforma ringkasan {k}_value", abs(Decimal(str(sp[f"{k}_value"])) - nilaip[k]) < Decimal("0.01"),
                    (sp[f"{k}_value"], nilaip[k]))
            print(f"   g={g:>4}            proforma: " + " ".join(f"{k}={sum(1 for x in expp.values() if x == k)}" for k in KUNCI_P))
            total += 1
    await conn.close()
    print(f"RINGKAS: {total} kombinasi tenant x tanggal, GAGAL={len(gagal)}")
    raise SystemExit(1 if gagal else 0)

asyncio.run(main())
