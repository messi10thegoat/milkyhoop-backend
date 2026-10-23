"""UJI NYATA saring umur_* Faktur Pembelian (KONTRAK-dashboard-T2 §5, 23 Okt 2026). BACA-SAJA (transaksi READ ONLY), grapgrap + kaos, pada
beberapa "hari ini" (tanggal bisnis asli + tanggal suntik +40/+70/+100 hari) supaya SEMUA bucket terisi.
  (a) PASANGAN SQL<->Python: id ?aksi=umur_k (SQL) == baris dengan TD.umur_tagihan == umur_k (Python, dari medan baris), tiap k
  (b) summary umur_k_count/_value == baris (nilai = outstanding_amount)
  (c) invarian: Σ umur_* == tagihan_terbuka (count & value); umur_1_30+…+umur_90_plus == lewat_jatuh_tempo (count & value)
  (d) Σ umur_*_value + kredit tak teralokasi (baris compute_ap_outstanding tanpa tagihan + baris bersisa negatif) == Σ compute_ap_outstanding
      (rumus aging dashboard T2: bucket per baris compute_ap_outstanding sisa > 0 menurut jatuh_tempo.kelas_umur(due ?? issue))
      dan bucket dari compute_ap_outstanding == summary per kunci (count & value)
  (e) BUKAN pill: aksi baris ⊆ AKSI_TAGIHAN ∪ {None} (tak ada umur_* di baris), ?aksi=umur_* tak mengubah aksi baris
MERAH=1 : SQL umur_1_30 disabotase (geser satu hari: 0..29 alih-alih 1..30) -> (a)/(b) WAJIB gagal (kaos: d1_30 terisi pada h0 dan
          tagihan bertelat 0 hari ikut tersaring salah pada tanggal suntik)."""
import asyncio, os, sys
from datetime import date, timedelta
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg

TEN = ["grapgrap-manado", "kaos-biru-konveksi"]
MERAH = os.environ.get("MERAH") == "1"
gagal = []


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


def D(v): return Decimal(str(v if v is not None else 0))


def tgl(v): return date.fromisoformat(str(v)[:10]) if v else None


