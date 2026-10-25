"""UJI NYATA item 7 Pembelian (25 Okt 2026): int() uang -> Decimal 0.01 HALF_UP. kaos SAJA, NOL COMMIT (satu transaksi luar SELALU ROLLBACK,
get_pool tertambal; transaksi handler = savepoint). Dijalankan di pohon LAMA (master) dan BARU, lalu BANDING:
  H1 ND 3 x 3.333 diskon dokumen 10% -> create + post lewat rute nyata: BARU diskon 999,90 / total 8.999,10 tersimpan (LAMA 999 / 9.000 =
     kontrol: LAMA memotong); jurnal ND seimbang (Σ debit == Σ kredit == header, Law 4); GET detail + daftar + ringkasan ND 200 (skema float).
  H2 tagihan ber-sen (1 x 1.000,50) -> create_bill_v2 + post: GET outstanding-summary/summary + daftar tagihan + open-bills vendor:
     BARU memuat ,50 PERSIS == Σ compute_ap_outstanding (LAMA terpotong = kontrol), tanpa galat skema.
  H0 (syarat MASTER) sebelum H1/H2: teks JSON outstanding-summary, summary, daftar tagihan, ringkasan ND, open-bills LAMA == BARU byte-identik.
  JEPRET sebelum/sesudah proses: net 0."""
import asyncio, inspect, json, os, sys
from datetime import timedelta
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from fastapi.params import Query as QP
from starlette.requests import Request
from starlette.responses import Response

T = "kaos-biru-konveksi"
VENDOR = "UJI Maklun 3B09D3"
MODE, NAMA = os.environ["MODE"], os.environ.get("NAMA", "")
gagal = []
D = lambda x: Decimal(str(x))  # noqa: E731

SQL_JEPRET = {
    "nd": "SELECT count(*)::text || '/' || COALESCE(max(credit_number), '') FROM vendor_credits WHERE tenant_id = $1",
    "tagihan": "SELECT count(*)::text || '/' || md5(COALESCE(string_agg(id::text || status_v2 || COALESCE(amount_paid::text,'-'), ',' ORDER BY id), '')) FROM bills WHERE tenant_id = $1",
    "urut_nd": "SELECT COALESCE(json_agg(json_build_object('m', year_month, 'n', last_number) ORDER BY year_month)::text, '[]') FROM vendor_credit_sequences WHERE tenant_id = $1",
    "jurnal": "SELECT count(*)::text || '/' || COALESCE(max(journal_number), '') FROM journal_entries WHERE tenant_id = $1",
    "stok": "SELECT count(*) FROM inventory_ledger WHERE tenant_id = $1",
    "idem": "SELECT count(*) FROM idempotency_keys WHERE tenant_id = $1",
    "audit": "SELECT count(*) FROM audit_logs WHERE tenant_id = $1",
}


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {str(i)[:300]}" if i else ""), flush=True)
    if not ok:
        gagal.append(l)


def req(uid, method="POST"):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": method, "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid), "role": "OWNER"}; return r


async def panggil(fn, uid, **isi):
    o = {}
    for n, p in inspect.signature(fn).parameters.items():
        if n == "request": o[n] = req(uid, "GET")
        elif n in isi: o[n] = isi[n]
        elif n == "response": o[n] = Response()
        elif isinstance(p.default, QP): o[n] = p.default.default
        elif p.default is not inspect._empty: o[n] = p.default
    x = await fn(**o)
    return json.loads(json.dumps(x.model_dump(mode="json") if hasattr(x, "model_dump") else x, default=str))


