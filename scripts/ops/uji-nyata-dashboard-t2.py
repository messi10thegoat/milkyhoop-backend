"""UJI NYATA dashboard T2 (23 Okt 2026, KONTRAK-dashboard-T2 + syarat MASTER): BACA-SAJA, kaos + grapgrap.
Mode:
  dump                  cetak `HASIL <json>` ringkasan v2 untuk kunci periode LAMA (day|week|month|year) dari pohon yang di-mount
  banding <lama> <baru> medan v1 byte-identik: tiap kunci (rekursif) di LAMA ada & SAMA di BARU; BARU hanya MENAMBAH kunci
  baru [merah_umur|merah_kredit|merah_tren]
                        (1) comparison: current == DA.laba_rugi/arus_kas_neto(range), pembanding == fungsi yang sama atas rentangnya
                        (2) trend_12m: tiap bulan (12) == DA.laba_rugi(bulan penuh)
                        (3) aging: Σ bucket + unapplied_credits == Σ compute_*_outstanding == kartu lama; d1_30..d90_plus == pill
                            jatuh_tempo faktur; current == segera_tempo + belum_lunas; count bucket == total daftar ?aksi=umur_* ==
                            summary umur_*_count
  Kontrol merah (WAJIB FAIL): merah_umur (bucket 1–30 dikirim ke 31–60), merah_kredit (unapplied_credits dibuang),
  merah_tren (tren tanpa filter jurnal efektif)."""
import asyncio, inspect, json, os, sys
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
from datetime import date
from decimal import Decimal

TENANT = ("kaos-biru-konveksi", "grapgrap-manado")
PERIODE_LAMA = ("day", "week", "month", "year")
gagal = []


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:300]}"))
    if not ok:
        gagal.append(nama)


def banding(p_lama, p_baru):
    lama, baru = (json.load(open(p)) for p in (p_lama, p_baru))

    def jalan(a, b, jalur):
        if isinstance(a, dict):
            if not isinstance(b, dict):
                return [f"{jalur}: bukan objek di BARU"]
            out = []
            for k in a:
                if k == "as_of":
                    continue
                out += [f"{jalur}.{k}: hilang di BARU"] if k not in b else jalan(a[k], b[k], f"{jalur}.{k}")
            return out
        if isinstance(a, list):
            if not isinstance(b, list) or len(a) != len(b):
                return [f"{jalur}: panjang daftar beda"]
            return [x for i, (u, v) in enumerate(zip(a, b)) for x in jalan(u, v, f"{jalur}[{i}]")]
        return [] if a == b else [f"{jalur}: lama={a!r} baru={b!r}"]

    for kunci in sorted(lama):
        beda = jalan(lama[kunci], baru.get(kunci, {}), kunci)
        cek(f"v1 byte-identik {kunci}", not beda, beda[:5])
        tambah = sorted(set(baru.get(kunci, {})) - set(lama[kunci]))
        print(f"INFO {kunci} kunci baru tingkat atas: {tambah}")
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


