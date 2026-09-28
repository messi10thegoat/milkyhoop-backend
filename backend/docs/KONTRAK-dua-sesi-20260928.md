# Kontrak: dua sesi per akun (desktop web + HP web) — 28 Sep 2026

**Aturan:** per akun ada SATU sesi aktif per kelas: `web_desktop` dan `web_mobile`.

- Login baru di kelas yang sama menggantikan HANYA sesi lama di kelas itu. Sesi yang digantikan menerima 401 `SESSION_REPLACED` di request berikutnya.
- Login di kelas lain tidak menendang siapa pun.
- "HP" = browser di HP (tampilan HP). Tidak ada aplikasi.

## Diukur sebelum perbaikan (prod 28 Sep)

- Login HP dengan UA iPhone/Android SUDAH bertipe `mobile` sejak 21 Sep (`detect_device_type`).
- Cabang mobile di `DeviceService.register_device` menonaktifkan SEMUA baris web, mencabut refresh token-nya, dan mengirim WS force_logout. Contoh nyata: kaos 25 Sep 14:35, login iPhone mematikan 1 sesi desktop. **Dihapus.**
- `/api/auth/logout` adalah jalur publik, dan FE memanggilnya TANPA `Authorization` → `request.state.user` kosong → `device_type` selalu `"web"` → **logout dari HP mencabut sesi desktop.** Cabang `mobile → revoke_all` adalah kode mati. Temuan ini datang dari journey, bukan dari bacaan kode.
- HP yang meminta "situs desktop" dan iPadOS mengirim UA Mac, sehingga tercatat `web` dan menendang desktop. Solusinya `client_kind`.

## Kontrak FE (FRONTEND)

| Titik | Kirim | Nilai |
|---|---|---|
| `POST /api/auth/login` body | `client_kind` | `"web_mobile"` bila deteksi sentuh cangkang HP (pointer:coarse / hover:none, `utils/device.ts` DESKTOP_POINTER_QUERY tak cocok), selain itu `"web_desktop"` |
| `POST /api/auth/google` body | `client_kind` | sama |

- Respons login `data.client_kind` = kelas yang dipakai server (`web_desktop` | `web_mobile`).
- Absen atau tak dikenal: server memakai UA (perilaku 21 Sep), jadi klien lama tidak berubah.
- Refresh tidak perlu mengirim apa pun. Kelas diambil dari `user_devices.device_type` milik refresh token.
- Logout tetap berbentuk seperti sekarang (`refresh_token` saja).
- Logout hanya mencabut sesi Redis milik perangkat itu, dan hanya bila perangkat itu masih pemegang kelasnya.
- `logout_all_devices: true` tetap mencabut keduanya.

## Penyimpanan

| Kelas klien | `user_devices.device_type` (CHECK) | Redis | Klaim JWT `device_type` |
|---|---|---|---|
| web_desktop | `web` | `session:{uid}:web` | `web` |
| web_mobile | `mobile` | `session:{uid}:mobile` | `mobile` |

- Tanpa migrasi (V327 tidak dipakai).
- Satu penentu: `session_manager.resolve_device_type(client_kind, user_agent)`.

## Batas yang diterima

Klien yang berbohong tentang kelasnya paling jauh mendapat DUA sesi (satu per kelas). Batas itu tetap ditegakkan server.

## Bukti

- `tests/unit/test_dua_sesi.py` (23): 4 lengan sabotase merah; tes logout merah sebelum patch logout.
- F4 23 hijau.
- Journey `skenario_dua_sesi` lengan B (app penuh, login/refresh/logout nyata, otoritas sesi nyata): 21/21 lulus.
- Kontrol merah pada master lama: 9 FAIL di langkah yang tepat.
