"""UJI NYATA Faktur Pembelian dari compute_ap_rincian (PEMBELIAN-SERAGAM unit (1), 22 Okt 2026). BACA-SAJA (READ ONLY + ROLLBACK),
grapgrap + kaos, SEMUA tagihan. argv: lama <out.json> | baru <out.json> | banding <lama.json> <baru.json>
  lama/baru : list_bills (semua halaman) + get_bill + get_bill_v2 per tagihan + get_summary (current_year, 2026-09, 2026-10) -> json
  baru      : + penegasan
     (a) baris daftar == detail v1 == detail v2 (status, amount_paid, amount_due)
     (b) == kembaran Python tagihan_daftar.keadaan_tagihan/sisa_tagihan dari baris compute_ap_rincian (pasangan SQL<->Python)
     (c) tagihan aktif ber-kredit AP: amount_due == COALESCE(compute_ap_outstanding.outstanding, 0)
     (d) summary: count/remaining per bucket == baris daftar ber-issue_date di periode (status sama), total_remaining == Σ sisa
     (e) setiap ?status= daftar (paid/partial/unpaid/overdue/void/draft/active) == baris daftar ber-status itu
  banding   : semua beda lama -> baru dicetak (laporan perubahan tampilan); syarat: baris yang berubah PERSIS yang (b) jelaskan.
MERAH=1 : harapan Python satu baris dibalik -> wajib gagal."""
import asyncio, json, os, sys
from datetime import date
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


