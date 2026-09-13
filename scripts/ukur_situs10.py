"""Situs 10 — bill_payments.create_bill_payment: alokasi ke tagihan tanpa pemeriksaan vendor & tenant.

DUA uji terpisah, masing-masing dgn kontrol. Handler nyata, konteks tenant A (kaos-biru), ROLLBACK.
  KONTROL  vendor SAMA, tagihan tenant A sendiri -> harus 200 dan menulis alokasi
  UJI-V    lintas VENDOR: pembayaran vendor X, tagihan vendor Y (tenant A)
  UJI-T    lintas TENANT: pembayaran tenant A, tagihan tenant B (grapgrap)
Untuk tiap penolakan: status + detail + LAPIS penolak (dibaca dari pesan & dari efek).
Nol baris menetap di KEDUA tenant (cacah alokasi, pembayaran, jurnal per tenant).
"""
import asyncio
import os
import sys
from datetime import date

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402
from starlette.requests import Request  # noqa: E402

A, B = "kaos-biru-konveksi", "grapgrap-manado"
HARAP = 8
hasil = []


def catat(uji, ok, ket=""):
    hasil.append((uji, bool(ok), str(ket)[:320]))


def req(tenant, uid):
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b"",
                    "state": {"user": {"tenant_id": tenant, "user_id": str(uid)}}})


async def cacah(conn):
    return (await conn.fetchval("SELECT count(*) FROM bill_payment_allocations"),
            await conn.fetchval("SELECT count(*) FROM bill_payments_v2 WHERE tenant_id=$1", A),
            await conn.fetchval("SELECT count(*) FROM bill_payments_v2 WHERE tenant_id=$1", B),
            await conn.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id=$1", A),
            await conn.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id=$1", B))


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    import app.services.db_pool as dbp

    class FakePool:
        def acquire(self, *a, **k):
            class C:
                async def __aenter__(s):
                    return conn
                async def __aexit__(s, *e):
                    return False
            return C()
        async def release(self, c):
            return None

    async def fake(*a, **k):
        return FakePool()
    dbp.get_db_pool = fake

    from app.routers import bill_payments as bp
    from app.schemas.bill_payments import CreateBillPaymentRequest as P, BillAllocationInput as Al

    uid = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", A)
    ba = await conn.fetchval("SELECT id FROM bank_accounts WHERE tenant_id=$1 AND is_active ORDER BY account_name LIMIT 1", A)
    awal = await cacah(conn)

    luar = conn.transaction(); await luar.start()
    try:
        tag_a = await conn.fetchrow(
            """SELECT a.bill_id, b.vendor_id FROM compute_ap_outstanding($1) a JOIN bills b ON b.id=a.bill_id
               WHERE a.bill_id IS NOT NULL AND a.outstanding >= 1000 ORDER BY a.bill_id LIMIT 1""", A)
        vendor_lain = await conn.fetchval(
            "SELECT id FROM vendors WHERE tenant_id=$1 AND id <> $2 ORDER BY id LIMIT 1", A, tag_a["vendor_id"])
        tag_b = await conn.fetchrow(
            "SELECT id, vendor_id, status_v2, journal_id IS NOT NULL AS berjurnal FROM bills WHERE tenant_id=$1 ORDER BY journal_id IS NULL, id LIMIT 1", B)
        catat("subjek: tagihan A ber-outstanding, vendor lain di A, tagihan B",
              bool(tag_a and vendor_lain and tag_b), f"A={tag_a and dict(tag_a)} vendor_lain={vendor_lain} B={tag_b and dict(tag_b)}")

        async def coba(label, vendor_id, bill_id):
            sp = conn.transaction(); await sp.start()
            c0 = await cacah(conn)
            try:
                body = P(vendor_id=str(vendor_id), payment_date=date.today(), bank_account_id=str(ba),
                         total_amount=1000, allocations=[Al(bill_id=str(bill_id), amount_applied=1000)], save_as_draft=True)
                await bp.create_bill_payment(req(A, uid), body)
                kode, det = 200, "OK"
            except Exception as e:  # noqa: BLE001
                kode, det = getattr(e, "status_code", type(e).__name__), getattr(e, "detail", str(e))
            c1 = await cacah(conn)
            alok = await conn.fetchval("SELECT count(*) FROM bill_payment_allocations WHERE bill_id=$1", bill_id)
            await sp.rollback()
            return kode, det, [y - x for x, y in zip(c0, c1)], alok

        k, d, delta, al = await coba("KONTROL", tag_a["vendor_id"], tag_a["bill_id"])
        catat("KONTROL vendor SAMA, tagihan A -> 200 dan menulis alokasi", k == 200 and delta[0] == 1, f"{k} {d} delta={delta}")

        k, d, delta, al = await coba("UJI-V", vendor_lain, tag_a["bill_id"])
        catat("UJI-V lintas VENDOR: kode", True, f"{k} {d}")
        catat("UJI-V lintas VENDOR: MENULIS alokasi? (delta alokasi)", True, f"delta={delta}  <- [alokasi, bp A, bp B, jurnal A, jurnal B]")

        k, d, delta, al = await coba("UJI-T", tag_a["vendor_id"], tag_b["id"])
        catat("UJI-T lintas TENANT: kode", True, f"{k} {d}")
        catat("UJI-T lintas TENANT: MENULIS alokasi/menyentuh tenant B? (delta)", True,
              f"delta={delta}  <- [alokasi, bp A, bp B, jurnal A, jurnal B]")
        catat("UJI-T lintas TENANT DITOLAK tanpa tulis", k != 200 and delta[0] == 0 and delta[2] == 0 and delta[4] == 0,
              f"{k} {d} delta={delta}")
    finally:
        await luar.rollback()

    akhir = await cacah(conn)
    catat("KONTROL nol menetap di KEDUA tenant", awal == akhir, f"{awal} -> {akhir}")
    await conn.close()
    for u, ok, k in hasil:
        print(("[H] " if ok else "[X] ") + f"{u}  | {k}")
    print(f"total={len(hasil)} harap={HARAP} -> {'LENGKAP' if len(hasil) == HARAP else 'TAK SAH'}")


asyncio.run(main())
