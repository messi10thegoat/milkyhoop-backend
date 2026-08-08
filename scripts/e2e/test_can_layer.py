"""UJI LAPIS 1 — can() dipanggil LANGSUNG, tanpa HTTP.

KENAPA TERPISAH DARI GATE HTTP
------------------------------
Kebijakan "tanpa baris peran -> DENY" ditegakkan di DUA lapis: can() dan
PermissionMiddleware. Kalau keduanya menolak, gate HTTP yang hijau TIDAK
memberi tahu lapisan mana yang bekerja — dan bila salah satunya diam-diam tak
jalan, itu baru ketahuan saat lapisan satunya dicabut, entah kapan.

Berkas ini menguji can() saja. Gate HTTP menguji middleware. Dua bukti, dua
lapisan.
"""
import asyncio, sys

sys.path.insert(0, "/app")
from backend.api_gateway.app.services.policy_engine_client import (  # noqa: E402
    PolicyEngineClient,
    UserContext,
)

PASS = FAIL = 0


def ok(label, got, want):
    global PASS, FAIL
    if got == want:
        print(f"  ✓ {label}: {got}")
        PASS += 1
    else:
        print(f"  ✗ {label}: dapat={got} HARAP={want}")
        FAIL += 1


async def main():
    engine = PolicyEngineClient(pool=None)  # pool tak tersentuh di jalur ini

    # --- INTI: tanpa baris peran -> DENY, apa pun tier-nya ---
    for tier in ("ADMIN", "OWNER", "USER", "FREE"):
        ctx = UserContext(user_id="u", tenant_id="t", subscription_role=tier)
        ok(f"tanpa business_role_id, tier={tier}", await engine.can(ctx, "R", "INVOICE"), False)

    # --- PAGAR: keanggotaan nonaktif tetap DENY (batch sebelumnya) ---
    ctx = UserContext(user_id="u", tenant_id="t", subscription_role="ADMIN",
                      business_role_id="r1", business_role_code="OWNER",
                      membership_active=False)
    ok("SUSPENDED walau OWNER", await engine.can(ctx, "R", "INVOICE"), False)

    # --- PAGAR DUA ARAH: OWNER sehat HARUS True.
    # Tanpa baris ini, "semuanya False" akan lulus dan uji ini tak membuktikan apa pun.
    ctx = UserContext(user_id="u", tenant_id="t", subscription_role="FREE",
                      business_role_id="r1", business_role_code="OWNER")
    ok("OWNER aktif -> True", await engine.can(ctx, "R", "INVOICE"), True)

    print(f"\n===== {PASS} sesuai, {FAIL} menyimpang =====")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
