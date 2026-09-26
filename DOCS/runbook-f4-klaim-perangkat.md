# F4 — runbook deploy + rollback (jendela MALAM, sesudah 20:00 WIB, satu jendela dengan restart gateway)

Cabang: fix/f4-klaim-perangkat (worktree /root/mh-f4). Tanpa migrasi. Dua layanan berubah:
- api_gateway (bind-mount) -> window_item seperti biasa (penanda "SESSION_INVALID" di middleware/auth_middleware.py).
- auth_service (IMAGE, compose `build:`; kode di-COPY saat build) -> build ulang + recreate.

## Urutan (satu jendela)
0. Prasyarat: `docker exec milkyhoop-dev-auth_service-1 sh -c 'test ${#JWT_SECRET} -ge 32 && echo OK'` dan hal
   sama untuk gateway -> OK (tanpa mencetak nilai). Kalau TIDAK OK: BERHENTI (layanan baru akan menolak jalan).
1. Arsip rollback auth_service:  `docker tag milkyhoop-dev-auth_service:latest milkyhoop-dev-auth_service:rollback-pra-f4`
   (catat image id: `docker image inspect -f '{{.Id}}' milkyhoop-dev-auth_service:rollback-pra-f4`).
2. Gateway: `window_item.sh f4 /root/mh-f4 fix/f4-klaim-perangkat middleware/auth_middleware.py "SESSION_INVALID"`
   (merge ke master + restart gateway + penanda + healthz). Gateway startup memanggil jwt_secret_wajib() — gagal = kontainer
   tak sehat -> window_item melapor healthz != 200 -> ROLLBACK gateway (langkah R1).
3. auth_service dari MASTER (sesudah merge): `cd /root/milkyhoop-dev && docker compose build auth_service &&
   docker compose up -d --no-deps auth_service`; tunggu `docker inspect -f '{{.State.Health.Status}}'` = healthy.
4. Verifikasi (TANPA login pemilik): kontainer sekali-pakai/harness — (a) grep kode BERJALAN auth_service tak memuat
   cadangan: `docker exec milkyhoop-dev-auth_service-1 grep -c jwt_secret_wajib /app/backend/services/auth_service/app/utils/jwt_handler.py` = 1;
   (b) POST /api/auth/register -> 409; (c) GET rute terlindungi dengan JWT tanpa klaim perangkat (ditandatangani di
   kontainer gateway, tak dicetak) -> 401 SESSION_INVALID; (d) refresh dengan token acak -> 401 SESSION_INVALID;
   (e) login/refresh/ganti-tenant NYATA hanya dengan akun uji non-pemilik (izin MASTER) atau ditunda ke harness.

## Rollback
R1 gateway: `git -C /root/milkyhoop-dev revert --no-edit <sha merge F4>` lewat gerbang + push + mh-restart (pola biasa).
R2 auth_service: `docker tag milkyhoop-dev-auth_service:rollback-pra-f4 milkyhoop-dev-auth_service:latest &&
   docker compose up -d --no-deps auth_service` (TANPA build) -> healthy -> login lama berjalan lagi.
Catatan: rollback gateway TANPA rollback auth_service aman (auth_service baru hanya menolak env kosong/bocor; prod punya env
sah). Rollback auth_service tanpa gateway juga aman. Token yang terbit sesudah F4 tetap sah di kode lama (klaim tambahan).
Efek samping yang DITERIMA MASTER: token hidup tanpa klaim perangkat (refresh gRPC lama, /register 24 Sep) -> keluar sekali.
