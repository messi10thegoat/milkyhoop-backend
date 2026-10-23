"""UJI NYATA kontrak daftar seragam Faktur Pembelian (22 Okt 2026). BACA-SAJA (transaksi READ ONLY), grapgrap + kaos, SEMUA tagihan,
pada beberapa "hari ini" (tanggal bisnis asli + tanggal suntik) supaya ketiga kunci aksi teruji.
  (a) PASANGAN SQL<->Python: id ?aksi=k (SQL TD.sql_aksi) == baris ber-aksi k (Python TD.aksi_tagihan), tiap k; partisi (kunci sah/None)
  (b) summary: <k>_count/_value == baris; tagihan_terbuka == baris terbuka == Σ baris tagihan compute_ap_outstanding (> 0)
  (c) detail v1 == v2 == baris (aksi, aksi_semua, position_text, outstanding_amount, jatuh_tempo_*, documents, doc_count)
  (d) bundel /bills/{id}/documents: documents_baris(groups) == documents baris; anchor position_text/paid/balance == baris
  (e) with_totals: sum_sisa == Σ outstanding_amount, sum_total == Σ amount bukan batal (tanpa filter DAN per ?aksi)
  (f) ?search=: id daftar == id summary (tagihan_terbuka_count dalam pencarian == baris terbuka hasil cari)
  (g) konsistensi: status overdue <=> jatuh_tempo_keadaan telat; Lunas/Batal/Draf <=> paid/void/draft
  (h) riwayat: tiap tagihan ada TAGIHAN_DIBUAT; id asing -> None (404)
  (i) segera_tempo (23 Okt): alias ?aksi=jatuh_tempo_minggu_ini == ?aksi=segera_tempo; summary jatuh_tempo_minggu_ini_* == segera_tempo_*;
      Σ sisa segera_tempo == agregat predikat pengingat (TD.sql_aksi_berparam + TD.sql_hutang, == pengingat._tagihan); ambang tenant
      DIPAKAI (ambang_segera disuntik 3 -> SQL == Python dgn 3, himpunan ⊆ N asli)
MERAH=1 : Python aksi_tagihan disabotase (lewat_jatuh_tempo -> None) -> (a) WAJIB gagal.
MERAH_AMBANG=1 : ambang tenant DIABAIKAN (SQL & Python memakai 7 tetap) -> (i) suntik N=3 WAJIB gagal pada tanggal pembeda."""
import asyncio, os, sys, uuid
from datetime import date, timedelta
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

TEN = ["grapgrap-manado", "kaos-biru-konveksi"]
MERAH = os.environ.get("MERAH") == "1"
gagal = []
KUNCI = ("aksi", "aksi_semua", "position_text", "outstanding_amount", "jatuh_tempo_terbuka", "jatuh_tempo_hari",
         "jatuh_tempo_keadaan", "jatuh_tempo_teks", "jatuh_tempo_minggu_ini", "documents", "doc_count")


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


def D(v): return Decimal(str(v if v is not None else 0))


def req(t):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": t, "user_id": None}; return r


