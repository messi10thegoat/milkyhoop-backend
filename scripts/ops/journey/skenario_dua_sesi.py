"""Dua sesi per akun (28 Sep 2026): satu sesi aktif per kelas {web_desktop, web_mobile}, lewat app PENUH (lengan B):
POST /api/auth/login NYATA (DeviceService + Prisma ke salinan, user_devices nyata), AuthMiddleware NYATA dengan otoritas
sesi NYATA (SessionManager.is_session_valid dipulihkan; Redis = kamus di memori karena Redis tak terjangkau dari jaringan
uji), /refresh dan /logout NYATA. gRPC auth_service (tak ada di jaringan internal) DITIRU: login mencetak akses dengan
klaim perangkat dari metadata + refresh opak yang disimpan di refresh_tokens; refresh = pola skenario F4.

Alur: desktop D1 -> HP M1 (UA iPhone) -> keduanya hidup, dua baris user_devices aktif -> HP "situs desktop" M2 (UA Mac,
client_kind web_mobile) menggantikan M1 saja -> desktop kedua D2 menggantikan D1 saja -> refresh M2 tetap kelas mobile ->
logout HP tak membunuh D2 -> token tanpa klaim tetap SESSION_INVALID (F4)."""
import hashlib
import os
import secrets
from datetime import datetime, timedelta

import jwt as pyjwt

from journey_lib import OWNER, T

UA_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140 Safari/537.36"
UA_HP = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"
UJI = "/api/sales-orders/summary"


def _h(t):
    return hashlib.sha256(t.encode()).hexdigest()


class _Redis:
    def __init__(self):
        self.d = {}

    def set(self, k, v, ex=None):
        self.d[k] = v

    def get(self, k):
        return self.d.get(k)

    def delete(self, k):
        return 1 if self.d.pop(k, None) is not None else 0

    def ping(self):
        return True

    def pipeline(self, transaction=True):
        r, ops = self, []

        class P:
            def set(self, *a, **kw):
                ops.append(lambda: r.set(*a, **kw))

            def delete(self, *a):
                ops.append(lambda: r.delete(*a))

            def execute(self):
                for f in ops:
                    f()
        return P()


def _pasang(J):
    sesi = J.mod("services.session_manager").session_manager
    if "is_session_valid" in vars(sesi):
        del sesi.is_session_valid          # harness mem-stub True -> otoritas sesi NYATA
    sesi.redis = _Redis()
    ac = J.mod("services.auth_instance").auth_client

    async def login_user(email, password, device_id=None, device_type=None):
        now = datetime.utcnow()
        akses = pyjwt.encode({"sub": OWNER, "user_id": OWNER, "tenant_id": T, "role": "OWNER", "email": email,
                              "username": email, "token_type": "access", "device_id": device_id,
                              "device_type": device_type, "iat": now, "exp": now + timedelta(days=7), "nbf": now},
                             os.environ["JWT_SECRET"], algorithm="HS256")
        rt = secrets.token_urlsafe(48)
        async with J.pool.acquire() as c:
            await c.execute("INSERT INTO refresh_tokens (user_id, tenant_id, token_hash, expires_at) "
                            "VALUES ($1,$2,$3, now()+interval '30 days')", OWNER, T, _h(rt))
            nama = await c.fetchval('SELECT name FROM "User" WHERE id = $1', OWNER)
        return {"success": True, "user_id": OWNER, "tenant_id": T, "email": email, "name": nama, "role": "OWNER",
                "access_token": akses, "refresh_token": rt, "expires_at": None}

    async def refresh_token(tok):
        async with J.pool.acquire() as c:
            row = await c.fetchrow("SELECT user_id, tenant_id, revoked_at FROM refresh_tokens WHERE token_hash = $1", _h(tok))
        if not row or row["revoked_at"] is not None:
            return {"success": False, "error": "Token refresh failed. Please login again."}
        now = datetime.utcnow()
        akses = pyjwt.encode({"user_id": row["user_id"], "tenant_id": row["tenant_id"], "role": "USER", "email": "",
                              "token_type": "access", "iat": now, "exp": now + timedelta(days=7), "nbf": now},
                             os.environ["JWT_SECRET"], algorithm="HS256")
        return {"success": True, "access_token": akses, "refresh_token": tok, "user_id": row["user_id"]}

    async def logout(user_id, refresh_token=None, logout_all_devices=False):
        return {"success": True, "revoked_tokens": 1}

    async def validate(token):
        try:
            d = pyjwt.decode(token, os.environ["JWT_SECRET"], algorithms=["HS256"])
        except Exception:
            return {"valid": False}
        return {"valid": True, "user_id": d.get("sub") or d.get("user_id"), "tenant_id": d.get("tenant_id"),
                "role": d.get("role", "USER"), "email": None, "username": None,
                "device_id": d.get("device_id"), "device_type": d.get("device_type")}
    ac.login_user, ac.refresh_token, ac.logout, ac.validate_token = login_user, refresh_token, logout, validate
    return sesi


def _k(tok):
    return pyjwt.decode(tok, os.environ["JWT_SECRET"], algorithms=["HS256"])


