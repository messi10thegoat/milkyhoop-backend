"""UJI NYATA SALDO-AWAL-VENDOR-C (25 Okt 2026), ROLLBACK-ONLY di kaos. Mode: baru [merah_c]
Rute NYATA set_vendor_opening_balance atas vendor kaos (tanpa saldo awal) dgn badan sah -> 409 SALDO_AWAL_VENDOR_DITAHAN + pesan; jurnal
VENDOR_OPENING_BALANCE & baris accounts_payable kaos sebelum == sesudah (nol tulisan). Kontrol merah (WAJIB FAIL): merah_c = salinan pohon
dengan penahan MATI -> jalur lama memposting (dalam rollback) atau bukan 409 penahan."""
import asyncio, json, os, sys
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from fastapi import HTTPException
from starlette.requests import Request

T = "kaos-biru-konveksi"
gagal = []


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:300]}"), flush=True)
    if not ok:
        gagal.append(nama)


async def main():
    from app.routers import vendors as RV
    c = await asyncpg.connect(os.environ["URL"])
    if not await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T):
        sys.exit(f"TENANT TAK ADA: {T}")
    uid = await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", T)

    class Kon:
        def __getattr__(s, n): return getattr(c, n)
        def transaction(s, **kw): return c.transaction()

    class CMA:
        async def __aenter__(s): return Kon()
        async def __aexit__(s, *a): return False

    class PA:
        def acquire(s): return CMA()
    async def gpa(): return PA()
    RV.get_pool = gpa
    jumlah = lambda: c.fetchval("""SELECT (SELECT count(*) FROM journal_entries WHERE tenant_id = $1 AND source_type = 'VENDOR_OPENING_BALANCE')
                                        + (SELECT count(*) FROM accounts_payable WHERE tenant_id = $1 AND source_type = 'VENDOR_OPENING_BALANCE')""", T)  # noqa: E731
    luar = c.transaction()
    await luar.start()
    try:
        vid = await c.fetchval("""SELECT v.id::text FROM vendors v WHERE v.tenant_id = $1 AND NOT EXISTS (SELECT 1 FROM journal_entries je
                                    WHERE je.tenant_id = v.tenant_id AND je.source_type = 'VENDOR_OPENING_BALANCE' AND je.source_id = v.id)
                                  ORDER BY v.created_at DESC LIMIT 1""", T)
        n0 = await jumlah()
        badan = json.dumps({"amount": 1000, "as_of_date": "2026-10-25"}).encode()

        async def receive(): return {"type": "http.request", "body": badan, "more_body": False}
        req = Request({"type": "http", "method": "POST", "path": "/", "headers": [(b"content-type", b"application/json")], "query_string": b""}, receive)
        req.state.user = {"tenant_id": T, "user_id": str(uid), "role": "OWNER", "username": "TES E2E"}
        e = None
        try:
            await RV.set_vendor_opening_balance(req, vid)
        except HTTPException as x:
            e = x
        n1 = await jumlah()
        cek("rute nyata: 409 SALDO_AWAL_VENDOR_DITAHAN + pesan", e is not None and e.status_code == 409 and isinstance(e.detail, dict)
            and e.detail.get("code") == "SALDO_AWAL_VENDOR_DITAHAN", e and (e.status_code, e.detail))
        cek(f"nol tulisan: jurnal OB vendor + accounts_payable OB kaos {n0} == {n1}", n0 == n1, (n0, n1))
    finally:
        await luar.rollback()
        await c.close()
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
