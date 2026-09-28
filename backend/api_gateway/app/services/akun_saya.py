"""Akun & usaha milik pemanggil — D2 sidebar (MASTER 28 Sep 2026; kontrak dashboard-sidebar/contracts/tenants-mine).

GET /api/tenants/mine dan GET /api/me = adaptor TIPIS atas sumber yang sudah ada:
  - keanggotaan = role_resolution.list_active_tenant_roles (SAMA dengan /api/auth/tenants & /switch-tenant; hanya
    baris user_tenant_roles AKTIF milik user INI, peran aktif) — BUKAN /api/user/tenants (yang hanya mengembalikan tenant
    aktif saat ini);
  - nama tampilan = user_profiles.display_name -> "User".name -> bagian lokal email (urutan /api/user/profile + auth);
  - label peran = roles.name (tenant > __SYSTEM__) untuk kode peran, bukan pemetaan tertulis di kode.
Tenant aktif = klaim JWT SAJA (bukan header X-Tenant-ID). Tak ada jalur buat-usaha di aplikasi (usaha lahir lewat
pendaftaran) -> can_create_tenant = False. Kota tak tersimpan di "Tenant" -> city = null.
"""
from typing import Optional

PAKET = {"BASE": "Paket Dasar", "PRO": "Paket Pro", "FREE": "Gratis", "ENTERPRISE": "Paket Enterprise"}


def inisial(nama: Optional[str]) -> str:
    """Maks 2 huruf: huruf pertama dua kata pertama ('Grapgrap Clothing' -> 'GC'; 'kaos' -> 'K')."""
    kata = [k for k in (nama or "").replace("_", " ").replace("-", " ").split() if k[:1].isalnum()]
    return "".join(k[0] for k in kata[:2]).upper() or "?"


def label_paket(kode: Optional[str]) -> Optional[str]:
    if not kode:
        return None
    return PAKET.get(str(kode).upper(), str(kode).title())


async def _nama_peran(conn, kode_per_tenant: dict) -> dict:
    """{tenant_id: nama peran} dari roles (peran tenant menang atas __SYSTEM__)."""
    if not kode_per_tenant:
        return {}
    rows = await conn.fetch(
        """SELECT tenant_id, code, name FROM roles
           WHERE is_active = TRUE AND code = ANY($1::text[])
             AND (tenant_id = '__SYSTEM__' OR tenant_id = ANY($2::text[]))""",
        list(set(kode_per_tenant.values())), list(kode_per_tenant.keys()),
    )
    sistem = {r["code"]: r["name"] for r in rows if r["tenant_id"] == "__SYSTEM__"}
    khusus = {(r["tenant_id"], r["code"]): r["name"] for r in rows if r["tenant_id"] != "__SYSTEM__"}
    return {t: khusus.get((t, k)) or sistem.get(k) or k.title() for t, k in kode_per_tenant.items()}


async def usaha_saya(conn, user_id: str, tenant_aktif: Optional[str]) -> dict:
    from .role_resolution import list_active_tenant_roles

    peran = await list_active_tenant_roles(conn, user_id)   # HANYA keanggotaan aktif user ini
    if not peran:
        return {"tenants": [], "can_create_tenant": False}
    rows = await conn.fetch(
        'SELECT id, display_name, alias, logo_url FROM "Tenant" WHERE id = ANY($1::text[]) AND suspended_at IS NULL',
        list(peran.keys()),
    )
    nama_peran = await _nama_peran(conn, {r["id"]: peran[r["id"]] for r in rows})
    daftar = []
    for r in rows:
        nama = r["display_name"] or r["alias"] or r["id"]
        daftar.append({
            "tenant_id": r["id"], "name": nama, "initial": inisial(nama),
            "role": nama_peran.get(r["id"]), "role_code": peran[r["id"]], "city": None,
            "logo_url": r["logo_url"], "is_active": r["id"] == tenant_aktif,
        })
    daftar.sort(key=lambda x: (not x["is_active"], x["name"].lower()))
    return {"tenants": daftar, "can_create_tenant": False}


async def akun_saya(conn, user_id: str, email: Optional[str], tenant_aktif: Optional[str]) -> dict:
    from .role_resolution import try_resolve_business_role

    u = await conn.fetchrow('SELECT name, email FROM "User" WHERE id = $1', user_id)
    p = await conn.fetchrow("SELECT display_name FROM user_profiles WHERE user_id = $1", user_id)
    surel = (u["email"] if u and u["email"] else None) or email
    nama = (p["display_name"] if p and p["display_name"] else None) or (u["name"] if u and u["name"] else None) \
        or ((surel or "").split("@")[0] or None)
    kode = await try_resolve_business_role(conn, user_id, tenant_aktif) if tenant_aktif else None
    t = await conn.fetchrow('SELECT id, display_name, alias, plan_tier FROM "Tenant" WHERE id = $1', tenant_aktif) \
        if tenant_aktif else None
    nama_peran = (await _nama_peran(conn, {tenant_aktif: kode})).get(tenant_aktif) if kode else None
    return {
        "user_id": str(user_id), "name": nama, "email": surel, "initials": inisial(nama),
        "role": nama_peran, "role_code": kode,
        "tenant": {"tenant_id": t["id"], "name": t["display_name"] or t["alias"] or t["id"]} if t else None,
        "plan_code": t["plan_tier"] if t else None, "plan_label": label_paket(t["plan_tier"]) if t else None,
    }
