"""Kontrol merah ALAT (Law 33): skenario ini HARUS berakhir FAIL -> mh-journey.sh keluar 1.
Bila keluar 0, penghitung FAIL/rc runner rusak -> jangan percayai hijau skenario lain."""


async def jalankan(J):
    await J.langkah("sengaja_salah_harap", "GET", "/api/tenant/today", harap=(418,))
