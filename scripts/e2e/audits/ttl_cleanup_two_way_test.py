#!/usr/bin/env python3
"""UJI DUA ARAH TTL cleanup (Law 33) — dijalankan di DB SCRATCH, bukan live."""
import asyncio, sys, os
sys.path.insert(0, "/app/backend/api_gateway")
from app.services.idempotency_cleanup import cleanup_tick  # noqa: E402
import asyncpg  # noqa: E402

DSN = os.environ["TEST_DSN"]

ROWS = [
    # (key, expires_at SQL, harus_terhapus?)
    ("keep-valid",        "NOW() + interval '24 hours'",  False),  # masih berlaku
    ("keep-grace-1d",     "NOW() - interval '1 day'",     False),  # kedaluwarsa TAPI dalam grace
    ("keep-grace-6d",     "NOW() - interval '6 days'",    False),  # tepat di dalam grace
    ("del-8d",            "NOW() - interval '8 days'",    True),   # lewat grace
    ("del-30d",           "NOW() - interval '30 days'",   True),   # jauh lewat grace
]


async def main():
    conn = await asyncpg.connect(DSN)
    await conn.execute("DELETE FROM idempotency_keys WHERE key LIKE 'keep-%' OR key LIKE 'del-%'")
    for k, exp, _ in ROWS:
        await conn.execute(
            f"INSERT INTO idempotency_keys (key, tenant_id, source_type, result, expires_at) "
            f"VALUES ($1, 'tenant-uji', 'TEST', '{{}}', {exp})", k)
    before = await conn.fetchval("SELECT count(*) FROM idempotency_keys")
    print(f"disiapkan: {before} baris")
    await conn.close()

    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
    n = await cleanup_tick(pool)
    print(f"cleanup_tick menghapus: {n}")
    await pool.close()

    conn = await asyncpg.connect(DSN)
    sisa = {r["key"] for r in await conn.fetch(
        "SELECT key FROM idempotency_keys WHERE key LIKE 'keep-%' OR key LIKE 'del-%'")}
    await conn.close()

    ok = True
    for k, _, harus_hapus in ROWS:
        ada = k in sisa
        lulus = (not ada) if harus_hapus else ada
        ok &= lulus
        arah = "HARUS TERHAPUS" if harus_hapus else "HARUS BERTAHAN"
        print(f"  {'PASS' if lulus else '!!! FAIL'}  {k:15} {arah:15} -> {'ada' if ada else 'hilang'}")

    # uji idempoten / tahan-restart: jalankan lagi, tak boleh merusak apa pun
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
    n2 = await cleanup_tick(pool)
    await pool.close()
    lulus2 = (n2 == 0)
    ok &= lulus2
    print(f"  {'PASS' if lulus2 else '!!! FAIL'}  jalan-ulang (simulasi restart) menghapus={n2} (harus 0)")

    print("\nHASIL:", "SEMUA LULUS" if ok else "!!! ADA YANG GAGAL")
    sys.exit(0 if ok else 1)

asyncio.run(main())