async def rekam():
    from app.routers import bill_payments as BPR
    from app.routers import bills as BR
    from app.routers import vendor_credits as VR
    from app.schemas.bills import CreateBillRequestV2
    from app.schemas.vendor_credits import CreateVendorCreditRequest
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    assert await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T), "tenant tak ada -- harness menolak"
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)
    v = await c.fetchrow("SELECT id, name FROM vendors WHERE tenant_id = $1 AND name = $2", T, VENDOR)
    hari = await c.fetchval("SELECT tanggal_bisnis($1)", T)

    class Kon:
        def __getattr__(s, a): return getattr(c, a)
        def transaction(s, **kw): return c.transaction()
    k = Kon()
    class CM:
        async def __aenter__(s): return k
        async def __aexit__(s, *a): return False
    class Pool:
        def acquire(s): return CM()
    async def gp(): return Pool()
    BR.get_pool = gp; BPR.get_pool = gp; VR.get_pool = gp
    import app.services.db_pool as DBP
    DBP.get_db_pool = gp
    out = {}
    tx = c.transaction(); await tx.start()
    try:
        # H0 (syarat MASTER): data hari ini (nol sen) -> teks JSON respons LAMA == BARU byte-identik (nilai bulat tetap int, bukan 50000.0)
        out["H0"] = {}
        for nama, fn, isi in (("outstanding_summary", BR.get_outstanding_summary, {}), ("bills_summary", BR.get_bills_summary, {"period": "current_year"}),
                              ("bills_list", BR.list_bills, {"limit": 100}), ("nd_summary", VR.get_vendor_credits_summary, {}),
                              ("open_bills", BPR.get_vendor_open_bills, {"vendor_id": str(v["id"])})):
            try:
                x = await fn(**{**{n: (req(uid, "GET") if n == "request" else (isi[n] if n in isi else (p.default.default if isinstance(p.default, QP) else p.default)))
                                   for n, p in inspect.signature(fn).parameters.items() if n == "request" or n in isi or p.default is not inspect._empty}})
                if hasattr(x, "model_dump_json"):
                    teks = x.model_dump_json()
                else:
                    from fastapi.encoders import jsonable_encoder
                    teks = json.dumps(jsonable_encoder(x), sort_keys=True)
                out["H0"][nama] = teks
            except Exception as e:  # noqa: BLE001
                out["H0"][nama] = f"GALAT {type(e).__name__}: {str(e)[:160]}"
        # H1 ND diskon dokumen 10%
        h = await VR.create_vendor_credit(req(uid), CreateVendorCreditRequest(
            vendor_id=str(v["id"]), vendor_name=v["name"], vendor_credit_date=hari, reason="other", discount_percent=10,
            notes="TES E2E item 7", items=[{"description": "TES E2E item 7", "quantity": 3, "unit_price": 3333}]), Response())
        vid = h["data"]["id"]
        await VR.post_vendor_credit(req(uid), vid, Response())
        row = await c.fetchrow("SELECT subtotal, discount_amount, tax_amount, total_amount, journal_id FROM vendor_credits WHERE id = $1::uuid", vid)
        je = await c.fetchrow("SELECT total_debit, total_credit, (SELECT sum(debit) FROM journal_lines WHERE journal_id = je.id) sd, "
                              "(SELECT sum(credit) FROM journal_lines WHERE journal_id = je.id) sk FROM journal_entries je WHERE id = $1", row["journal_id"])
        out["H1"] = {"create_total": h["data"]["total_amount"], "row": {k2: str(row[k2]) for k2 in ("subtotal", "discount_amount", "tax_amount", "total_amount")},
                     "jurnal": {k2: str(je[k2]) for k2 in ("total_debit", "total_credit", "sd", "sk")}}
        try:
            det = await panggil(VR.get_vendor_credit, uid, vendor_credit_id=vid)
            out["H1"]["detail_total"] = (det.get("data") or {}).get("total_amount")
        except Exception as e:  # noqa: BLE001
            out["H1"]["detail_total"] = f"GALAT {type(e).__name__}: {str(e)[:120]}"
        try:
            lst = await panggil(VR.list_vendor_credits, uid, limit=100)
            out["H1"]["daftar"] = [it.get("total_amount") for it in (lst.get("items") or lst.get("data") or []) if str(it.get("id")) == vid]
        except Exception as e:  # noqa: BLE001
            out["H1"]["daftar"] = f"GALAT {type(e).__name__}: {str(e)[:120]}"
        sm = await panggil(VR.get_vendor_credits_summary, uid)
        out["H1"]["ringkasan_total_value"] = sm["data"]["total_value"]
        out["H1"]["ringkasan_total_value_db"] = str(await c.fetchval("SELECT COALESCE(sum(total_amount),0) FROM vendor_credits WHERE tenant_id = $1 AND status != 'void'", T))
        # H2 tagihan ber-sen
        nomor = "TES-E2E-P2P7-SEN"
        await BR.create_bill_v2(req(uid), CreateBillRequestV2(vendor_id=v["id"], invoice_number=nomor, due_date=hari + timedelta(days=30),
                                notes="TES E2E item 7", items=[{"product_name": "TES E2E sen", "qty": 1, "price": 1000.5}]), Response())
        bid = await c.fetchval("SELECT id FROM bills WHERE tenant_id = $1 AND invoice_number = $2", T, nomor)
        await BR.post_bill(req(uid), bid, Response())
        ap = await c.fetch("SELECT bill_id, outstanding FROM compute_ap_outstanding($1)", T)
        out["H2"] = {"sisa_db": str(next((r["outstanding"] for r in ap if r["bill_id"] == bid), None)),
                     "ap_total_db": str(sum((D(r["outstanding"]) for r in ap), Decimal(0)))}
        os_ = await panggil(BR.get_outstanding_summary, uid)
        out["H2"]["outsum_total"] = os_["data"]["total_outstanding"]
        sm2 = await panggil(BR.get_bills_summary, uid, period="current_year")
        out["H2"]["summary_total_remaining"] = sm2["data"]["total_remaining"]
        out["H2"]["summary_total_remaining_db"] = str(await c.fetchval(
            "SELECT COALESCE(sum(outstanding),0) FROM compute_ap_outstanding($1) a JOIN bills b ON b.id = a.bill_id "
            "WHERE date_trunc('year', b.issue_date) = date_trunc('year', tanggal_bisnis($1))", T))
        try:
            ob = await panggil(BPR.get_vendor_open_bills, uid, vendor_id=str(v["id"]))
            out["H2"]["open_bills"] = [it["remaining_amount"] for it in ob["data"]["bills"] if it["bill_number"] == nomor] if isinstance(ob.get("data"), dict) else ob
        except Exception as e:  # noqa: BLE001
            out["H2"]["open_bills"] = f"GALAT {type(e).__name__}: {str(e)[:160]}"
    finally:
        await tx.rollback(); await c.close()
    json.dump(out, open(f"/out/r-{NAMA}.json", "w")); print(f"REKAM {NAMA} {json.dumps(out)[:900]}", flush=True)