async def main(mode, merah):
    import asyncpg
    from fastapi.params import Query as QP
    from starlette.requests import Request
    from app.routers import sales_invoices as RSI
    from app.services import dashboard_v2 as DV
    from app.utils.tanggal_tenant import tanggal_dokumen, zona_tenant

    c = await asyncpg.connect(os.environ["URL"]); await c.execute("SET default_transaction_read_only = on")

    class Pool:
        def acquire(s):
            class CM:
                async def __aenter__(x): return c
                async def __aexit__(x, *a): return False
            return CM()
        async def release(s, x): return None
    async def gp(): return Pool()
    if hasattr(RSI, "get_pool"):
        RSI.get_pool = gp
    try:
        import app.services.db_pool as DBP
        DBP.get_db_pool = gp
    except Exception:
        pass

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

    def total_daftar(x):
        x = x.model_dump() if hasattr(x, "model_dump") else x
        for k in ("total", "total_count"):
            if isinstance(x, dict) and k in x: return int(x[k])
        d = x.get("data") if isinstance(x, dict) else None
        if isinstance(d, dict):
            for k in ("total", "total_count"):
                if k in d: return int(d[k])
        raise ValueError(f"bentuk daftar tak dikenal: {list(x)[:8]}")

    async def ringkasan(t, per):
        zona = await zona_tenant(c, t)
        hari = await tanggal_dokumen(c, t)
        return json.loads(json.dumps(await DV.ringkasan(c, t, per, hari, zona.key, "x"), default=str)), hari

    if mode == "latensi":  # biaya hitung /v2/summary TANPA cache (dv2.ringkasan), 30x per tenant, periode month (bawaan FE)
        import time
        for t in TENANT:
            await ringkasan(t, "month")  # pemanasan
            w = []
            for _ in range(30):
                t0 = time.perf_counter(); await ringkasan(t, "month"); w.append((time.perf_counter() - t0) * 1000)
            w.sort()
            print(f"LATENSI {t} p50={w[15]:.1f} p95={w[28]:.1f} max={w[-1]:.1f} ms")
        await c.close()
        return
    if mode == "dump":
        out = {}
        for t in TENANT:
            for per in PERIODE_LAMA:
                out[f"{t}/{per}"], _ = await ringkasan(t, per)
        await c.close()
        print("HASIL " + json.dumps(out, sort_keys=True))
        return

    from app.services import dashboard_angka as DA
    from app.services import jatuh_tempo as JT
    if merah == "merah_umur":
        asli = JT.kelas_umur
        JT.kelas_umur = lambda eff, h: (lambda k: "umur_31_60" if k == "umur_1_30" else k)(asli(eff, h))
    if merah == "merah_kredit":
        asli_blok = DV.umur_blok
        DV.umur_blok = lambda *a, **k: {**asli_blok(*a, **k), "unapplied_credits": "0.00"}
    if merah == "merah_tren":
        DA.SQL_LABA_RUGI_BULANAN = DA.SQL_LABA_RUGI_BULANAN.replace("AND is_effective_journal(je.id)", "")

    D = lambda v: Decimal(str(v)) if v is not None else None
    for t in TENANT:
        uid = str(await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", t))
        r = req(t, uid)
        for per in ("bulan_ini", "bulan_lalu", "kuartal_ini", "tahun_ini", "month"):
            s, hari = await ringkasan(t, per)
            cmp = s["comparison"]
            for kunci in ("range", "prev_period", "prev_year"):
                a, b = date.fromisoformat(cmp[kunci]["start"]), date.fromisoformat(cmp[kunci]["end"])
                lr = await DA.laba_rugi(c, t, a, b)
                kas = await DA.arus_kas_neto(c, t, a, b)
                harap = {"revenue": lr["pendapatan"], "expense": lr["beban"], "profit": lr["laba"], "net_cash": kas}
                for m, v in harap.items():
                    x = cmp["metrics"][m]
                    nilai = D(x["current"]) if kunci == "range" else (D(x[kunci]["value"]) if x[kunci] else None)
                    if kunci != "range" and nilai is None:
                        cek(f"{t} {per} {kunci}.{m}: null hanya bila sebelum jurnal pertama", b < await DA.jurnal_efektif_pertama(c, t), (a, b))
                        continue
                    cek(f"{t} {per} {kunci}.{m} == DA ({nilai})", nilai == Decimal(v).quantize(Decimal("0.01")), (nilai, v))
            if s["period_end"] <= str(hari) or per in ("bulan_ini", "month"):
                cek(f"{t} {per} comparison.current.revenue == pnl.revenue (jurnal masa depan 0)",
                    cmp["metrics"]["revenue"]["current"] == s["pnl"]["revenue"], (cmp["metrics"]["revenue"]["current"], s["pnl"]["revenue"]))
            cek(f"{t} {per} needs_action hanya net_cash", all("needs_action" not in cmp["metrics"][k] for k in ("revenue", "expense", "profit")))
        s, hari = await ringkasan(t, "month")
        for p in s["trend_12m"]["points"]:
            th, bl = map(int, p["month"].split("-"))
            a = date(th, bl, 1)
            b = DV._akhir_bulan(a)
            lr = await DA.laba_rugi(c, t, a, b)
            if p["revenue"] is None:
                cek(f"{t} tren {p['month']} null hanya sebelum jurnal pertama", b < await DA.jurnal_efektif_pertama(c, t))
                continue
            cek(f"{t} tren {p['month']} == DA.laba_rugi ({p['revenue']}/{p['expense']})",
                (D(p["revenue"]), D(p["expense"])) == (lr["pendapatan"].quantize(Decimal("0.01")), lr["beban"].quantize(Decimal("0.01"))),
                (p, lr["pendapatan"], lr["beban"]))
        ag = s["aging"]
        for sisi, fn in (("receivables", "compute_ar_outstanding"), ("payables", "compute_ap_outstanding")):
            b = ag[sisi]
            jml = sum(D(x["amount"]) for x in b["buckets"]) + D(b["unapplied_credits"])
            db = Decimal(str(await c.fetchval(f"SELECT COALESCE(SUM(outstanding), 0) FROM {fn}($1)", t)))
            cek(f"{t} aging.{sisi}: Σ bucket + kredit ({jml}) == Σ {fn} ({db}) == kartu lama",
                jml == db == D(b["total"]) == D(s[sisi]["total"]), (jml, db, b["total"], s[sisi]["total"]))
        sm = data(await RSI.get_invoice_summary(**kw(RSI.get_invoice_summary, r)))
        bk = {x["key"]: x for x in ag["receivables"]["buckets"]}
        telat = ("d1_30", "d31_60", "d61_90", "d90_plus")
        cek(f"{t} aging lewat (Σ d1_30..d90_plus) == pill jatuh_tempo ({sm['jatuh_tempo_count']})",
            sum(bk[k]["count"] for k in telat) == sm["jatuh_tempo_count"]
            and sum(D(bk[k]["amount"]) for k in telat) == D(sm["jatuh_tempo_value"]).quantize(Decimal("0.01")),
            ([(k, bk[k]["count"], bk[k]["amount"]) for k in telat], sm["jatuh_tempo_count"], sm["jatuh_tempo_value"]))
        cek(f"{t} aging current == segera_tempo + belum_lunas",
            bk["current"]["count"] == sm["segera_tempo_count"] + sm["belum_lunas_count"]
            and D(bk["current"]["amount"]) == (D(sm["segera_tempo_value"]) + D(sm["belum_lunas_value"])).quantize(Decimal("0.01")),
            (bk["current"], sm["segera_tempo_count"], sm["belum_lunas_count"]))
        for kunci, bucket in JT.UMUR_KE_BUCKET.items():
            n = total_daftar(await RSI.list_invoices(**kw(RSI.list_invoices, r, aksi=kunci)))
            cek(f"{t} {bucket}: count ({bk[bucket]['count']}) == daftar ?aksi={kunci} ({n}) == summary {kunci}_count ({sm[f'{kunci}_count']})",
                bk[bucket]["count"] == n == sm[f"{kunci}_count"]
                and D(bk[bucket]["amount"]) == D(sm[f"{kunci}_value"]).quantize(Decimal("0.01")),
                (bk[bucket], n, sm[f"{kunci}_count"], sm[f"{kunci}_value"]))
        print(f"INFO {t} aging AR {[(x['key'], x['count'], x['amount']) for x in ag['receivables']['buckets']]} kredit {ag['receivables']['unapplied_credits']}")
        print(f"INFO {t} aging AP {[(x['key'], x['count'], x['amount']) for x in ag['payables']['buckets']]} kredit {ag['payables']['unapplied_credits']}")
    await c.close()
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


if __name__ == "__main__":
    if sys.argv[1:2] == ["banding"]:
        banding(sys.argv[2], sys.argv[3])
    else:
        asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "baru", sys.argv[2] if len(sys.argv) > 2 else ""))
