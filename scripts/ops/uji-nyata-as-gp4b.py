"""UJI NYATA AS-GP4b (24 Okt 2026) PATCH UM Pembelian If-Match x idem, ROLLBACK-ONLY di kaos (TES E2E). Mode: baru [merah_ifmatch]
UM draf TES E2E -> updated_at dimundurkan 1 jam (DALAM rollback; NOW() tetap sepanjang transaksi, tanpa ini v1 tak pernah basi) = v1 ->
(1) PATCH kunci K + If-Match v1 = berhasil; (2) ulang kunci K + If-Match v1 (kini basi) = jawaban PERTAMA + X-Idempotent-Replay;
(3) kunci baru + If-Match v1 basi = 412 STALE_WRITE; (4) kontrol positif: kunci baru + If-Match terkini = berhasil.
If-Match membaca lewat db_pool sendiri -> diarahkan ke koneksi transaksi YANG SAMA (tanpa itu baris draf tak terlihat = no-op diam).
Kontrol merah (WAJIB FAIL): merah_ifmatch = kode NYATA dari SALINAN pohon dengan If-Match kembali SEBELUM replay -> (2) jadi 412."""
import asyncio, os, sys
from decimal import Decimal as D
from uuid import UUID, uuid4
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

T = "kaos-biru-konveksi"
gagal = []


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:300]}"), flush=True)
    if not ok:
        gagal.append(nama)


async def main():
    from app.routers import vendor_deposits as RV
    from app.schemas.vendor_deposits import VendorDepositCreate, VendorDepositUpdate
    from app.utils.tanggal_tenant import tanggal_dokumen
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
    import app.services.db_pool as DBP
    DBP.get_db_pool = gpa  # If-Match (optimistic_concurrency) membaca lewat db_pool -> transaksi yang SAMA

    def req(kunci=None, if_match=None):
        async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
        hdr = ([(b"x-idempotency-key", kunci.encode())] if kunci else []) + ([(b"if-match", if_match.encode())] if if_match else [])
        r = Request({"type": "http", "method": "PATCH", "path": "/", "headers": hdr, "query_string": b""}, receive)
        r.state.user = {"tenant_id": T, "user_id": str(uid), "role": "OWNER", "username": "TES E2E"}
        return r

    async def galat(coro):
        try:
            return None, await coro
        except HTTPException as e:
            return e, None

    def dt(x): return x.model_dump(mode="json") if hasattr(x, "model_dump") else x
    luar = c.transaction()
    await luar.start()
    try:
        hari = await tanggal_dokumen(c, T)
        vendor = await c.fetchval("SELECT id FROM vendors WHERE tenant_id = $1 ORDER BY name LIMIT 1", T)
        bank = await c.fetchval("SELECT id FROM bank_accounts WHERE tenant_id = $1 AND is_active ORDER BY is_default DESC NULLS LAST, created_at LIMIT 1", T)
        r = dt(await RV.create_vendor_deposit(req(), VendorDepositCreate(deposit_date=hari, vendor_id=vendor, amount=D("10000"), bank_account_id=bank,
                                                                        reference="TES E2E AS-GP4b", notes="TES E2E AS-GP4b")))
        u = UUID(str(r.get("id") or r["data"]["id"]))
        await c.execute("UPDATE vendor_deposits SET updated_at = now() - interval '1 hour' WHERE id = $1 AND tenant_id = $2", u, T)
        v1 = (await c.fetchval("SELECT updated_at FROM vendor_deposits WHERE id = $1", u)).isoformat()
        k, h = f"TES-E2E-{uuid4()}", Response()
        e1, r1 = await galat(RV.update_vendor_deposit(req(k, v1), u, VendorDepositUpdate(notes="TES E2E AS-GP4b ubah"), Response()))
        v2 = (await c.fetchval("SELECT updated_at FROM vendor_deposits WHERE id = $1", u)).isoformat()
        cek(f"(1) PATCH kunci K + If-Match v1 = berhasil; updated_at maju (v1 {v1} -> v2 {v2})", r1 is not None and v2 != v1, (e1 and e1.detail, v1, v2))
        e2, r2 = await galat(RV.update_vendor_deposit(req(k, v1), u, VendorDepositUpdate(notes="TES E2E AS-GP4b ubah"), h))
        cek("(2) ulang kunci K + If-Match v1 BASI = jawaban PERTAMA + X-Idempotent-Replay (bukan 412)",
            r2 is not None and dt(r2) == dt(r1) and h.headers.get("X-Idempotent-Replay") == "true", (e2 and (e2.status_code, e2.detail)))
        e3, _ = await galat(RV.update_vendor_deposit(req(f"TES-E2E-{uuid4()}", v1), u, VendorDepositUpdate(notes="TES E2E AS-GP4b lain"), Response()))
        cek("(3) kunci baru + If-Match v1 basi = 412 STALE_WRITE", e3 is not None and e3.status_code == 412, e3 and (e3.status_code, e3.detail))
        e4, r4 = await galat(RV.update_vendor_deposit(req(f"TES-E2E-{uuid4()}", v2), u, VendorDepositUpdate(notes="TES E2E AS-GP4b terkini"), Response()))
        cek("(4) kontrol positif: kunci baru + If-Match terkini = berhasil", r4 is not None, e4 and (e4.status_code, e4.detail))
    finally:
        await luar.rollback()
        sisa = await c.fetchval("SELECT count(*) FROM vendor_deposits WHERE tenant_id = $1 AND reference = 'TES E2E AS-GP4b'", T)
        cek("rollback: nol UM TES E2E AS-GP4b", sisa == 0, sisa)
        await c.close()
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
