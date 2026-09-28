"""D2 sidebar (28 Sep 2026): GET /api/tenants/mine & /api/me lewat app PENUH (middleware izin nyata, STEP 2 READ
default-tertutup) di salinan prod. Untuk OWNER & KOLABORATOR kaos: daftar usaha == keanggotaan AKTIF user itu di DB
(user_tenant_roles + roles aktif + tenant tak ditangguhkan) — tak ada usaha orang lain; tepat satu is_active = tenant
JWT; /api/me peran == peran DB di tenant aktif."""
from journey_lib import KOLAB, OWNER, T


async def _kebenaran(J, user_id):
    async with J.pool.acquire() as c:
        rows = await c.fetch(
            """SELECT utr.tenant_id, r.code FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id
               JOIN "Tenant" t ON t.id = utr.tenant_id
               WHERE utr.user_id = $1::uuid AND r.is_active AND t.suspended_at IS NULL
                 AND lower(COALESCE(utr.status, 'active')) IN ('active', 'aktif')""", user_id)
        semua = await c.fetchval('SELECT count(*) FROM "Tenant"')
    return {r["tenant_id"]: r["code"] for r in rows}, semua


async def jalankan(J):
    for nama, uid in (("owner", OWNER), ("kolab", KOLAB)):
        _, r = await J.langkah(f"{nama}_tenants_mine", "GET", "/api/tenants/mine", user=uid)
        benar, semua = await _kebenaran(J, uid)
        didapat = {t["tenant_id"]: t.get("role_code") for t in (r or {}).get("tenants", [])}
        print(f"{nama}: dapat {sorted(didapat)} | DB {sorted(benar)} | total tenant di DB {semua}", flush=True)
        if didapat != benar:
            J.gagal(f"{nama}_sama_dengan_keanggotaan", f"API {didapat} != DB {benar}")
        aktif = [t["tenant_id"] for t in (r or {}).get("tenants", []) if t.get("is_active")]
        if aktif != [T]:
            J.gagal(f"{nama}_satu_aktif", f"is_active={aktif}")
        for t in (r or {}).get("tenants", []):
            if not t.get("initial") or len(t["initial"]) > 2 or not t.get("name") or not t.get("role"):
                J.gagal(f"{nama}_bentuk", str(t)[:200])
        if (r or {}).get("can_create_tenant") is not False:
            J.gagal(f"{nama}_can_create", str(r)[:120])
        _, m = await J.langkah(f"{nama}_me", "GET", "/api/me", user=uid)
        if not (m or {}).get("email") or not (m or {}).get("name") or (m or {}).get("role_code") != benar.get(T):
            J.gagal(f"{nama}_me", f"{str(m)[:200]} (peran DB {benar.get(T)})")
        if ((m or {}).get("tenant") or {}).get("tenant_id") != T:
            J.gagal(f"{nama}_me_tenant", str(m)[:160])
