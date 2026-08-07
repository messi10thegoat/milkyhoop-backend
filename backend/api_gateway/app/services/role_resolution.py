"""Resolusi peran bisnis — SATU definisi, dipakai semua pembaca.

KENAPA MODUL INI ADA
--------------------
`user_tenant_roles.status` punya lima pembaca yang ditulis terpisah, dengan
TIGA predikat berbeda:

    pay_group_access.py:37   LOWER(utr.status) = 'active'   <- satu-satunya benar
    auth.py:237/263/501/565  status = 'active'              <- selalu MISS

Baris nyata memakai 'ACTIVE' (DEFAULT kolom, V195:232), sehingga keempat
predikat di auth.py TIDAK PERNAH cocok. Dibuktikan dengan query:
`SELECT count(*) FROM user_tenant_roles WHERE status='active'` -> 0.

Akibatnya login tak pernah mem-*lookup* peran; ia jatuh ke cabang tebakan
"tenant cocok -> OWNER". Peran BENDAHARA login sebagai OWNER; anggota yang
DIHAPUS login sebagai OWNER; anggota SUSPENDED login sebagai OWNER.

ATURAN MODUL INI
----------------
1. Satu query kanonik. Lima call-site memanggilnya, bukan menulis ulang.
2. Ketidaktahuan TIDAK dijawab dengan tebakan — ke atas maupun ke bawah.
   Tak ada baris  -> 409 ROLE_NOT_PROVISIONED (bukan OWNER, bukan VIEWER)
   Baris nonaktif -> 403 ROLE_INACTIVE
3. 409, bukan 403, untuk "tak ada baris": 403 berarti "kamu tak boleh", dan
   itu klaim otorisasi yang justru tak bisa kita buat — kita tak tahu.
   409 = konflik keadaan yang bisa ditindaklanjuti.

Lihat DOCS/issues/BE-AUTH-ROLE-LOOKUP-CASE-MISMATCH-MASKING-001.md
    + DOCS/issues/BE-TEAM-REVOCATION-ESCALATES-TO-OWNER-001.md
"""

from typing import Optional

from fastapi import HTTPException, Request

# Bentuk kanonik = DEFAULT kolom di skema. Kode TIDAK memilih; skema yang
# memutuskan. Pembacaan tetap case-insensitive supaya baris lama tak putus.
CANONICAL_ACTIVE = "ACTIVE"

MSG_NOT_PROVISIONED = (
    "Akun Anda belum memiliki peran di bisnis ini. Hubungi pemilik bisnis "
    "untuk diberikan akses, atau hubungi dukungan MilkyHoop bila Anda pemiliknya."
)
MSG_INACTIVE = (
    "Akses Anda ke bisnis ini sedang dinonaktifkan. Hubungi pemilik bisnis."
)

_ROLE_SQL = """
    SELECT r.code AS role_code, utr.status AS status
    FROM user_tenant_roles utr
    JOIN roles r ON r.id = utr.role_id
    WHERE utr.user_id = $1::uuid
      AND utr.tenant_id = $2
      AND r.is_active = TRUE
    ORDER BY utr.is_primary DESC
    LIMIT 1
"""


async def fetch_role_row(conn, user_id: str, tenant_id: str):
    """Baris peran mentah, atau None. Nol keputusan kebijakan di sini."""
    return await conn.fetchrow(_ROLE_SQL, str(user_id), tenant_id)


def is_active_status(status: Optional[str]) -> bool:
    """Case-insensitive DENGAN SENGAJA.

    Data lama bisa memuat 'active' (ditulis invite_public sebelum diseragamkan).
    Constraint menjaga tulisan BARU; pembacaan tetap toleran supaya perbaikan
    ini tak mengunci siapa pun yang barisnya lahir sebelum hari ini.
    """
    return (status or "").strip().upper() == CANONICAL_ACTIVE


