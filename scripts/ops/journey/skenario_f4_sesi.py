"""Skenario F4 (26 Sep 2026, BACKEND3): pengguna DUA tenant — ganti tenant -> refresh -> TETAP di tenant baru, tak keluar.
Hanya lengan B bermakna (middleware + klaim perangkat); lengan A mencatat SESSION_INVALID untuk ganti-tenant (tanpa klaim).
Di SALINAN terisolasi: OWNER diberi keanggotaan tenant kedua; user_devices 'journey' + refresh R0 disisipkan; RefreshToken
gRPC (auth_service tak ada di jaringan internal) DITIRU apa adanya: cari sha256 di refresh_tokens, tolak bila dicabut,
akses ditandatangani dengan tenant TERSIMPAN (perilaku grpc_server.RefreshToken)."""
import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta

import jwt as pyjwt

from journey_lib import OWNER, T

T2 = "grapgrap-manado"
DIKENAL = {}


def _h(t):
    return hashlib.sha256(t.encode()).hexdigest()


async def _siapkan(J):
    r0 = secrets.token_urlsafe(48)
    async with J.pool.acquire() as c:
        tipe = {r["column_name"]: r["data_type"] for r in await c.fetch(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'user_tenant_roles'")}
        kol = list(tipe)
        expr = []
        for k in kol:
            if k == "tenant_id":
                expr.append("$3")
            elif k == "id":
                expr.append(f"gen_random_uuid()::{'uuid' if tipe[k] == 'uuid' else 'text'}")
            else:
                expr.append(f'"{k}"')
        await c.execute(
            f'INSERT INTO user_tenant_roles ({", ".join(chr(34) + k + chr(34) for k in kol)}) '
            f'SELECT {", ".join(expr)} FROM user_tenant_roles WHERE user_id = $1 AND tenant_id = $2 LIMIT 1',
            OWNER, T, T2)
        await c.execute("INSERT INTO refresh_tokens (user_id, tenant_id, token_hash, expires_at) VALUES ($1,$2,$3, now()+interval '30 days')",
                        OWNER, T, _h(r0))
        await c.execute("""INSERT INTO user_devices (id, user_id, tenant_id, device_type, browser_id, refresh_token_hash, is_active)
                           VALUES ('journey', $1, $2, 'web', 'journey', $3, true)
                           ON CONFLICT (id) DO UPDATE SET refresh_token_hash = EXCLUDED.refresh_token_hash, is_active = true""",
                        OWNER, T, _h(r0))
    return r0


def _pasang_grpc_tiruan(J):
    ac = J.mod("services.auth_instance").auth_client

    async def refresh_token(tok):
        async with J.pool.acquire() as c:
            row = await c.fetchrow("SELECT user_id, tenant_id, revoked_at, expires_at FROM refresh_tokens WHERE token_hash = $1", _h(tok))
        if not row or row["revoked_at"] is not None or row["expires_at"] < datetime.now(row["expires_at"].tzinfo):
            return {"success": False, "error": "Token refresh failed. Please login again."}
        now = datetime.utcnow()
        akses = pyjwt.encode({"user_id": row["user_id"], "tenant_id": row["tenant_id"], "role": "USER", "email": "",
                              "username": "", "token_type": "access", "iat": now, "exp": now + timedelta(days=7), "nbf": now},
                             os.environ["JWT_SECRET"], algorithm="HS256")
        return {"success": True, "access_token": akses, "refresh_token": tok, "user_id": row["user_id"]}
    ac.refresh_token = refresh_token
    if J.lengan == "B":
        # tiruan validate milik harness membaca `sub`; token nyata (gRPC/gateway) memakai `user_id` -> terima keduanya,
        # klaim perangkat diteruskan APA ADANYA (yang dinilai middleware)
        async def validate(token):
            try:
                d = pyjwt.decode(token, os.environ["JWT_SECRET"], algorithms=["HS256"])
            except Exception:
                return {"valid": False}
            return {"valid": True, "user_id": d.get("sub") or d.get("user_id"), "tenant_id": d.get("tenant_id"),
                    "role": d.get("role", "USER"), "email": None, "username": None,
                    "device_id": d.get("device_id"), "device_type": d.get("device_type")}
        ac.validate_token = validate


def _klaim(tok):
    return pyjwt.decode(tok, os.environ["JWT_SECRET"], algorithms=["HS256"])


async def jalankan(J):
    r0 = await _siapkan(J)
    _pasang_grpc_tiruan(J)
    if J.lengan == "A":
        # lengan A = app mini TANPA router auth & tanpa middleware -> tak ada yang bisa dinilai untuk F4
        print("lengan A: F4 hanya bermakna di lengan B (router auth + AuthMiddleware nyata) — dilewati")
        return
    _, sw = await J.langkah("01_ganti_tenant", "POST", "/api/auth/switch-tenant", {"tenant_id": T2})
    akses1, r1 = (sw or {}).get("access_token"), (sw or {}).get("refresh_token")
    if not akses1 or not r1:
        return J.gagal("01_isi", str(sw)[:200])
    k = _klaim(akses1)
    if (k.get("tenant_id"), k.get("device_id"), k.get("device_type")) != (T2, "journey", "web"):
        J.gagal("01_klaim", str(k))
    if "." in r1 or r1 == r0:
        J.gagal("01_refresh_diputar", "refresh bukan token opak baru")
    async with J.pool.acquire() as c:
        lama = await c.fetchval("SELECT revoked_at IS NOT NULL FROM refresh_tokens WHERE token_hash = $1", _h(r0))
        baru = await c.fetchrow("SELECT tenant_id, revoked_at FROM refresh_tokens WHERE token_hash = $1", _h(r1))
        dev = await c.fetchval("SELECT refresh_token_hash FROM user_devices WHERE id = 'journey'")
    if not (lama and baru and baru["tenant_id"] == T2 and baru["revoked_at"] is None and dev == _h(r1)):
        J.gagal("01_db", f"lama_dicabut={lama} baru={dict(baru) if baru else None} dev_ok={dev == _h(r1)}")

    _, rf = await J.langkah("02_refresh_sesudah_ganti", "POST", "/api/auth/refresh", {"refresh_token": r1})
    akses2 = ((rf or {}).get("data") or {}).get("access_token")
    if not akses2:
        return J.gagal("02_isi", str(rf)[:200])
    k2 = _klaim(akses2)
    if (k2.get("tenant_id"), k2.get("device_id"), k2.get("device_type")) != (T2, "journey", "web"):
        J.gagal("02_tetap_di_tenant_baru", str(k2))
    await J.langkah("03_pakai_akses_hasil_refresh", "GET", "/api/sales-orders/summary",
                    headers={"Authorization": f"Bearer {akses2}"})
    await J.langkah("04_refresh_lama_ditolak", "POST", "/api/auth/refresh", {"refresh_token": r0}, harap=(401,))
    now = datetime.utcnow()
    tanpa = pyjwt.encode({"sub": OWNER, "user_id": OWNER, "tenant_id": T, "role": "OWNER", "exp": now + timedelta(hours=1)},
                         os.environ["JWT_SECRET"], algorithm="HS256")
    _, b = await J.langkah("05_token_tanpa_klaim_perangkat_401", "GET", "/api/sales-orders/summary",
                           headers={"Authorization": f"Bearer {tanpa}"}, harap=(401,))
    if (b or {}).get("code") != "SESSION_INVALID":
        J.gagal("05_kode", str(b)[:200])
    await J.langkah("06_register_lama_diparkir", "POST", "/api/auth/register",
                    {"email": f"j{uuid.uuid4().hex[:6]}@resend.dev", "password": "Xx123456!x", "name": "j"}, harap=(409,))
