"""GERBANG V252 FK komposit pelanggan. Satu transaksi luar, ROLLBACK. Hasil di memori.
argv: baru (badan dipasang di transaksi) | lama (tanpa badan) | hidup (sudah live)
Perilaku: UPDATE customer_id sebuah baris -> pelanggan TENANT LAIN => 23503 (baru/hidup) / LOLOS (lama).
UPDATE -> pelanggan tenant SAMA => sukses. Skema: 8 FK komposit ada, 4 FK simpel lama hilang.
Handler: buat faktur pelanggan sah -> 200; gabung pelanggan in-tenant -> 200.
"""
import asyncio
import os
import sys

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402

MODE = sys.argv[1]
BADAN = "/tmp/V252_badan.sql"
T, TB = "kaos-biru-konveksi", "grapgrap-manado"
BER_BARIS = ["sales_invoices", "receive_payments", "accounts_receivable", "proformas", "quotes", "sales_orders"]
SEMUA = BER_BARIS + ["sales_receipts"]  # recurring_invoices dihapus V292
hasil = []


def catat(s, u, ok, k=""):
    hasil.append((s, u, bool(ok), str(k)[:160]))


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    tr = conn.transaction(); await tr.start()
    try:
        if MODE == "baru":
            await conn.execute(open(BADAN, encoding="utf-8").read())

        # skema
        for t in SEMUA:
            komp = await conn.fetchval("""SELECT count(*) FROM pg_constraint WHERE conrelid=$1::regclass AND contype='f'
                AND confrelid='customers'::regclass AND conkey @> ARRAY[
                  (SELECT attnum FROM pg_attribute WHERE attrelid=$1::regclass AND attname='customer_id'),
                  (SELECT attnum FROM pg_attribute WHERE attrelid=$1::regclass AND attname='tenant_id')]::smallint[]""", t)
            catat("SKEMA", f"{t}: FK komposit (customer_id, tenant_id) ada", komp >= 1, komp)
        for t in ("sales_invoices", "receive_payments", "sales_receipts"):
            simpel = await conn.fetchval("SELECT count(*) FROM pg_constraint WHERE conname=$1", f"{t}_customer_id_fkey")
            catat("SKEMA", f"{t}: FK simpel lama hilang", simpel == 0, simpel)

        pelanggan_lain = await conn.fetchval("SELECT id FROM customers WHERE tenant_id=$1 LIMIT 1", TB)
        for t in BER_BARIS:
            row = await conn.fetchrow(f"SELECT id, customer_id FROM {t} WHERE tenant_id=$1 AND customer_id IS NOT NULL LIMIT 1", T)
            if not row:
                catat("PERILAKU", f"{t}: subjek baris ada", False, "tak ada baris"); continue
            # lintas tenant
            sp = conn.transaction(); await sp.start()
            try:
                await conn.execute(f"UPDATE {t} SET customer_id=$1 WHERE id=$2", pelanggan_lain, row["id"])
                got = "LOLOS"
            except asyncpg.PostgresError as e:
                got = e.sqlstate
            await sp.rollback()
            if MODE == "lama":
                catat("PERILAKU-LAMA", f"{t}: UPDATE lintas tenant LOLOS (celah)", got == "LOLOS", got)
            else:
                catat("PERILAKU", f"{t}: UPDATE pelanggan tenant lain -> 23503", got == "23503", got)
            # tenant sama (idempoten set ke nilai sekarang)
            sp = conn.transaction(); await sp.start()
            try:
                await conn.execute(f"UPDATE {t} SET customer_id=$1 WHERE id=$2", row["customer_id"], row["id"])
                ok2 = True
            except asyncpg.PostgresError:
                ok2 = False
            await sp.rollback()
            catat("PERILAKU", f"{t}: UPDATE pelanggan tenant sama -> sukses", ok2, "")

        # handler: buat faktur + gabung pelanggan (pakai FakePool + modul hidup)
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
        import app.routers  # noqa: F401
        from app.routers import sales_invoices as rsi, customers as rcu
        import app.schemas.sales_invoices as ssi
        from datetime import date, timedelta
        from starlette.requests import Request
        import json as _j
        uid = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)

        def rq(body=None):
            async def recv():
                return {"type": "http.request", "body": _j.dumps(body or {}).encode(), "more_body": False}
            return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b"",
                            "state": {"user": {"tenant_id": T, "user_id": str(uid)}}}, recv if body is not None else None)

        cust = await conn.fetchval("SELECT id FROM customers WHERE tenant_id=$1 AND deleted_at IS NULL LIMIT 1", T)
        item = await conn.fetchrow("SELECT id FROM products WHERE tenant_id=$1 LIMIT 1", T)
        wh = await conn.fetchval("SELECT id FROM warehouses WHERE tenant_id=$1 LIMIT 1", T)
        sp = conn.transaction(); await sp.start()
        try:
            body = ssi.CreateInvoiceRequest(customer_id=str(cust), customer_name="gerbang V252", invoice_date=date.today(),
                due_date=date.today() + timedelta(days=30), warehouse_id=str(wh) if wh else None,
                items=[ssi.InvoiceItemCreate(item_id=str(item["id"]), description="g", quantity=1, unit_price=1000)])
            r = await rsi.create_invoice(rq(), body)
            k = 200
        except Exception as e:  # noqa: BLE001
            k = getattr(e, "status_code", type(e).__name__), str(getattr(e, "detail", e))[:80]
        await sp.rollback()
        catat("HANDLER", "buat faktur pelanggan sah tenant sama -> 200 (FK komposit tak mengganggu)", k == 200, k)

        src2 = await conn.fetchval("""SELECT c.id FROM customers c WHERE c.tenant_id=$1 AND c.deleted_at IS NULL
            AND EXISTS (SELECT 1 FROM sales_invoices s WHERE s.customer_id=c.id) LIMIT 1""", T)
        dst2 = await conn.fetchval("SELECT id FROM customers WHERE tenant_id=$1 AND deleted_at IS NULL AND id<>$2 LIMIT 1", T, src2)
        sp = conn.transaction(); await sp.start()
        try:
            r = await rcu.merge_customers(rq({"source_ids": [str(src2)], "target_id": str(dst2)}))
            km = 200 if (isinstance(r, dict) and r.get("success")) else r
        except Exception as e:  # noqa: BLE001
            km = getattr(e, "status_code", type(e).__name__), str(getattr(e, "detail", e))[:80]
        await sp.rollback()
        catat("HANDLER", "gabung pelanggan in-tenant -> 200 (FK komposit meloloskan tenant sama)", km == 200, km)
    finally:
        await tr.rollback()
        sisa = await conn.fetchval("SELECT count(*) FROM pg_constraint WHERE conname='fk_sales_orders_customer_tenant'")
        await conn.close()
    if MODE == "baru":
        catat("ROLLBACK", "FK V252 tak menetap sesudah rollback", sisa == 0, sisa)

    for s, u, ok, k in hasil:
        print(("[H] " if ok else "[X] ") + f"{s:13} {u}  | {k}")
    g = [h[1] for h in hasil if not h[2]]
    print(f"\nMODE={MODE} gagal={len(g)} total={len(hasil)}")
    if MODE in ("baru", "hidup"):
        sys.exit(0 if not g else 1)
    ok = any("LOLOS (celah)" in x for x in g) is False and all(x for x in [True])  # lama: PERILAKU-LAMA harus HIJAU (lolos)
    merah_lama = [h for h in hasil if h[0] == "PERILAKU-LAMA" and not h[2]]
    print("[lama] ->", "CELAH LINTAS-TENANT TERBUKTI" if not merah_lama else "TAK SESUAI", [h[1] for h in merah_lama])
    sys.exit(0 if not merah_lama else 1)


asyncio.run(main())
