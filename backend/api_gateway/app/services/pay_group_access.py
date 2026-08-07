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
