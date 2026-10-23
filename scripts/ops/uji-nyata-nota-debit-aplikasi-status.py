"""UJI NYATA ND applications[] status (23 Okt 2026, aditif). kaos SAJA; ND sintetis 'TES E2E' lewat HANDLER NYATA di SATU transaksi luar yang
SELALU di-ROLLBACK: buat -> posting -> terapkan ke BILL-2609-0003 -> lepas -> terapkan lagi. Lalu GET detail:
  (a) 2 baris penerapan: pertama status 'reversed' + reversed_at + alasan, kedua 'active' (== status V443 di tabel); baris dilepas TIDAK dibuang
  (b) medan lama tiap baris (bill_number, amount_applied, application_date) -> /out/<NAMA>.json; BANDING: pohon LAMA == BARU (medan lama tak berubah)
MERAH=1 : status disabotase ('active' semua) -> (a) WAJIB gagal."""
import asyncio, json, os, sys
from datetime import date
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

T, TAGIHAN, TGL = "kaos-biru-konveksi", "BILL-2609-0003", date(2026, 10, 23)
MODE, NAMA = os.environ["MODE"], os.environ.get("NAMA", "")
gagal = []


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


def req(uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid)}; return r


async def tulis():
    from app.routers import vendor_credits as VR
    from app.schemas.vendor_credits import CreateVendorCreditRequest, ApplyVendorCreditRequest, UnapplyVendorCreditRequest
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    bill = await c.fetchrow("SELECT id, vendor_id, vendor_name FROM bills WHERE tenant_id = $1 AND invoice_number = $2", T, TAGIHAN)
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    VR.get_pool = gp
    tx = c.transaction(); await tx.start()
    try:
        r = await VR.create_vendor_credit(req(uid), CreateVendorCreditRequest(
            vendor_id=str(bill["vendor_id"]), vendor_name=bill["vendor_name"], vendor_credit_date=TGL, reason="other", ref_no="TES E2E aplikasi",
            notes="TES E2E aplikasi (rollback)", items=[{"description": "TES E2E", "quantity": 1, "unit_price": 1000}]))
        vid = r["data"]["id"] if isinstance(r, dict) else r.data["id"]
        await VR.post_vendor_credit(req(uid), vid)
        ab = ApplyVendorCreditRequest(applications=[{"bill_id": str(bill["id"]), "amount": 1000}], application_date=TGL)
        await VR.apply_vendor_credit(req(uid), vid, ab)
        await VR.unapply_vendor_credit(req(uid), vid, UnapplyVendorCreditRequest(reason="TES E2E lepas"))
        await VR.apply_vendor_credit(req(uid), vid, ab)
        d = (await VR.get_vendor_credit(req(uid), vid))["data"]
        d = d if isinstance(d, dict) else d.model_dump()
        apps = d["applications"]
        if os.environ.get("MERAH") == "1":
            apps = [{**a, "status": "active"} for a in apps]
        db = {str(x["id"]): x for x in await c.fetch("SELECT id, status, reversed_at, reversal_reason FROM vendor_credit_applications WHERE vendor_credit_id = $1", vid)}
        if "status" in (apps[0] if apps else {}):
            cek(len(apps) == 2, "(a) dua baris penerapan (yang dilepas TIDAK dibuang)", str(len(apps)))
            cek([a["status"] for a in apps] == ["reversed", "active"], "(a) urutan + status: reversed lalu active", str([a.get("status") for a in apps]))
            cek(all(a["status"] == db[a["id"]]["status"] for a in apps), "(a) status == tabel V443")
            cek(apps[0]["reversal_reason"] == "TES E2E lepas" and apps[0]["reversed_at"] and apps[1]["reversed_at"] is None,
                "(a) reversed_at + alasan pada baris dilepas saja")
        lama = [{k: a[k] for k in ("bill_number", "amount_applied", "application_date")} for a in apps]
        json.dump(lama, open(f"/out/{NAMA}.json", "w"), default=str)
    finally:
        await tx.rollback(); await c.close()
    print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)


async def main():
    if MODE == "TULIS":
        return await tulis()
    a, b = json.load(open("/out/lama.json")), json.load(open("/out/baru.json"))
    cek(a == b, "(b) medan lama applications[] LAMA == BARU", "" if a == b else f"{a} / {b}")
    print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)

asyncio.run(main())