async def jepret():
    c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
    d = {k: str(await c.fetchval(q, T)) for k, q in SQL_JEPRET.items()}
    await c.close()
    json.dump(d, open(f"/out/j-{NAMA}.json", "w")); print(f"JEPRET {NAMA} ok", flush=True)


def banding():
    L, B = json.load(open("/out/r-lama.json")), json.load(open(f"/out/r-{os.environ.get('PEMBANDING', 'baru')}.json"))
    for k in L["H0"]:
        cek(L["H0"][k] == B["H0"][k] and not L["H0"][k].startswith("GALAT"), f"(H0) JSON {k} LAMA == BARU byte-identik (data nol sen; bulat tetap int)",
            "" if L["H0"][k] == B["H0"][k] else f"{L['H0'][k][:150]} / {B['H0'][k][:150]}")
    h1l, h1 = L["H1"], B["H1"]
    cek(D(h1l["row"]["discount_amount"]) == D("999") and D(h1l["row"]["total_amount"]) == D("9000"), "(H1 kontrol) LAMA memotong diskon -> 999 / total 9.000", h1l["row"])
    cek(D(h1["row"]["discount_amount"]) == D("999.90") and D(h1["row"]["total_amount"]) == D("8999.10") and D(h1["create_total"]) == D("8999.10"),
        "(H1) BARU diskon 999,90 / total 8.999,10 tersimpan + respons create", h1)
    j = h1["jurnal"]
    cek(D(j["total_debit"]) == D(j["total_credit"]) == D(j["sd"]) == D(j["sk"]) == D("8999.10"), "(H1 Law 4) jurnal ND seimbang, baris == header == 8.999,10", j)
    cek(D(h1["detail_total"]) == D("8999.10") and h1["daftar"] and D(h1["daftar"][0]) == D("8999.10"), "(H1) GET detail + daftar ND 200 dengan sen (skema float)",
        f"{h1['detail_total']} / {h1['daftar']}")
    cek(D(h1["ringkasan_total_value"]) == D(h1["ringkasan_total_value_db"]), "(H1) ringkasan ND total_value == Σ DB (tanpa potong)",
        f"{h1['ringkasan_total_value']} / {h1['ringkasan_total_value_db']}")
    h2l, h2 = L["H2"], B["H2"]
    cek(D(h2["sisa_db"]) == D("1000.50"), "(H2) sisa jurnal tagihan ber-sen 1.000,50", h2["sisa_db"])
    cek(D(h2l["outsum_total"]) != D(h2l["ap_total_db"]), "(H2 kontrol) LAMA outstanding-summary terpotong != Σ fungsi", f"{h2l['outsum_total']} / {h2l['ap_total_db']}")
    cek(D(h2["outsum_total"]) == D(h2["ap_total_db"]), "(H2) BARU outstanding-summary == Σ compute_ap_outstanding PERSIS", f"{h2['outsum_total']} / {h2['ap_total_db']}")
    cek(D(h2["summary_total_remaining"]) == D(h2["summary_total_remaining_db"]), "(H2) BARU /bills/summary total_remaining == Σ DB tahun berjalan",
        f"{h2['summary_total_remaining']} / {h2['summary_total_remaining_db']}")
    cek(isinstance(h2["open_bills"], list) and h2["open_bills"] and D(h2["open_bills"][0]) == D("1000.50"), "(H2) BARU open-bills vendor sisa 1.000,50",
        f"{h2['open_bills']} (LAMA {h2l['open_bills']})")
    if os.path.exists("/out/j-sesudah.json"):
        s, e = json.load(open("/out/j-sebelum.json")), json.load(open("/out/j-sesudah.json"))
        for k in SQL_JEPRET:
            cek(s[k] == e[k], f"NET 0 sesudah proses: {k}", "" if s[k] == e[k] else f"{s[k]} / {e[k]}")


async def main():
    if MODE == "JEPRET":
        return await jepret()
    if MODE == "REKAM":
        return await rekam()
    banding()
    print(f"RINGKAS gagal={len(gagal)} {gagal}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
