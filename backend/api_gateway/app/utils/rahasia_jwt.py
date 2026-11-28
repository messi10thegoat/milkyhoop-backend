"""Rahasia JWT — SATU pintu baca, GAGAL KERAS (F4, 26 Sep 2026).

Diukur: nilai cadangan 64-hex TERTANAM di auth.py (2x) + auth_service jwt_handler (tanda tangan DAN verifikasi)
+ docker-compose.staging.yml (ter-track, ada di riwayat git). Prod memakai nilai LAIN (dibandingkan lewat hash),
jadi tak tereksploitasi hari ini — tetapi tumpukan mana pun yang kehilangan env menandatangani token dengan
kunci yang diketahui siapa pun pemegang repo -> token palsu (user_id/tenant apa pun). Cadangan "" (signup/config)
= HS256 berkunci kosong. Kini: env WAJIB, >= 32 karakter, dan BUKAN nilai yang pernah tertanam (dibandingkan
lewat sha256 — literalnya TIDAK ditulis di sini). Dipanggil saat startup (lifespan) dan saat menandatangani.
"""
import hashlib
import os

PANJANG_MIN = 32
# sha256 dari nilai cadangan yang pernah tertanam di repo (bukan nilainya)
_HASH_BOCOR = frozenset({
    "2bd85b519dd8ed223e79c9b36ae241ae8c81cbcd093f3c7f618bb4aa208d5012",
    "43e60f1de582b7536583283668bdaedd966ce06e174d618648d22d26566618d5",
    "90ec970de9cbe0e68ad8962ce756463f91cfe17256973240a24f525e7959278f",
    "dbb5b37cd6122ae71bea2bf9154bd3da43212903e01fe77d85b656b02a07ef39",
})  # +audit 28 Nov (rahasia-rantai-pasok-6): literal generate_test_token.py, infra/env/.env_template, docker-compose.override.yml.test


def jwt_secret_wajib() -> str:
    v = os.getenv("JWT_SECRET", "")
    if len(v) < PANJANG_MIN:
        raise RuntimeError("JWT_SECRET tidak diset atau < 32 karakter — layanan menolak jalan (F4, gagal keras)")
    if hashlib.sha256(v.encode()).hexdigest() in _HASH_BOCOR:
        raise RuntimeError("JWT_SECRET = nilai yang tertanam di riwayat repo — wajib diganti (F4)")
    return v