async def _login(J, nama, ua, kind=None):
    body = {"email": "delivered+owner@resend.dev", "password": "tak-dipakai-grpc-ditiru"}
    if kind:
        body["client_kind"] = kind
    _, r = await J.langkah(nama, "POST", "/api/auth/login", body, headers={"User-Agent": ua})
    d = (r or {}).get("data") or {}
    return d.get("access_token"), d.get("refresh_token"), d


async def _pakai(J, nama, tok, harap):
    _, b = await J.langkah(nama, "GET", UJI, headers={"Authorization": f"Bearer {tok}"}, harap=harap)
    return b


async def _aktif(J):
    async with J.pool.acquire() as c:
        return {r["id"]: r["device_type"] for r in await c.fetch(
            "SELECT id, device_type FROM user_devices WHERE user_id = $1 AND tenant_id = $2 AND is_active", OWNER, T)}


async def jalankan(J):
    if J.lengan == "A":
        print("lengan A: tanpa router auth & middleware — skenario dua sesi hanya bermakna di lengan B")
        return
    _pasang(J)
    async with J.pool.acquire() as c:   # awal bersih: sesi lama salinan tak ikut dinilai
        await c.execute("UPDATE user_devices SET is_active = false WHERE user_id = $1", OWNER)

    d1, rd1, dd1 = await _login(J, "01_login_desktop", UA_MAC)
    m1, _, dm1 = await _login(J, "02_login_hp_ua_iphone", UA_HP)
    if not (d1 and m1):
        return J.gagal("01_02_isi", f"d1={bool(d1)} m1={bool(m1)}")
    if (_k(d1).get("device_type"), _k(m1).get("device_type")) != ("web", "mobile"):
        J.gagal("02_klaim", f"{_k(d1).get('device_type')} {_k(m1).get('device_type')}")
    if (dd1.get("client_kind"), dm1.get("client_kind")) != ("web_desktop", "web_mobile"):
        J.gagal("02_respons_client_kind", f"{dd1.get('client_kind')} {dm1.get('client_kind')}")
    await _pakai(J, "03_desktop_tetap_hidup_sesudah_login_hp", d1, (200,))
    await _pakai(J, "04_hp_hidup", m1, (200,))
    akt = await _aktif(J)
    if sorted(akt.values()) != ["mobile", "web"]:
        J.gagal("04_user_devices_dua_aktif", str(akt))

    m2, rm2, _ = await _login(J, "05_login_hp_situs_desktop_ua_mac", UA_MAC, "web_mobile")
    b = await _pakai(J, "06_hp_lama_diganti", m1, (401,))
    if (b or {}).get("code") != "SESSION_REPLACED":
        J.gagal("06_kode", str(b)[:160])
    await _pakai(J, "07_desktop_tetap_hidup_sesudah_hp_kedua", d1, (200,))
    await _pakai(J, "08_hp_baru_hidup", m2, (200,))

    d2, _, _ = await _login(J, "09_login_desktop_kedua", UA_MAC, "web_desktop")
    await _pakai(J, "10_desktop_pertama_diganti", d1, (401,))
    await _pakai(J, "11_desktop_kedua_hidup", d2, (200,))
    await _pakai(J, "12_hp_tetap_hidup_sesudah_desktop_kedua", m2, (200,))
    akt = await _aktif(J)
    if sorted(akt.values()) != ["mobile", "web"]:
        J.gagal("12_user_devices_tepat_satu_per_kelas", str(akt))

    _, rf = await J.langkah("13_refresh_hp", "POST", "/api/auth/refresh", {"refresh_token": rm2})
    m3 = ((rf or {}).get("data") or {}).get("access_token")
    if not m3 or _k(m3).get("device_type") != "mobile" or _k(m3).get("device_id") != _k(m2).get("device_id"):
        J.gagal("13_refresh_tetap_kelas_mobile", str(_k(m3) if m3 else rf)[:200])
    else:
        await _pakai(J, "14_akses_hasil_refresh_hidup", m3, (200,))
    await _pakai(J, "15_desktop_hidup_sesudah_refresh_hp", d2, (200,))

    # bentuk FE (utils/auth.ts): TANPA Authorization, hanya refresh_token -> request.state.user kosong
    await J.langkah("16a_logout_desktop_usang_tak_mencabut_desktop_baru", "POST", "/api/auth/logout",
                    {"refresh_token": rd1, "logout_all_devices": False}, headers={"Authorization": ""},
                    harap=(401,))   # refresh D1 sudah dicabut saat D2 login -> LOGOUT_UNPROVEN, nol pencabutan
    await _pakai(J, "16b_desktop_kedua_masih_hidup", d2, (200,))
    await J.langkah("16_logout_hp", "POST", "/api/auth/logout", {"refresh_token": rm2, "logout_all_devices": False},
                    headers={"Authorization": ""})
    await _pakai(J, "17_hp_mati_sesudah_logout", m2, (401,))
    await _pakai(J, "18_desktop_hidup_sesudah_logout_hp", d2, (200,))

    now = datetime.utcnow()
    tanpa = pyjwt.encode({"sub": OWNER, "user_id": OWNER, "tenant_id": T, "role": "OWNER", "exp": now + timedelta(hours=1)},
                         os.environ["JWT_SECRET"], algorithm="HS256")
    b = await _pakai(J, "19_token_tanpa_klaim_perangkat_401", tanpa, (401,))
    if (b or {}).get("code") != "SESSION_INVALID":
        J.gagal("19_kode", str(b)[:160])
