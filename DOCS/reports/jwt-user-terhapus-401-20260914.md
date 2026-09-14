# JWT user-terhapus → 401 (+ refresh ditolak) — LIVE

**Commit:** `6ecf5037` (deploy/master) · **Tanggal:** 2026-09-14 · **Gateway:** healthz 200

## Masalah (terukur)
`auth_middleware` set `request.state.user` dari klaim JWT **tanpa cek User ada** →
user yang DIHAPUS tetap diterima sampai token kedaluwarsa (bisa berhari-hari). `validate_token`
= gRPC ke auth-service (verify tanda tangan/expiry), **tidak** cek eksistensi. Refresh
(`/api/auth/refresh`) juga tak cek → user terhapus bisa perpanjang token selamanya.

Fakta pendukung: tabel `"User"` ADA di `milkydb` (gateway bisa query); **kolom `id` bertipe TEXT**
(bukan uuid); tak ada kolom active/deleted (hapus = row hilang). `user_tenant_roles` tak punya FK ke
`"User"`. auth-service = di LUAR repo (hanya stub gRPC) → perbaikan di GATEWAY.

## Perbaikan
1. **Middleware**: sesudah JWT valid, `SELECT 1 FROM "User" WHERE id = $1` (TEXT). Absen → **401
   `USER_NOT_FOUND`** (`force_logout`). Cache in-proc pendek (**positif 60s, negatif 5s**) → 1
   query/user/60s (PK lookup). **Galat DB/pool → 503 `AUTH_DB_UNAVAILABLE`** (retry), **cache TIDAK
   diisi** — blip DB tak me-logout semua orang (JWT sig tetap primer). uuid tak valid → 401 (bukan 503).
2. **Refresh** (poin d): cek eksistensi sebelum gRPC; absen → 401; galat DB → 503.
3. **403 (membership non-aktif) TAK diubah** — tetap jalur policy engine (`membership_active`).

## Gerbang dua-sisi (kontainer, dispatch+refresh NYATA, validate_token/gRPC distub, DB nyata) — 10/10 GREEN
user ADA → lolos & state.user diset; TERHAPUS → 401; malformed → 401; **galat DB → 503 + cache tak
diisi**; **cache-hit hindari query**; refresh terhapus → 401 sebelum gRPC; refresh galat DB → 503;
refresh user ada → lolos; **middleware LAMA tanpa fungsi + dispatch LAMA meloloskan user terhapus
(RED-alat)**. Gerbang **menangkap bug `$1::uuid`** (kolom id TEXT) sebelum live.
Live smoke (tanpa sentuh pemilik): token bogus → 401, tanpa token → 401.

## Temuan tambahan (MASTER minta)
- **Hapus sesi Redis saat user dihapus:** TIDAK ADA endpoint hapus-user di app (grep: hanya
  delete_account=CoA, delete_bank_account). Penghapusan user selama ini **manual/DB-level**
  (mis. skill milkyhoop-clean). → Tak ada tempat hook. **Catatan operasional:** saat menghapus row
  `"User"` manual, hapus juga sesi Redis-nya (`session_manager`) supaya token aktif langsung mati
  (kini cache 60s + 401 sudah membatasi paparan).
- **Redirect FE (poin c) — untuk diteruskan ke FE:** `auth.ts refreshAccessToken()` SUDAH
  `logout('token_expired')` saat refresh balas 401/403; `logout()` bersihkan token + dispatch event
  `'logout'`. Listener di `App.tsx` = `handleAuthChange` → `setAuthState(false)` (routing digerakkan
  STATE), **bukan** `navigate('/welcome')` eksplisit. Jadi user kembali ke layar tak-terautentikasi
  via router; landing tepat (welcome vs login) = default router — **minta sesi FE konfirmasi /welcome**.