async def main():
    mode = sys.argv[1]
    if mode == "banding":
        return banding()
    from app.services import bills_service as BS
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    tx = c.transaction(readonly=True); await tx.start()
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    svc = BS.BillsService(P())
    out = {}
    try:
        for t in TEN:
            rows, skip = [], 0
            while True:
                r = await svc.list_bills(t, skip=skip, limit=100, status="all")
                rows += r["items"]; skip += 100
                if not r["has_more"]: break
            for x in rows:
                d1 = await svc.get_bill(t, x["id"])
                d2 = await svc.get_bill_v2(t, x["id"])
                out[f"{t}|{x['id']}"] = {"nomor": x["invoice_number"], "baris": [x["status"], x["amount_paid"], x["amount_due"]],
                                         "v1": [d1["status"], str(d1["amount_paid"]), str(d1["amount_due"])],
                                         "v2": [d2["status"], str(d2["amount_paid"]), str(d2["amount_due"])]}
            for per in ("current_year", "2026-09", "2026-10"):
                sm = (await svc.get_summary(t, per))["data"]
                out[f"{t}|summary|{per}"] = {k: sm[k] for k in ("total_count", "total_amount", "total_remaining")} | {
                    "breakdown": {k: [v["count"], v["amount"]] for k, v in sm["breakdown"].items()}}
            if mode != "baru":
                continue
            from app.services import tagihan_daftar as TD
            from app.utils.tanggal_tenant import tanggal_dokumen
            hari = await tanggal_dokumen(c, t)
            rin = {str(r["bill_id"]): dict(r) for r in await c.fetch("SELECT * FROM compute_ap_rincian($1)", t)}
            ap = {str(r["bill_id"]): r["outstanding"] for r in await c.fetch(
                "SELECT bill_id, outstanding FROM compute_ap_outstanding($1) WHERE bill_id IS NOT NULL", t)}
            meta = {str(r["id"]): r for r in await c.fetch(
                "SELECT id, status, status_v2, amount, due_date, issue_date FROM bills WHERE tenant_id=$1", t)}
            ea, eb, ec = [], [], []
            for i, x in enumerate(rows):
                k = f"{t}|{x['id']}"; o = out[k]; m = meta[x["id"]]
                if not (o["baris"][0] == o["v1"][0] == o["v2"][0] and D(o["baris"][1]) == D(o["v1"][1]) == D(o["v2"][1])
                        and D(o["baris"][2]) == D(o["v1"][2]) == D(o["v2"][2])):
                    ea.append((x["invoice_number"], o))
                kd = TD.keadaan_tagihan(m["status"], m["status_v2"], rin.get(x["id"]), m["due_date"], hari)
                ss = TD.sisa_tagihan(m["status"], m["status_v2"], rin.get(x["id"]), m["amount"])
                byr = D((rin.get(x["id"]) or {}).get("paid_amount"))
                if MERAH and i == 0: kd = "MERAH"
                # daftar membulatkan ke int (perilaku lama `int(...)`)
                if (x["status"], int(x["amount_paid"]), int(x["amount_due"])) != (kd, int(byr), int(ss)):
                    eb.append((x["invoice_number"], m["status"], m["status_v2"], x["status"], kd, x["amount_due"], str(ss)))
                if x["status"] not in ("void", "draft") and D(x["amount_due"]) != D(int(D(ap.get(x["id"], 0)))):
                    ec.append((x["invoice_number"], x["amount_due"], str(ap.get(x["id"]))))
            cek(not ea, f"{t}: daftar == detail v1 == detail v2 ({len(rows)} tagihan)", str(ea[:2]))
            cek(not eb, f"{t}: SQL == kembaran Python (keadaan, dibayar, sisa)", str(eb[:3]))
            cek(not ec, f"{t}: tagihan aktif sisa == compute_ap_outstanding", str(ec[:3]))
            sebaran = {}
            for x in rows: sebaran[x["status"]] = sebaran.get(x["status"], 0) + 1
            print(f"  {t} sebaran status: {json.dumps(sebaran)}")
            # (d) summary per periode
            ed = []
            for per, (a, b) in {"current_year": (date(hari.year, 1, 1), date(hari.year + 1, 1, 1)), "2026-09": (date(2026, 9, 1), date(2026, 10, 1)),
                                "2026-10": (date(2026, 10, 1), date(2026, 11, 1))}.items():
                sm = out[f"{t}|summary|{per}"]
                ikut = [x for x in rows if a <= date.fromisoformat(x["issue_date"]) < b and x["status"] not in ("draft", "void")]
                harap = {k: [sum(1 for x in ikut if x["status"] == k), sum(int(x["amount_due"]) for x in ikut if x["status"] == k)]
                         for k in ("partial", "unpaid", "overdue")}
                harap["paid"] = [sum(1 for x in ikut if x["status"] == "paid"), 0]
                if sm["total_count"] != len(ikut) or any(sm["breakdown"][k] != harap[k] for k in harap) \
                        or abs(sm["total_remaining"] - sum(int(x["amount_due"]) for x in ikut)) > len(ikut):
                    ed.append((per, sm, harap, len(ikut)))
            cek(not ed, f"{t}: summary bucket == baris daftar (3 periode)", str(ed[:1])[:400])
            # (e) filter ?status=
            ee = []
            for st in ("paid", "partial", "unpaid", "overdue", "void", "draft", "active"):
                r = await svc.list_bills(t, skip=0, limit=100, status=st)
                ids = {y["id"] for y in r["items"]}
                harap = {x["id"] for x in rows if (x["status"] == st if st not in ("active", "unpaid") else
                                                   (x["status"] not in ("draft", "void") if st == "active" else x["status"] in ("unpaid", "partial", "overdue")))}
                if ids != harap or r["total"] != len(harap):
                    ee.append((st, len(ids), len(harap), r["total"]))
            cek(not ee, f"{t}: ?status= == baris daftar ber-status itu (7 filter)", str(ee[:3]))
    finally:
        await tx.rollback(); await c.close()
    json.dump(out, open(sys.argv[2], "w"), sort_keys=True, default=str)
    print(mode, len(out), "entri")
    if mode == "baru":
        print("RINGKAS gagal=%d %s" % (len(gagal), gagal[:6])); sys.exit(1 if gagal else 0)


def banding():
    a, b = json.load(open(sys.argv[2])), json.load(open(sys.argv[3]))
    beda = [(k, a[k], b.get(k)) for k in sorted(a) if a[k] != b.get(k)]
    hilang = [k for k in b if k not in a]
    print(f"BANDING: {len(a)} entri lama, {len(beda)} berubah, {len(hilang)} baru")
    for k, x, y in beda:
        print(f"  {k}\n     lama {json.dumps(x, sort_keys=True)}\n     baru {json.dumps(y, sort_keys=True)}")


asyncio.run(main())