async def main():
    from app.services import bills_service as BS
    from app.services import tagihan_daftar as TD
    from app.services import tagihan_riwayat as TR
    from app.routers import bills as BR
    from app.utils.tanggal_tenant import tanggal_dokumen as asli
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    tx = c.transaction(readonly=True); await tx.start()
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    BR.get_pool = gp
    svc = BS.BillsService(P())
    kan = lambda x: TD.AKSI_USANG.get(x["aksi"], x["aksi"])  # noqa: E731 -- aksi baris (nama lama selama transisi) -> kunci kanonik
    if os.environ.get("MERAH_AMBANG") == "1":
        _sb, _at, _jf = TD.sql_aksi_berparam, TD.aksi_tagihan, TD.jatuh_tempo_fakta
        TD.sql_aksi_berparam = lambda aksi, mulai, h, ambang=None: _sb(aksi, mulai, h, None if ambang is None else 7)
        TD.aksi_tagihan = lambda k, d, i, h, ambang: _at(k, d, i, h, 7)
        TD.jatuh_tempo_fakta = lambda k, d, h, ambang: _jf(k, d, h, 7)
    if MERAH:
        _a = TD.aksi_tagihan
        TD.aksi_tagihan = lambda *a, **k: (None if _a(*a, **k) == "lewat_jatuh_tempo" else _a(*a, **k))
    try:
        for t in TEN:
            h0 = await asli(c, t)
            # tanggal PEMBEDA: jatuh tempo efektif tagihan terbuka tertua - 5 hari -> di dalam N=7, di luar N=3 (uji (i) bermakna)
            d5 = await c.fetchval(f"""SELECT MIN({TD.sql_jt_efektif('b')}) - 5 FROM bills b {TD.join_rincian()}
                                      WHERE b.tenant_id = $1 AND {TD.sql_terbuka()}""", t)
            for H in (h0, date(2026, 9, 15), date(2026, 10, 1)) + ((d5,) if d5 else ()):
                async def palsu(conn, tid, _H=H): return _H
                BS.tanggal_dokumen = palsu
                L = f"{t}@{H}"

                async def semua(**kw):
                    rows, skip = [], 0
                    while True:
                        r = await svc.list_bills(t, skip=skip, limit=100, status="all", **kw)
                        rows += r["items"]; skip += 100
                        if not r["has_more"]: return rows, r
                rows, _ = await semua()
                ak = {}
                for x in rows:
                    ak.setdefault(kan(x), set()).add(x["id"])
                cek(set(ak) <= set(TD.AKSI_TAGIHAN) | {None}, f"{L} (a) partisi: kunci sah", str(sorted(map(str, ak))))
                sm = (await svc.get_summary(t, "current_year"))["data"]
                for k in TD.AKSI_TAGIHAN:
                    ids = {x["id"] for x in (await semua(aksi=k))[0]}
                    harap = ak.get(k, set())
                    cek(ids == harap, f"{L} (a) ?aksi={k} SQL == Python", f"sql {len(ids)} py {len(harap)}")
                    nilai = sum((D(x["amount"]) if k == "perlu_diposting" else D(x["outstanding_amount"])) for x in rows if kan(x) == k)
                    cek(sm[f"{k}_count"] == len(harap) and abs(D(sm[f"{k}_value"]) - nilai) < Decimal("0.01"),
                        f"{L} (b) summary {k}", f"{sm[f'{k}_count']}/{sm[f'{k}_value']} vs {len(harap)}/{nilai}")
                    _, rk = await semua(aksi=k, with_totals=True)
                    sisa_k = sum((D(x["outstanding_amount"]) for x in rows if kan(x) == k and x["outstanding_amount"] is not None), Decimal(0))
                    cek(D(rk["sum_sisa"]) == sisa_k, f"{L} (e) with_totals ?aksi={k} sum_sisa", f"{rk['sum_sisa']} vs {sisa_k}")
                # (i) segera_tempo: alias, summary alias, predikat pengingat (AP agregat), ambang tenant dipakai
                n_asli = await BS.ambang_segera(c, t)
                ids_seg = ak.get("segera_tempo", set())
                ids_alias = {x["id"] for x in (await semua(aksi="jatuh_tempo_minggu_ini"))[0]}
                cek(ids_alias == ids_seg and sm["jatuh_tempo_minggu_ini_count"] == sm["segera_tempo_count"]
                    and sm["jatuh_tempo_minggu_ini_value"] == sm["segera_tempo_value"], f"{L} (i) alias jatuh_tempo_minggu_ini == segera_tempo (N={n_asli})",
                    f"alias {len(ids_alias)} seg {len(ids_seg)} sm {sm['jatuh_tempo_minggu_ini_count']}/{sm['segera_tempo_count']}")
                _pr, _pa = TD.sql_aksi_berparam("segera_tempo", 2, H, n_asli)
                pg = await c.fetchrow(f"""SELECT COUNT(*) AS n, COALESCE(SUM({TD.sql_hutang('b')}), 0) AS v
                    FROM bills b {TD.join_rincian()} WHERE b.tenant_id = $1 AND {_pr}""", t, *_pa)
                cek(pg["n"] == sm["segera_tempo_count"] and abs(D(pg["v"]) - D(sm["segera_tempo_value"])) < Decimal("0.01"),
                    f"{L} (i) summary segera_tempo == agregat predikat pengingat", f"{pg['n']}/{pg['v']} vs {sm['segera_tempo_count']}/{sm['segera_tempo_value']}")
                _amb = BS.ambang_segera
                async def tiga(conn, tid): return 3
                BS.ambang_segera = tiga
                try:
                    r3, _ = await semua()
                    py3 = {x["id"] for x in r3 if kan(x) == "segera_tempo"}
                    sql3 = {x["id"] for x in (await semua(aksi="segera_tempo"))[0]}
                    harap3 = {x["id"] for x in rows if x["status"] in TD.TERBUKA and (x["due_date"] or x["issue_date"])
                              and H <= date.fromisoformat(str(x["due_date"] or x["issue_date"])[:10]) <= H + timedelta(days=3)}
                finally:
                    BS.ambang_segera = _amb
                cek(py3 == sql3 == harap3 and (py3 <= ids_seg or n_asli < 3), f"{L} (i) ambang tenant dipakai (suntik N=3)",
                    f"py {len(py3)} sql {len(sql3)} harap {len(harap3)} asli {len(ids_seg)}")
                terbuka = [x for x in rows if x["status"] in TD.TERBUKA]
                ap = D(await c.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ap_outstanding($1) WHERE bill_id IS NOT NULL AND outstanding > 0", t))
                v_baris = sum((D(x["outstanding_amount"]) for x in terbuka), Decimal(0))
                cek(sm["tagihan_terbuka_count"] == len(terbuka) and D(sm["tagihan_terbuka_value"]) == v_baris == ap,
                    f"{L} (b) payung tagihan_terbuka == baris == AP jurnal", f"{sm['tagihan_terbuka_count']}/{sm['tagihan_terbuka_value']} baris {len(terbuka)}/{v_baris} ap {ap}")
                _, rt = await semua(with_totals=True)
                st = sum((D(x["outstanding_amount"]) for x in rows if x["outstanding_amount"] is not None), Decimal(0))
                tt = sum((D(x["amount"]) for x in rows if x["status"] != "void"), Decimal(0))
                cek(D(rt["sum_sisa"]) == st and D(rt["sum_total"]) == tt, f"{L} (e) with_totals tanpa filter", f"{rt['sum_sisa']}/{rt['sum_total']} vs {st}/{tt}")
                beda_g = [x["invoice_number"] for x in rows if (x["status"] == "overdue") != (x["jatuh_tempo_keadaan"] == "telat")
                          or (x["position_text"] == "Lunas") != (x["status"] == "paid") or (x["position_text"] == "Batal") != (x["status"] == "void")
                          or (x["position_text"] == "Draf") != (x["status"] == "draft")]
                cek(not beda_g, f"{L} (g) keadaan/posisi konsisten", str(beda_g[:5]))
                if H != h0:
                    continue
                beda_c, beda_d, beda_h = [], [], []
                for x in rows:
                    d1 = await svc.get_bill(t, x["id"]); d2 = await svc.get_bill_v2(t, x["id"])
                    for k in KUNCI:
                        if not (d1.get(k) == d2.get(k) == x[k]):
                            beda_c.append((x["invoice_number"], k, x[k], d1.get(k), d2.get(k))); break
                    bu = await BR.get_bill_documents(req(t), uuid.UUID(x["id"]))
                    an = bu["anchor"]
                    if (TD.documents_baris(bu["groups"]) != x["documents"] or an["position_text"] != x["position_text"]
                            or D(an["paid"]) != D(x["amount_paid"]) or D(an["balance"]) != D(x["amount_due"])):
                        beda_d.append((x["invoice_number"], an["position_text"], x["position_text"], an["paid"], x["amount_paid"], an["balance"], x["amount_due"]))
                    rw = await TR.riwayat_tagihan(c, t, uuid.UUID(x["id"]), lambda m: _ya(), 200)
                    if not rw or not any(e["jenis"] == "TAGIHAN_DIBUAT" for e in rw["events"]):
                        beda_h.append(x["invoice_number"])
                cek(not beda_c, f"{L} (c) detail v1 == v2 == baris ({len(rows)})", str(beda_c[:3]))
                cek(not beda_d, f"{L} (d) bundel == baris ({len(rows)})", str(beda_d[:3]))
                cek(not beda_h, f"{L} (h) riwayat ada TAGIHAN_DIBUAT", str(beda_h[:5]))
                cek(await TR.riwayat_tagihan(c, t, uuid.uuid4(), lambda m: _ya(), 200) is None, f"{L} (h) id asing -> None")
                kata = next((x["vendor"]["name"].split()[0] for x in rows if x["vendor"]["name"]), None)
                if kata:
                    hr, _ = await semua(search=kata)
                    sc = (await svc.get_summary(t, "current_year", search=kata))["data"]
                    cek(sc["tagihan_terbuka_count"] == sum(1 for x in hr if x["status"] in TD.TERBUKA)
                        and all(sc[f"{k}_count"] == sum(1 for x in hr if kan(x) == k) for k in TD.AKSI_TAGIHAN) and 0 < len(hr) <= len(rows),
                        f"{L} (f) ?search={kata} daftar == summary", f"daftar {len(hr)} terbuka {sc['tagihan_terbuka_count']}")
                print(f"  {L}: {len(rows)} tagihan, aksi " + ", ".join(f"{k}={len(ak.get(k, ()))}" for k in TD.AKSI_TAGIHAN), flush=True)
    finally:
        await tx.rollback(); await c.close()
    print("RINGKAS gagal=%d %s" % (len(gagal), gagal[:6])); sys.exit(1 if gagal else 0)


async def _ya(): return True

asyncio.run(main())
