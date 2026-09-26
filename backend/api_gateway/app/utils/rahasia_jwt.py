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
_HASH_BOCOR = frozenset({"90ec970de9cbe0e68ad8962ce756463f91cfe17256973240a24f525e7959278f"})


def jwt_secret_wajib() -> str:
    v = os.getenv("JWT_SECRET", "")
    if len(v) < PANJANG_MIN:
        raise RuntimeError("JWT_SECRET tidak diset atau < 32 karakter — layanan menolak jalan (F4, gagal keras)")
    if hashlib.sha256(v.encode()).hexdigest() in _HASH_BOCOR:
        raise RuntimeError("JWT_SECRET = nilai yang tertanam di riwayat repo — wajib diganti (F4)")
    return v
