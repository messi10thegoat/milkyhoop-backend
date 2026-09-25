"""Rute yang DIPARKIR: fungsi basis datanya gagal pada SETIAP panggilan (sapuan kelas-bigint 23 Sep 2026:
deklarasi tipe tak cocok / kolom yang sudah tak ada). Diam 500 diganti penolakan jujur 409 -- pola yang
sama dengan transfer stok. Dibangun ulang hanya bila sebuah layar/fitur benar-benar memakainya.
0 pemanggil FE (git grep origin/main), 0 aksi chat, tabel di belakangnya kosong di produksi.
"""
from fastapi import HTTPException

PESAN = "Fitur ini belum tersedia."


async def fitur_belum_tersedia():
    """Dependensi FastAPI: 409 SEBELUM badan handler berjalan."""
    raise HTTPException(status_code=409, detail={"code": "FEATURE_NOT_AVAILABLE", "message": PESAN})


def fitur_belum_tersedia_dengan(pesan: str):
    """Dependensi parkir dengan pesan KHUSUS (arah pengganti untuk pengguna).

    Dipakai bila layar FE MASIH punya tombolnya (beda dengan rute di atas yang
    0 pemanggil): pengguna perlu tahu harus ke mana, bukan sekadar "belum ada".
    """
    async def _dep():
        raise HTTPException(status_code=409, detail={"code": "FEATURE_NOT_AVAILABLE", "message": pesan})

    return _dep
