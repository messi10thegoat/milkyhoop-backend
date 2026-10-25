"""UJI NYATA P2P-3b (25 Okt 2026): POST /vendor-credits/preview (pratinjau BUAT ND) == create + posting TERTULIS. kaos SAJA, NOL COMMIT.
Tiap kasus: (1) PRATINJAU lewat rute nyata di koneksi P tersendiri (transaksi READ ONLY tingkat atas -> DB menolak tulisan apa pun);
(2) TULIS lewat rute nyata create_vendor_credit lalu post_vendor_credit di koneksi W dalam transaksi luar yang SELALU di-ROLLBACK (get_pool
tertambal; transaksi handler = savepoint). BANDING per kasus:
  galat create/post -> pratinjau can_proceed false dan blocks[0].message == detail galat PERTAMA penulis
  sukses -> nomor perkiraan == credit_number terbit · subtotal/diskon/pajak/total/nomor tagihan asal == baris tertulis
          · jurnal pratinjau (source_type, akun, debit, kredit, memo) == jurnal tertulis ND itu · stok/sisa tagihan == pratinjau posting
            draf yang baru dibuat (/{id}/post/preview, terbukti == tulis di U3d)
  PRATINJAU dua kali == sama (deterministik). JEPRET sebelum/sesudah proses: net 0.
MERAH (pohon tersabotase, .sh): perkiraan nomor +2 DAN baris maya diabaikan (HPP dari tabel) -> (NOMOR) dan (STOK) WAJIB merah."""
import asyncio, json, os, sys, uuid
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from fastapi import HTTPException
from starlette.requests import Request

T = "kaos-biru-konveksi"
VENDOR, TAGIHAN, BARANG = "UJI Maklun 3B09D3", "BILL-2609-0003", "UJI FG Subkontrak 3B09D3"
MODE, NAMA = os.environ["MODE"], os.environ.get("NAMA", "")
gagal = []
D = lambda x: Decimal(str(x))  # noqa: E731


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {str(i)[:400]}" if i else ""), flush=True)
    if not ok:
        gagal.append(l)


SQL_JEPRET = {
    "nd": "SELECT count(*)::text || '/' || COALESCE(max(credit_number), '') FROM vendor_credits WHERE tenant_id = $1",
    "urut_nd": "SELECT COALESCE(json_agg(json_build_object('m', year_month, 'n', last_number) ORDER BY year_month)::text, '[]') FROM vendor_credit_sequences WHERE tenant_id = $1",
    "jurnal": "SELECT count(*)::text || '/' || COALESCE(max(journal_number), '') FROM journal_entries WHERE tenant_id = $1",
    "stok": "SELECT count(*) FROM inventory_ledger WHERE tenant_id = $1",
    "idem": "SELECT count(*) FROM idempotency_keys WHERE tenant_id = $1",
    "tagihan": "SELECT md5(COALESCE(string_agg(id::text || status_v2 || COALESCE(amount_paid::text,'-'), ',' ORDER BY id), '')) FROM bills WHERE tenant_id = $1",
    "audit": "SELECT count(*) FROM audit_logs WHERE tenant_id = $1",
}


def req(uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid), "role": "OWNER"}; return r


def pool_untuk(conn):
    class CM:
        async def __aenter__(s): return conn
        async def __aexit__(s, *a): return False
    class Pool:
        def acquire(s): return CM()
    async def gp(): return Pool()
    return gp


def rapi(d):
    return json.loads(json.dumps(d, default=str))


