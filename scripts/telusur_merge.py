"""TELUSUR (bukan gerbang) POST /api/customers/merge — semua lapis. Satu transaksi luar, ROLLBACK. Nol tulis bertahan.
argv[1] = lapis tambalan kumulatif yang diterapkan ke SALINAN modul (0 = kode hidup). Tiap lapis hanya menambal dinding
yang TERUKUR di lapis sebelumnya; galat bergeser dicatat, tidak ditambal tanpa melihat.
Sesudah 200: ukur apa yang BERPINDAH vs apa yang TERTINGGAL di semua tabel ber-customer_id.
"""
import asyncio
import importlib.util
import json
import os
import sys
import traceback

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402
from starlette.requests import Request  # noqa: E402

LAPIS = int(sys.argv[1])
T, TB = "kaos-biru-konveksi", "grapgrap-manado"
SRC = "/app/backend/api_gateway/app/routers/customers.py"
TABEL = ["accounts_receivable", "cheques", "credit_notes", "customer_activities", "customer_deposits", "customer_price_lists",
         "item_serials", "production_orders", "proformas", "quotes", "receive_payments", "sales_invoices",
         "sales_orders", "sales_receipts", "table_reservations"]

TAMBAL = [
    # lapis 1: deleted_by varchar menerima UUID
    ('"UPDATE customers SET is_active = false, deleted_at = NOW(), deleted_by = $3 WHERE id = ANY($1) AND tenant_id = $2",\n                    source_ids,\n                    ctx["tenant_id"],\n                    ctx["user_id"],',
     '"UPDATE customers SET is_active = false, deleted_at = NOW(), deleted_by = $3 WHERE id = ANY($1::uuid[]) AND tenant_id = $2",\n                    source_ids,\n                    ctx["tenant_id"],\n                    str(ctx["user_id"]),'),
]


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
    src = open(SRC, encoding="utf-8").read()
    for lama, baru in TAMBAL[:LAPIS]:
        print("tambal lapis cocok", src.count(lama), "x")
        src = src.replace(lama, baru)
    open("/tmp/cust_telusur.py", "w", encoding="utf-8").write(src)
    import app.routers  # noqa: F401
    spec = importlib.util.spec_from_file_location("app.routers.cust_telusur", "/tmp/cust_telusur.py")
    m = importlib.util.module_from_spec(spec); sys.modules[spec.name] = m; spec.loader.exec_module(m)

    uid = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)
    # sumber = pelanggan dengan dokumen terbanyak lintas tabel; sasaran = pelanggan aktif lain
    sumber = await conn.fetchval("""SELECT c.id FROM customers c WHERE c.tenant_id=$1 AND c.deleted_at IS NULL
        ORDER BY (SELECT count(*) FROM sales_invoices s WHERE s.customer_id=c.id) + (SELECT count(*) FROM customer_deposits d WHERE d.customer_id=c.id)
               + (SELECT count(*) FROM credit_notes n WHERE n.customer_id=c.id) DESC LIMIT 1""", T)
    sasaran = await conn.fetchval("SELECT id FROM customers WHERE tenant_id=$1 AND deleted_at IS NULL AND id<>$2 ORDER BY created_at LIMIT 1", T, sumber)

    async def cacah(cid):
        hasil = {}
        for t in TABEL:
            try:
                hasil[t] = await conn.fetchval(f"SELECT count(*) FROM {t} WHERE customer_id=$1", cid)
            except asyncpg.PostgresError as e:
                hasil[t] = f"GALAT {e.sqlstate}"
        return hasil

    def rq(body):
        async def recv():
            return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
        return Request({"type": "http", "method": "POST", "path": "/", "headers": [(b"content-type", b"application/json")], "query_string": b"",
                        "state": {"user": {"tenant_id": T, "user_id": str(uid)}}}, recv)

    luar = conn.transaction(); await luar.start()
    try:
        c_sumber, c_sasaran = await cacah(sumber), await cacah(sasaran)
        print("sumber", sumber, {k: v for k, v in c_sumber.items() if v})
        print("sasaran", sasaran, {k: v for k, v in c_sasaran.items() if v})
        try:
            r = await m.merge_customers(rq({"source_ids": [str(sumber)], "target_id": str(sasaran)}))
            print("MERGE:", r)
        except Exception as e:  # noqa: BLE001
            print(f"MERGE GAGAL: {type(e).__name__}: {getattr(e, 'detail', e)}")
        # galat asli dari logger handler
        s2, t2 = await cacah(sumber), await cacah(sasaran)
        print("TERTINGGAL di sumber:", {k: v for k, v in s2.items() if v})
        print("sasaran sesudah:", {k: v for k, v in t2.items() if v})
        print("status sumber:", dict(await conn.fetchrow("SELECT is_active, deleted_at IS NOT NULL dihapus, deleted_by FROM customers WHERE id=$1", sumber)))
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    finally:
        await luar.rollback()
        await conn.close()


asyncio.run(main())
