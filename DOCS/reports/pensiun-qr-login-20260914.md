# Pensiun QR login — LIVE

**Commit:** `79fdc113` (deploy/master) · **Tanggal:** 2026-09-14 · **Gateway:** healthz 200

## Konteks
Owner: fitur QR login tak dipakai. FE r61 mengarahkan `/login` → `/welcome` (nol pemanggil nyata).
Rute publik tanpa auth = permukaan serangan → dicabut.

## Ukur pra-cabut
- Permukaan: `routers/qr_auth.py` (prefix `/api/auth/qr`, **5 rute**: generate, status/{token}, ws/{token},
  scan, approve) + `services/qr_token_service.py` (QR-only) + `_is_qr_public_endpoint` (skip-auth) +
  plumbing `websocket_hub` (qr_connections, register_qr/unregister_qr/send_to_qr/is_qr_connected).
- Pemanggil lain: **NOL** ref kode BE-internal/mobile/bot. Pemanggil terlihat di log = halaman `/login`
  pra-r61 yang auto-generate saat dibuka (bundel/tab basi) — bukan pemakaian. 1 jam terakhir: 2 hit
  generate dari IP proxy seragam (UA tak tercatat di log uvicorn).
- **Device WS `/api/devices/ws/{id}` BUKAN QR** (force-logout + remote barcode scan) → TAK disentuh.
- Login email/register/refresh + Google/signup TERPISAH.

## Dicabut
- Hapus file `qr_auth.py` + `qr_token_service.py`.
- `main.py`: cabut import + mount.
- `auth_middleware`: hapus `_is_qr_public_endpoint` + bypass call-site.
- `websocket_hub`: hapus plumbing QR (init, 4 metode, baris stats, blok cleanup QR); device plumbing utuh.
- (823 baris terhapus, 2 file hilang.)

## Gerbang dua-sisi (probe live + tabel rute) — GREEN
| | LAMA (baseline) | BARU (live) |
|---|---|---|
| qr/generate, qr/status | **200 (publik)** | **401** (bypass dicabut) |
| qr/scan, qr/approve | 401 (rute ada) | 401 (middleware blok; rute hilang) |
| **tabel rute /api/auth/qr** | 5 rute | **`[]` (0)** |
| login (POST no body) | 422 | **422** (tak terpengaruh) |
| google (GET) | 405 | **405** (tak terpengaruh) |
| device WS `/api/devices/ws/{id}` | ada | **ada** (utuh) |

Catatan 401-bukan-404: middleware kini menolak request tak-terautentikasi ke `/api/auth/qr/*` SEBELUM
routing (bypass sudah dicabut), jadi 401 menutupi 404. Ketiadaan rute dibuktikan lewat **tabel rute
live = kosong**. Klien basi (tab/bundel pra-r61) pulih dengan muat ulang (r61 → `/welcome`).