async def kasus(c):
    assert await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T), "tenant tak ada -- harness menolak"
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)
    v = await c.fetchrow("SELECT id::text id, name FROM vendors WHERE tenant_id = $1 AND name = $2", T, VENDOR)
    b = await c.fetchrow("SELECT id::text id FROM bills WHERE tenant_id = $1 AND invoice_number = $2", T, TAGIHAN)
    p = await c.fetchrow("SELECT id::text id FROM products WHERE tenant_id = $1 AND nama_produk = $2", T, BARANG)
    lain = await c.fetchrow("""SELECT b.id::text id FROM bills b WHERE b.tenant_id = $1 AND b.vendor_id <> $2::uuid AND b.status_v2 = 'posted'
                               ORDER BY b.created_at LIMIT 1""", T, v["id"])
    assert v and b and p, ("data uji kaos tak lengkap", v, b, p)
    dasar = {"vendor_id": v["id"], "vendor_name": v["name"], "vendor_credit_date": str(await c.fetchval("SELECT tanggal_bisnis($1)", T))}
    it = lambda **k: {"description": "TES E2E P2P-3b", "quantity": 1, "unit_price": 10000, **k}  # noqa: E731
    k = [
        {**dasar, "reason": "return", "original_bill_id": b["id"], "items": [it(item_id=p["id"], unit_price=10000)]},          # HPP + stok + sisa
        {**dasar, "reason": "pricing_error", "items": [it(unit_price=2500, tax_rate=11)]},                                      # PPN (PKP?) / NON_PKP
        {**dasar, "reason": "discount", "original_bill_id": b["id"], "items": [it(unit_price=1000, tax_rate=11)]},              # pagar potongan PPN
        {**dasar, "reason": "return", "original_bill_id": b["id"], "items": [it(unit_price=999999)]},                           # melebihi sisa
        {**dasar, "vendor_id": str(uuid.uuid4()), "reason": "other", "items": [it()]},                                          # vendor tak ada
        {**dasar, "reason": "other", "discount_percent": 10, "items": [it(quantity=3, unit_price=3333, discount_percent=5)]},  # pembulatan
        {**dasar, "reason": "damaged", "items": [it(item_id=p["id"], quantity=2, unit_price=1)]},                               # HPP tanpa tagihan
    ]
    if lain:
        k.append({**dasar, "reason": "other", "original_bill_id": lain["id"], "items": [it(unit_price=500)]})                   # tagihan vendor lain
    return uid, k


async def pratinjau_satu(VR, Body, p, uid, x):
    VR.get_pool = pool_untuk(p)
    try:
        return {"ok": rapi((await VR.preview_create_vendor_credit(req(uid), Body(**x)))["data"])}
    except HTTPException as e:
        return {"galat": [e.status_code, rapi(e.detail)]}


async def rekam():
    from app.routers import vendor_credits as VR
    from app.schemas.vendor_credits import CreateVendorCreditRequest as Body
    p = await asyncpg.connect(os.environ["DATABASE_URL"])
    w = await asyncpg.connect(os.environ["DATABASE_URL"])
    uid, ks = await kasus(p)
    out = []
    for x in ks:
        r1 = await pratinjau_satu(VR, Body, p, uid, x)
        r2 = await pratinjau_satu(VR, Body, p, uid, x)
        rec = {"kasus": x, "p": r1, "p2": r2}
        tx = w.transaction(); await tx.start()
        try:
            VR.get_pool = pool_untuk(w)
            try:
                h = await VR.create_vendor_credit(req(uid), Body(**x))
            except HTTPException as e:
                rec["tulis"] = {"langkah": "create", "galat": [e.status_code, rapi(e.detail)]}
                out.append(rec); continue
            vid = uuid.UUID(h["data"]["id"])
            pp = (await VR.preview_post_vendor_credit(req(uid), vid))["data"]  # penentu posting atas draf NYATA (U3d: == tulis)
            try:
                await VR.post_vendor_credit(req(uid), vid)
            except HTTPException as e:
                rec["tulis"] = {"langkah": "post", "galat": [e.status_code, rapi(e.detail)], "pp": rapi(pp)}
                out.append(rec); continue
            row = await w.fetchrow("SELECT credit_number, subtotal, discount_amount, tax_amount, total_amount, original_bill_number "
                                   "FROM vendor_credits WHERE id = $1", vid)
            jl = await w.fetch("""SELECT je.source_type, coa.account_code, jl.debit, jl.credit, jl.memo
                                  FROM journal_entries je JOIN journal_lines jl ON jl.journal_id = je.id
                                  LEFT JOIN chart_of_accounts coa ON coa.id = jl.account_id
                                  WHERE je.tenant_id = $1 AND je.source_id = $2 ORDER BY je.source_type, coa.account_code, jl.debit, jl.credit""", T, vid)
            rec["tulis"] = {"langkah": "ok", "row": rapi(dict(row)), "jurnal": rapi([dict(r) for r in jl]), "pp": rapi(pp)}
            out.append(rec)
        finally:
            await tx.rollback()
    await p.close(); await w.close()
    json.dump(out, open(f"/out/r-{NAMA}.json", "w"))
    print(f"REKAM {NAMA} {len(out)} kasus: " + ", ".join(r["tulis"]["langkah"] for r in out), flush=True)


