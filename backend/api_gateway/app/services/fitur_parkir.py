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
