"""UJI NYATA kontrak daftar seragam Pembayaran Keluar (22 Okt 2026). grapgrap + kaos, SEMUA pembayaran, satu transaksi yang SELALU di-ROLLBACK.
Data nyata hari ini: nol pembayaran ber-sisa belum dialokasikan -> kaos diberi 2 baris SINTETIS 'TES E2E' DI DALAM transaksi (di-ROLLBACK;
nol tulisan tersisa): posted 50.000 tanpa alokasi (belum) + posted 30.000 beralokasi 10.000 ke satu tagihan kaos (sebagian). grapgrap BACA saja.
  (a) PASANGAN SQL<->Python: id ?aksi=belum_dialokasikan (SQL sql_belum) == baris ber-aksi (Python alokasi_fakta)
  (b) summary belum_dialokasikan_count/_value == baris
  (c) with_totals: sum_sisa == Σ belum_dialokasikan baris ber-aksi; sum_total == Σ total_amount bukan voided (tanpa filter DAN ?aksi)
  (d) detail == baris (aksi, alokasi_*, position_text, documents, doc_count)
  (e) bundel /documents: documents_baris(groups) == documents baris; anchor position_text/paid/balance == baris; dok tagihan di bundel
      pembayaran == dok tagihan yang SAMA di bundel /bills/{id}/documents (satu status lintas bundel)
  (f) ?search=: id daftar == hitungan summary (total_count bukan voided + belum_dialokasikan_count)
  (g) riwayat: tiap pembayaran ada PEMBAYARAN_DIBUAT; id asing -> None
MERAH=1 : Python alokasi_fakta disabotase (aksi selalu None) -> (a)/(b) WAJIB gagal (baris sintetis menjamin ada yang ber-aksi)."""
import asyncio, os, sys, uuid
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

TEN = ["grapgrap-manado", "kaos-biru-konveksi"]
MERAH = os.environ.get("MERAH") == "1"
gagal = []
KUNCI = ("aksi", "alokasi_keadaan", "alokasi_teks", "dialokasikan", "belum_dialokasikan", "position_text", "documents", "doc_count")


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


def D(v): return Decimal(str(v if v is not None else 0))


def req(t):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": t, "user_id": None}; return r


async def _ya(): return True