async def resolve_business_role(conn, user_id: str, tenant_id: str) -> str:
    """Kode peran bisnis, atau HTTPException. TIDAK PERNAH menebak.

    Raises:
        409 ROLE_NOT_PROVISIONED — tak ada baris peran
        403 ROLE_INACTIVE        — ada baris tapi tidak aktif
    """
    row = await fetch_role_row(conn, user_id, tenant_id)
    if row is None:
        raise HTTPException(
            status_code=409,
            detail={"error_code": "ROLE_NOT_PROVISIONED", "message": MSG_NOT_PROVISIONED},
        )
    if not is_active_status(row["status"]):
        raise HTTPException(
            status_code=403,
            detail={"error_code": "ROLE_INACTIVE", "message": MSG_INACTIVE},
        )
    return row["role_code"]


async def try_resolve_business_role(conn, user_id: str, tenant_id: str) -> Optional[str]:
    """Varian non-raising untuk jalur yang HARUS meneruskan (mis. daftar tenant).

    Mengembalikan None untuk 'tak ada' MAUPUN 'tidak aktif' — pemanggil wajib
    memperlakukan None sebagai "tidak boleh", bukan sebagai "boleh apa saja".
    """
    row = await fetch_role_row(conn, user_id, tenant_id)
    if row is None or not is_active_status(row["status"]):
        return None
    return row["role_code"]


async def list_active_tenant_roles(conn, user_id: str) -> dict:
    """{tenant_id: role_code} untuk SEMUA keanggotaan aktif pengguna."""
    rows = await conn.fetch(
        """
        SELECT utr.tenant_id, r.code AS role_code, utr.status
        FROM user_tenant_roles utr
        JOIN roles r ON r.id = utr.role_id
        WHERE utr.user_id = $1::uuid AND r.is_active = TRUE
        """,
        str(user_id),
    )
    return {
        row["tenant_id"]: row["role_code"]
        for row in rows
        if is_active_status(row["status"])
    }


async def has_active_membership(conn, user_id: str, tenant_id: str) -> bool:
    """Dipakai pemeriksaan last_active_tenant_id (pembaca kelima, auth.py:237).

    Pembaca ini nyaris terlewat: ia ditulis TANPA prefix `utr.` sehingga luput
    dari grep pertama. Karena selalu miss, `last_active_tenant_id` milik owner
    dihapus di SETIAP login dan pemulihan sesi multi-tenant tak pernah bekerja.
    """
    return await try_resolve_business_role(conn, user_id, tenant_id) is not None


async def require_active_membership(request: Request) -> str:
    """Dependency FastAPI: tuntut keanggotaan AKTIF di tenant pada token.

    KENAPA ADA, padahal PermissionMiddleware sudah menegakkan hal yang sama:
    beberapa prefix rute MELEWATI middleware itu lewat SKIP_PATTERNS —
    `/api/dashboard` salah satunya, dengan komentar "Dashboard has own FCL
    rules". Aturan itu tidak pernah ada. Dibuktikan 2026-08-07: anggota
    berstatus SUSPENDED, memakai token yang diterbitkan sebelum ia
    dinonaktifkan, tetap menerima HTTP 200 berisi laba-rugi, pendapatan, dan
    piutang dari `/api/dashboard/all`.

    Dipasang di level ROUTER supaya berlaku untuk SELURUH endpoint di router
    itu, termasuk yang ditambahkan besok. Memasangnya per-endpoint berarti
    endpoint berikutnya lahir tanpa pagar dan tak ada yang menyadarinya.

    Raises:
        401 — token tak membawa identitas (mestinya sudah disaring auth middleware)
        409 ROLE_NOT_PROVISIONED / 403 ROLE_INACTIVE — lihat resolve_business_role
    """
    # `get_db_pool` — BUKAN `get_pool`. Yang terakhir itu helper LOKAL milik
    # team_members.py, bukan nama publik db_pool. Percobaan pertama mengimpornya
    # dari sini dan MELEDAK dengan ImportError -> SELURUH dashboard 500.
    from .db_pool import get_db_pool

    user = getattr(request.state, "user", None) or {}
    user_id, tenant_id = user.get("user_id"), user.get("tenant_id")
    if not user_id or not tenant_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await resolve_business_role(conn, user_id, tenant_id)