async def main():
    from app.services import bills_service as BS
    from app.services import tagihan_daftar as TD
    from app.services import jatuh_tempo as JT
    from app.utils.tanggal_tenant import tanggal_dokumen as asli
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    tx = c.transaction(readonly=True); await tx.start()
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    svc = BS.BillsService(P())
    if MERAH:  # sabotase SQL bucket 1_30 (0..29) -> SQL != Python
        _su = JT.sql_umur
        def rusak(k, eff, p):
            return f"({eff} <= {p}::date AND {eff} >= {p}::date - 29)" if k == "umur_1_30" else _su(k, eff, p)
        TD.sql_umur = rusak
    try:
        for t in TEN:
            h0 = await asli(c, t)
            for H in (h0, h0 + timedelta(days=40), h0 + timedelta(days=70), h0 + timedelta(days=100), h0 + timedelta(days=30)):
                async def palsu(conn, tid, _H=H): return _H
                BS.tanggal_dokumen = palsu
                L = f"{t}@{H}"

                async def semua(**kw):
                    rows, skip = [], 0
                    while True:
                        r = await svc.list_bills(t, skip=skip, limit=100, status="all", **kw)
                        rows += r["items"]; skip += 100
                        if not r["has_more"]: return rows
                rows = await semua()
                py = {}
                for x in rows:
                    k = TD.umur_tagihan(x["status"], tgl(x["due_date"]), tgl(x["issue_date"]), H)
                    if k: py.setdefault(k, []).append(x)
                cek(all(x["aksi"] is None or TD.AKSI_USANG.get(x["aksi"], x["aksi"]) in TD.AKSI_TAGIHAN for x in rows),
                    f"{L} (e) aksi baris tetap partisi pill (tanpa umur_*)")
                sm = (await svc.get_summary(t, "current_year"))["data"]
                tot_n, tot_v = 0, Decimal(0)
                for k in TD.SARING_UMUR:
                    sql_rows = await semua(aksi=k)
                    ids = {x["id"] for x in sql_rows}
                    harap = {x["id"] for x in py.get(k, [])}
                    cek(ids == harap, f"{L} (a) ?aksi={k} SQL == Python", f"sql {len(ids)} py {len(harap)}")
                    cek(all(TD.AKSI_USANG.get(x["aksi"], x["aksi"]) in TD.AKSI_TAGIHAN + (None,) for x in sql_rows),
                        f"{L} (e) ?aksi={k} tak mengubah aksi baris")
                    nilai = sum((D(x["outstanding_amount"]) for x in py.get(k, [])), Decimal(0))
                    cek(sm[f"{k}_count"] == len(harap) and abs(D(sm[f"{k}_value"]) - nilai) < Decimal("0.01"),
                        f"{L} (b) summary {k}", f"{sm[f'{k}_count']}/{sm[f'{k}_value']} vs {len(harap)}/{nilai}")
                    tot_n += sm[f"{k}_count"]; tot_v += D(sm[f"{k}_value"])
                cek(tot_n == sm["tagihan_terbuka_count"] and abs(tot_v - D(sm["tagihan_terbuka_value"])) < Decimal("0.01"),
                    f"{L} (c) Σ umur_* == tagihan_terbuka", f"{tot_n}/{tot_v} vs {sm['tagihan_terbuka_count']}/{sm['tagihan_terbuka_value']}")
                lw = [k for k in TD.SARING_UMUR if k != "umur_current"]
                cek(sum(sm[f"{k}_count"] for k in lw) == sm["lewat_jatuh_tempo_count"]
                    and abs(sum(D(sm[f"{k}_value"]) for k in lw) - D(sm["lewat_jatuh_tempo_value"])) < Decimal("0.01"),
                    f"{L} (c) umur_1_30..90_plus == lewat_jatuh_tempo", f"{sum(sm[f'{k}_count'] for k in lw)} vs {sm['lewat_jatuh_tempo_count']}")
                # (d) rumus dashboard: baris compute_ap_outstanding (sisa > 0 per tagihan) di-bucket jatuh_tempo.kelas_umur(due ?? issue)
                ap = await c.fetch("""SELECT o.bill_id, o.outstanding, b.due_date, b.issue_date FROM compute_ap_outstanding($1) o
                                      LEFT JOIN bills b ON b.id = o.bill_id AND b.tenant_id = $1""", t)
                total = sum((D(r["outstanding"]) for r in ap), Decimal(0))
                kredit = sum((D(r["outstanding"]) for r in ap if r["bill_id"] is None or D(r["outstanding"]) <= 0), Decimal(0))
                bk = {}
                for r in ap:
                    if r["bill_id"] is not None and D(r["outstanding"]) > 0:
                        k = JT.kelas_umur(r["due_date"] or r["issue_date"], H)
                        n, v = bk.get(k, (0, Decimal(0)))
                        bk[k] = (n + 1, v + D(r["outstanding"]))
                cek(abs(tot_v + kredit - total) < Decimal("0.01"), f"{L} (d) Σ umur_*_value + kredit tak teralokasi == Σ compute_ap_outstanding",
                    f"{tot_v} + {kredit} vs {total}")
                cek(all(bk.get(k, (0, Decimal(0)))[0] == sm[f"{k}_count"] and abs(bk.get(k, (0, Decimal(0)))[1] - D(sm[f"{k}_value"])) < Decimal("0.01")
                        for k in TD.SARING_UMUR), f"{L} (d) bucket compute_ap_outstanding (rumus dashboard) == summary per kunci",
                    str({k: (sm[f'{k}_count'], sm[f'{k}_value']) for k in TD.SARING_UMUR}))
                print(f"BUCKET {L} " + " ".join(f"{JT.UMUR_KE_BUCKET[k]}={sm[f'{k}_count']}/{sm[f'{k}_value']}" for k in TD.SARING_UMUR), flush=True)
    finally:
        await tx.rollback(); await c.close()
    print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)

asyncio.run(main())