async def main():
    from app.routers import bill_payments as BP
    from app.routers import bills as BR
    from app.services import pembayaran_keluar_daftar as PK
    from app.services import bayar_keluar_riwayat as RW
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    tx = c.transaction(); await tx.start()
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    BP.get_pool = gp; BR.get_pool = gp
    if MERAH:
        _a = PK.alokasi_fakta
        PK.alokasi_fakta = lambda *a, **k: {**_a(*a, **k), "aksi": None}
    try:
        T = "kaos-biru-konveksi"
        v = await c.fetchrow("SELECT id, name FROM vendors WHERE tenant_id = $1 ORDER BY created_at LIMIT 1", T)
        b = await c.fetchval("SELECT bill_id FROM compute_ap_outstanding($1) WHERE bill_id IS NOT NULL AND outstanding > 10000 LIMIT 1", T)
        p1 = await c.fetchval("""INSERT INTO bill_payments_v2 (tenant_id, payment_number, vendor_id, vendor_name, payment_date, total_amount, status, notes)
                                 VALUES ($1, 'TES-E2E-BK-1', $2, $3, DATE '2026-10-22', 50000, 'posted', 'TES E2E kontrak') RETURNING id""", T, v["id"], v["name"])
        p2 = await c.fetchval("""INSERT INTO bill_payments_v2 (tenant_id, payment_number, vendor_id, vendor_name, payment_date, total_amount, status, notes)
                                 VALUES ($1, 'TES-E2E-BK-2', $2, $3, DATE '2026-10-22', 30000, 'posted', 'TES E2E kontrak') RETURNING id""", T, v["id"], v["name"])
        sb = await c.fetchval("SELECT outstanding FROM compute_ap_outstanding($1) WHERE bill_id = $2", T, b)
        await c.execute("""INSERT INTO bill_payment_allocations (payment_id, bill_id, remaining_before, amount_applied, remaining_after)
                           VALUES ($1, $2, $3::numeric, 10000, $3::numeric - 10000)""", p2, b, sb)
        for t in TEN:
            async def semua(**kw):
                rows, skip = [], 0
                while True:
                    r = await BP.list_bill_payments(req(t), status="all", vendor_id=None, payment_method=None, search=kw.get("search"),
                                                    date_from=None, date_to=None, skip=skip, limit=100, sort_by="created_at", sort_order="desc",
                                                    aksi=kw.get("aksi"), with_totals=kw.get("with_totals", False))
                    r = r.model_dump()
                    rows += r["items"]; skip += 100
                    if not r["has_more"]: return rows, r
            rows, _ = await semua()
            ber = {x["id"] for x in rows if x["aksi"] == "belum_dialokasikan"}
            ids = {x["id"] for x in (await semua(aksi="belum_dialokasikan"))[0]}
            cek(ids == ber, f"{t} (a) ?aksi=belum_dialokasikan SQL == Python", f"sql {len(ids)} py {len(ber)}")
            sm = (await BP.get_bill_payments_summary(req(t), search=None)).model_dump()["data"]
            nilai = sum((D(x["belum_dialokasikan"]) for x in rows if x["aksi"]), Decimal(0))
            cek(sm["belum_dialokasikan_count"] == len(ber) and abs(D(sm["belum_dialokasikan_value"]) - nilai) < Decimal("0.01"),
                f"{t} (b) summary belum_dialokasikan", f"{sm['belum_dialokasikan_count']}/{sm['belum_dialokasikan_value']} vs {len(ber)}/{nilai}")
            _, rt = await semua(with_totals=True)
            _, ra = await semua(aksi="belum_dialokasikan", with_totals=True)
            st = sum((D(x["total_amount"]) for x in rows if x["status"] != "voided"), Decimal(0))
            sa = sum((D(x["total_amount"]) for x in rows if x["aksi"] and x["status"] != "voided"), Decimal(0))
            cek(D(rt["sum_total"]) == st and D(rt["sum_sisa"]) == nilai and D(ra["sum_sisa"]) == nilai and D(ra["sum_total"]) == sa,
                f"{t} (c) with_totals", f"{rt['sum_total']}/{rt['sum_sisa']} aksi {ra['sum_total']}/{ra['sum_sisa']} vs {st}/{nilai}/{sa}")
            beda_d, beda_e, beda_g, beda_x = [], [], [], []
            for x in rows:
                d = (await BP.get_bill_payment(req(t), x["id"])).model_dump()["data"]
                for k in KUNCI:
                    if d.get(k) != x[k]:
                        beda_d.append((x["payment_number"], k, x[k], d.get(k))); break
                bu = await BP.get_payment_documents(req(t), x["id"])
                an = bu["anchor"]
                if (PK.documents_baris(bu["groups"]) != x["documents"] or an["position_text"] != x["position_text"]
                        or D(an["paid"]) != D(x["dialokasikan"]) or D(an["balance"]) != D(x["belum_dialokasikan"])
                        or (x["status"] == "posted" and D(an["total"]) + D(an["discount"]) != D(an["paid"]) + D(an["balance"]))):
                    beda_e.append((x["payment_number"], an["position_text"], x["position_text"], an["paid"], an["balance"]))
                for g in bu["groups"]:
                    if g["key"] != "faktur":
                        continue
                    for dk in g["docs"]:
                        bb = await BR.get_bill_documents(req(t), uuid.UUID(dk["id"]))
                        sendiri = next(gg for gg in bb["groups"] if gg["key"] == "faktur")["docs"][0]
                        if sendiri != dk:
                            beda_x.append((x["payment_number"], dk["number"], dk["status"], sendiri["status"]))
                rw = await RW.riwayat_bayar_keluar(c, t, uuid.UUID(x["id"]), lambda m: _ya(), 200)
                if not rw or not any(e["jenis"] == "PEMBAYARAN_DIBUAT" for e in rw["events"]):
                    beda_g.append(x["payment_number"])
            cek(not beda_d, f"{t} (d) detail == baris ({len(rows)})", str(beda_d[:3]))
            cek(not beda_e, f"{t} (e) bundel == baris ({len(rows)})", str(beda_e[:3]))
            cek(not beda_x, f"{t} (e) dok tagihan bundel bayar == bundel tagihan", str(beda_x[:3]))
            cek(not beda_g, f"{t} (g) riwayat ada PEMBAYARAN_DIBUAT", str(beda_g[:5]))
            cek(await RW.riwayat_bayar_keluar(c, t, uuid.uuid4(), lambda m: _ya(), 200) is None, f"{t} (g) id asing -> None")
            kata = next((x["vendor_name"].split()[0] for x in rows if x["vendor_name"]), None)
            if kata:
                hr, _ = await semua(search=kata)
                sc = (await BP.get_bill_payments_summary(req(t), search=kata)).model_dump()["data"]
                cek(sc["total_count"] == sum(1 for x in hr if x["status"] != "voided")
                    and sc["belum_dialokasikan_count"] == sum(1 for x in hr if x["aksi"]) and 0 < len(hr) <= len(rows),
                    f"{t} (f) ?search={kata} daftar == summary", f"daftar {len(hr)} summary {sc['total_count']}/{sc['belum_dialokasikan_count']}")
            kd = {}
            for x in rows:
                kd[x["alokasi_keadaan"]] = kd.get(x["alokasi_keadaan"], 0) + 1
            print(f"  {t}: {len(rows)} pembayaran, keadaan {kd}, belum_dialokasikan {len(ber)}", flush=True)
    finally:
        await tx.rollback(); await c.close()
    print("RINGKAS gagal=%d %s" % (len(gagal), gagal[:6])); sys.exit(1 if gagal else 0)

asyncio.run(main())
