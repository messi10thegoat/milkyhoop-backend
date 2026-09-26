"""Pay Group Access Helper — resolves which pay groups a user can access."""
import logging

logger = logging.getLogger(__name__)


async def get_accessible_pay_group_ids(
    user_id: str, tenant_id: str, role_code: str, conn
) -> list:
    """Get pay group IDs this user can access.

    OWNER/ADMIN = all active groups (bypass).
    Others = only groups in user_pay_group_access where revoked_at IS NULL.
    Empty list = no payroll access.
    """
    if role_code in ("OWNER", "ADMIN"):
        rows = await conn.fetch(
            "SELECT id FROM pay_groups WHERE tenant_id = $1 AND is_active = true",
            tenant_id,
        )
        return [str(r["id"]) for r in rows]

    rows = await conn.fetch(
        """SELECT pay_group_id FROM user_pay_group_access
           WHERE user_id = $1 AND tenant_id = $2 AND revoked_at IS NULL""",
        user_id,
        tenant_id,
    )
    return [str(r["pay_group_id"]) for r in rows]


async def get_user_role_code(user_id: str, tenant_id: str, conn) -> str:
    """Kode peran bisnis, atau "VIEWER" bila tak ada keanggotaan aktif.

    Fungsi INI sudah benar sebelum batch 2026-08-07 (satu-satunya pembaca yang
    memakai LOWER). Sekarang ia memanggil query kanonik supaya definisi "peran
    aktif" hidup di SATU tempat — perilakunya untuk owner sehat TIDAK berubah,
    dan itu diuji eksplisit.

    ⚠️ Fallback "VIEWER" DIPERTAHANKAN DI SINI, sengaja, berbeda dari auth.py.
    Ini jalur PEMBATASAN (filter payroll), bukan jalur pemberian akses: menebak
    ke peran paling sempit di sini mempersempit, tidak membuka. Mengubahnya
    jadi raise akan mengubah perilaku endpoint payroll yang belum ada dalam
    lingkup batch ini dan belum punya ujinya. Lihat catatan di
    DOCS/issues/BE-AUTH-ROLE-LOOKUP-CASE-MISMATCH-MASKING-001.md.
    """
    from .role_resolution import try_resolve_business_role

    return await try_resolve_business_role(conn, user_id, tenant_id) or "VIEWER"


async def employee_in_scope(conn, tenant_id: str, user_id, employee_id) -> bool:
    """True if the caller may see/act on this employee: their pay-group is accessible
    (OWNER/ADMIN always). Unassigned employees (NULL pay_group) are OWNER/ADMIN-only.
    RULE (privacy): EVERY endpoint taking an employee_id — by-id or nested, READ OR WRITE —
    must gate on this. The list views filtering is NOT the guarantee (see payroll/team-access doc)."""
    role = await get_user_role_code(str(user_id), tenant_id, conn)
    if role in ("OWNER", "ADMIN"):
        return True
    accessible = set(await get_accessible_pay_group_ids(str(user_id), tenant_id, role, conn))
    if not accessible:
        return False
    pg = await conn.fetchval(
        "SELECT pay_group_id FROM employees WHERE id = $1 AND tenant_id = $2", employee_id, tenant_id
    )
    return pg is not None and str(pg) in accessible


async def accessible_pay_group_filter(conn, tenant_id: str, user_id):
    """For LIST endpoints. Returns (is_all, ids): OWNER/ADMIN -> (True, None) no filter;
    limited -> (False, [pay_group_ids]) (possibly empty = sees none)."""
    role = await get_user_role_code(str(user_id), tenant_id, conn)
    if role in ("OWNER", "ADMIN"):
        return True, None
    return False, await get_accessible_pay_group_ids(str(user_id), tenant_id, role, conn)


async def run_dalam_cakupan(conn, tenant_id: str, user_id, run_id) -> bool:
    """Run gaji SELURUHNYA dalam cakupan pay-group pemanggil (26 Sep 2026, audit pay-group PG1/PG2).

    OWNER/ADMIN -> ya. Selain itu SETIAP karyawan run (payroll_run_employees UNION
    payroll_slip_lines) wajib ada, ber-grup, dan grupnya dapat diakses. Sengaja
    gagal TERTUTUP:
      - run KOSONG (0 karyawan/slip) -> TIDAK: tak bisa dibuktikan dalam cakupan
        (run tak menyimpan pay_group_id; "kosong = boleh" = pintas).
      - karyawan run yang barisnya tak ditemukan / tanpa grup -> TIDAK.
    Grup = grup karyawan SAAT INI (run/slip tak menyimpan snapshot grup) -- sama
    dengan GET /payroll/{id} dan /slips yang sudah tayang.
    """
    uid = str(user_id) if user_id else None
    if not uid:
        return False
    role = await get_user_role_code(uid, str(tenant_id), conn)
    if role in ("OWNER", "ADMIN"):
        return True
    boleh = set(await get_accessible_pay_group_ids(uid, str(tenant_id), role, conn))
    if not boleh:
        return False
    ids = await conn.fetch(
        """SELECT employee_id FROM payroll_run_employees WHERE payroll_id = $1
           UNION SELECT employee_id FROM payroll_slip_lines WHERE payroll_id = $1""",
        run_id,
    )
    ids = [r["employee_id"] for r in ids if r["employee_id"] is not None]
    if not ids:
        return False
    rows = await conn.fetch(
        "SELECT id, pay_group_id FROM employees WHERE tenant_id = $1 AND id = ANY($2::uuid[])",
        str(tenant_id), ids,
    )
    grup = {str(r["id"]): r["pay_group_id"] for r in rows}
    return all(grup.get(str(e)) is not None and str(grup[str(e)]) in boleh for e in ids)
