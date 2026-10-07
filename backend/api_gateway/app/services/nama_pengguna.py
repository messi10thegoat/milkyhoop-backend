"""Nama tampilan pengguna: SATU rantai, SATU fungsi (6 Okt 2026, penanda tangan Penawaran).

Rantai: user_profiles.display_name (diubah pengguna SENDIRI di Akun > Profil, PUT /api/user/profile)
        -> "User".fullname -> "User".name; kosong/spasi dilewati. Tak ada yang cocok -> None (pemanggil jatuh ke surel).

Dulu pembaca penanda tangan Penawaran (default_penawaran + dropdown GET /api/team-members) membaca "User".fullname/name
dan MELEWATKAN user_profiles, sedangkan sidebar/GET /api/me membaca user_profiles lebih dulu -> pemilik mengganti nama
di profil, dropdown tetap nama lama. Semua pembaca nama pengguna WAJIB lewat sini (penjaga: tests/unit/test_nama_pengguna.py).
"""
from typing import Optional


def nama_pengguna(profil_display_name=None, fullname=None, name=None) -> Optional[str]:
    for v in (profil_display_name, fullname, name):
        v = (v or "").strip()
        if v:
            return v
    return None


# Padanan SQL rantai di atas; alias tabel diberikan pemanggil ("User" u, user_profiles p).
def nama_pengguna_sql(profil: str = "p", user: str = "u", email: bool = False) -> str:
    """email=True: surel jadi cadangan terakhir (tampilan riwayat/lampiran); False: NULL bila ketiganya kosong."""
    return (f"COALESCE(NULLIF(trim({profil}.display_name), ''), NULLIF(trim({user}.fullname), ''), "
            f"NULLIF(trim({user}.name), '')" + (f", {user}.email)" if email else ")"))


async def nama_untuk(conn, user_ids, cadangan_email: bool = True) -> dict:
    """{user_id(str): nama} untuk tampilan aktor (riwayat, aktivitas dokumen). SATU kueri, rantai SAMA dgn dropdown/me/penanda tangan.
    user_id tak dikenal tak muncul di hasil."""
    ids = sorted({str(i) for i in user_ids if i})
    if not ids:
        return {}
    rows = await conn.fetch(
        f'''SELECT u.id, {nama_pengguna_sql("p", "u", cadangan_email)} AS nama
            FROM "User" u LEFT JOIN user_profiles p ON p.user_id = u.id WHERE u.id = ANY($1::text[])''', ids)
    return {r["id"]: r["nama"] for r in rows}
