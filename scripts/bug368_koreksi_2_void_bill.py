"""BUG-368 (2): void 4 bill uji kaos lewat LOGIKA RESMI BillsService.void_bill (jurnal balik + AP + ledger), SATU transaksi per bill (di dalam servis).
UNTUK DITINJAU MASTER; JANGAN jalankan sebelum MASTER OK. Jalankan DI DALAM kontainer api_gateway:
    docker exec -i milkyhoop-dev-api_gateway python - < scripts/bug368_koreksi_2_void_bill.py          (dry-run: hanya daftar + prasyarat)
    docker exec -i milkyhoop-dev-api_gateway env APPLY=1 python - < scripts/bug368_koreksi_2_void_bill.py (eksekusi)
Hanya 4 jurnal: PJ-2609-0008/0009/0010/0021 (BILL, 2026-09-02, tanpa baris ledger). Bill lain TIDAK disentuh."""
import asyncio, os, sys
sys.path.insert(0, "/app/backend/api_gateway")
TENANT = "kaos-biru-konveksi"
JURNAL = ("PJ-2609-0008", "PJ-2609-0009", "PJ-2609-0010", "PJ-2609-0021")
APPLY = os.environ.get("APPLY") == "1"


async def main():
    from app.routers.bills import get_bills_service
    from app.db import get_pool  # noqa: F401  (pola get_bills_service)
    svc = await get_bills_service()
    async with svc.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT je.journal_number, je.source_id AS bill_id, b.status, b.created_by FROM journal_entries je JOIN bills b ON b.id::text=je.source_id::text AND b.tenant_id=je.tenant_id "
            "WHERE je.tenant_id=$1 AND je.source_type='BILL' AND je.journal_number = ANY($2::text[]) AND je.reversed_by_id IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM inventory_ledger il WHERE il.journal_id=je.id) ORDER BY 1", TENANT, list(JURNAL))
        pemakai = await conn.fetchval("SELECT id FROM users WHERE tenant_id=$1 AND is_active ORDER BY created_at LIMIT 1", TENANT)
    assert len(rows) == 4, f"diharapkan 4 bill, dapat {len(rows)}"
    for r in rows:
        print(r["journal_number"], r["bill_id"], r["status"], "APPLY" if APPLY else "dry-run")
        if APPLY and r["status"] != "void":
            res = await svc.void_bill(TENANT, r["bill_id"], {"reason": "Koreksi BUG-368: bill uji tanpa gerak persediaan"}, pemakai)
            print("  ->", {k: res.get(k) for k in ("success", "message")})
            assert res.get("success"), res


asyncio.run(main())
