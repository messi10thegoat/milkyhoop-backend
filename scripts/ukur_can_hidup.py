"""Sesudah restart: can() nyata untuk beberapa sel modul baru (peran uji non-owner) — buktikan matriks TERBACA engine.
Baca-saja."""
import asyncio
import os
import sys

sys.path.insert(0, "/app/backend/api_gateway")
sys.path.insert(0, "/app")
import asyncpg  # noqa: E402

T = "kaos-biru-konveksi"
CEK = [  # (peran, aksi, modul, harap)
    ("CASHIER", "C", "expense", True), ("CASHIER", "V", "expense", False),
    ("ACCOUNTANT", "C", "period", False), ("ACCOUNTANT", "R", "period", True),
    ("FINANCE_MGR", "P", "period", True),
    ("VIEWER", "R", "fixed_asset", True), ("VIEWER", "C", "fixed_asset", False),
    ("COLLABORATOR", "R", "employee", False), ("HR_PAYROLL", "C", "employee", True),
    ("SALES", "P", "credit_note", False), ("SALES", "C", "credit_note", True),
    ("FINANCE_MGR", "A", "budget", True), ("ACCOUNTANT", "A", "budget", False),
    ("OWNER", "V", "expense", True), ("OWNER", "V", "period", True),
]


async def main():
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
    import backend.api_gateway.app.services.policy_engine_client as pec
    pec.init_policy_engine(pool)
    eng = pec.get_policy_engine()
    hasil = []
    for peran, aksi, modul, harap in CEK:
        uid = await pool.fetchval("""SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id=utr.role_id
            WHERE r.code=$1 AND utr.status='ACTIVE' LIMIT 1""", peran)
        if not uid:
            # peran tak punya user aktif di DB: bangun context sintetis via role langsung
            rid = await pool.fetchval("SELECT id FROM roles WHERE code=$1", peran)

            class Ctx:
                membership_active = True
                business_role_id = rid
                business_role_code = peran
                user_id = "00000000-0000-4000-8000-000000000000"
                tenant_id = T
                subscription_role = "ADMIN"
            got = await eng.can(Ctx(), aksi, modul)
        else:
            ctx = await eng.get_user_context(user_id=str(uid), tenant_id=T, subscription_role="ADMIN")
            got = await eng.can(ctx, aksi, modul)
        ok = got == harap
        hasil.append((ok, f"{peran} can({aksi},{modul}) == {harap}", got))
    await pool.close()
    for ok, u, got in hasil:
        print(("[H] " if ok else "[X] ") + f"{u}  | got={got}")
    g = sum(1 for h in hasil if not h[0])
    print(f"\ngagal={g} total={len(hasil)}")
    sys.exit(0 if g == 0 else 1)


asyncio.run(main())
