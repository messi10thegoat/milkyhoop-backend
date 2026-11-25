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


class SatuSambungan:
    """Pool palsu: semua acquire() memberi SATU koneksi yang sama (di dalam transaksi luar). conn.transaction() di dalam servis jadi savepoint."""
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self_):
                return conn

            async def __aexit__(self_, *a):
                return False
        return _Ctx()

    def __getattr__(self, nama):  # fetch/fetchrow/fetchval/execute langsung ke koneksi
        return getattr(self.conn, nama)


class Batal(Exception):
    pass


async def main():
    from app.services.bills_service import BillsService
    from app.routers.bills import get_bills_service
    asli = await get_bills_service()
    async with asli.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT je.journal_number, je.source_id AS bill_id, b.status, b.created_by FROM journal_entries je JOIN bills b ON b.id::text=je.source_id::text AND b.tenant_id=je.tenant_id "
            "WHERE je.tenant_id=$1 AND je.source_type='BILL' AND je.journal_number = ANY($2::text[]) AND je.reversed_by_id IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM inventory_ledger il WHERE il.journal_id=je.id) ORDER BY 1", TENANT, list(JURNAL))
        pemakai = await conn.fetchval("SELECT created_by FROM bills WHERE tenant_id=$1 AND created_by IS NOT NULL GROUP BY 1 ORDER BY count(*) DESC LIMIT 1", TENANT)  # pengguna pembuat bill kaos (tabel pengguna = "User", bukan users)
    assert len(rows) == 4, f"diharapkan 4 bill, dapat {len(rows)}"
    print("pemakai servis:", pemakai)
    if APPLY:
        svc = asli
        for r in rows:
            print(r["journal_number"], r["bill_id"], r["status"], "APPLY")
            if r["status"] != "void":
                res = await svc.void_bill(TENANT, r["bill_id"], {"reason": "Koreksi BUG-368: bill uji tanpa gerak persediaan"}, pemakai)
                print("  ->", {k: res.get(k) for k in ("success", "message")})
                assert res.get("success"), res
        return
    # DRY-RUN SUNGGUHAN: satu koneksi, transaksi luar, SELALU di-rollback (bukan sekadar flag)
    async with asli.pool.acquire() as conn:
        try:
            async with conn.transaction():
                svc = BillsService(SatuSambungan(conn), accounting_facade=None)
                for r in rows:
                    res = await svc.void_bill(TENANT, r["bill_id"], {"reason": "dry-run BUG-368"}, pemakai)
                    print(r["journal_number"], r["status"], "->", {k: res.get(k) for k in ("success", "message")})
                sisa = await conn.fetchval(
                    "SELECT (SELECT sum((quantity_in-quantity_out)*unit_cost) FROM inventory_ledger WHERE tenant_id=$1) - "
                    "(SELECT sum(jl.debit-jl.credit) FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id "
                    "WHERE je.tenant_id=$1 AND je.status='POSTED' AND c.account_code='1-10600')", TENANT)
                print("selisih ledger-GL DI DALAM transaksi dry-run:", sisa)
                raise Batal()
        except Batal:
            print("DRY-RUN: transaksi luar di-ROLLBACK; tidak ada yang tertulis.")


asyncio.run(main())