async def jepret():
    c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
    d = {k: str(await c.fetchval(q, T)) for k, q in SQL_JEPRET.items()}
    await c.close()
    json.dump(d, open(f"/out/j-{NAMA}.json", "w")); print(f"JEPRET {NAMA} ok", flush=True)


def jur_p(p):
    return sorted([(j["source_type"], l["account_code"], D(l["debit"] or 0), D(l["credit"] or 0), l["memo"])
                   for j in p["journals"] for l in j["lines"]], key=lambda t: (t[0], t[1] or "", t[2], t[3]))


def jur_t(t):
    return sorted([(r["source_type"], r["account_code"], D(r["debit"] or 0), D(r["credit"] or 0), r["memo"]) for r in t["jurnal"]],
                  key=lambda t: (t[0], t[1] or "", t[2], t[3]))


def banding():
    out = json.load(open(f"/out/r-{os.environ.get('PEMBANDING', 'baru')}.json"))
    n_ok = n_galat = 0
    for i, r in enumerate(out):
        p, t = r["p"].get("ok"), r["tulis"]
        lab = f"[{i}] {r['kasus']['reason']}"
        cek(r["p"] == r["p2"], f"{lab} (DET) pratinjau dua kali == sama")
        if p is None:
            cek(False, f"{lab} pratinjau galat HTTP", r["p"]); continue
        if t["langkah"] != "ok":
            n_galat += 1
            det = t["galat"][1]
            pesan = det.get("message") if isinstance(det, dict) else det
            cek(p["can_proceed"] is False and p["blocks"] and p["blocks"][0]["message"] == str(pesan),
                f"{lab} (BLOK) galat {t['langkah']} {t['galat'][0]} == blok pertama pratinjau", f"{p['blocks']} / {t['galat']}")
            continue
        n_ok += 1
        row = t["row"]
        cek(p["can_proceed"] is True and not p["blocks"], f"{lab} (OK) pratinjau boleh lanjut", p["blocks"])
        cek(p["vendor_credit_number_preview"] == row["credit_number"] and p["vendor_credit_number_estimated"] is True,
            f"{lab} (NOMOR) perkiraan == terbit", f"{p['vendor_credit_number_preview']} / {row['credit_number']}")
        cek(all(D(p[k]) == D(row[k]) for k in ("subtotal", "discount_amount", "tax_amount", "total_amount"))
            and p["original_bill_number"] == row["original_bill_number"], f"{lab} (ANGKA) total == tertulis",
            f"{[(k, p[k], row[k]) for k in ('subtotal', 'discount_amount', 'tax_amount', 'total_amount', 'original_bill_number')]}")
        cek(jur_p(p) == jur_t(t) and jur_t(t), f"{lab} (J) jurnal pratinjau == tertulis ({len(jur_t(t))} baris)", f"{jur_p(p)} / {jur_t(t)}")
        pp = t["pp"]
        cek(p["stock"] == pp["stock"], f"{lab} (STOK) stok == pratinjau posting draf nyata ({len(p['stock'])})", f"{p['stock']} / {pp['stock']}")
        cek([(b["bill_number"], b["remaining_before"], b["remaining_after"]) for b in p["bills"]] ==
            [(b["bill_number"], b["remaining_before"], b["remaining_after"]) for b in pp["bills"]], f"{lab} (SISA) sisa tagihan == pratinjau posting",
            f"{p['bills']} / {pp['bills']}")
    cek(n_ok >= 3 and n_galat >= 2, f"(CAKUPAN) sukses {n_ok} >= 3, galat {n_galat} >= 2")
    cek(any(r["p"].get("ok", {}).get("stock") for r in out if r["tulis"]["langkah"] == "ok"), "(CAKUPAN) >= 1 kasus sukses ber-HPP/stok")
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
