-- V229: user_profiles — tabel yang kode tulis tapi tak pernah dimigrasikan.
--
-- `GET /api/user/profile` 500 SETIAP KALI dengan `relation "user_profiles"
-- does not exist`. Terukur 2026-09-03: 93 kali dalam satu jendela log
-- (~36 menit), yaitu 100% dari galat 500 terbanyak di sistem.
--
-- Ini jalur HARIAN, bukan endpoint mati: dipanggil dari
-- `AuthenticatedLayout.tsx:242` (setiap muat halaman ber-auth),
-- `Dashboard.tsx:473`, dan `AccountSettingsPage.tsx:151,176` (GET + PUT).
-- Fiturnya menghadap pengguna: nama tampilan di layout, bisa diubah di
-- Pengaturan Akun.
--
-- Kenapa tak seorang pun melaporkannya: FE memakai `if (res.ok)` tanpa
-- cabang else, jadi 500-nya GAGAL SENYAP — yang terlihat hanya nama tampilan
-- yang tak pernah muncul, bukan pesan galat. Kelas silent-fallback.
--
-- BUKAN korban recovery: tabel ini TIDAK tercantum di
-- RECOVERY_MISSING_TABLES_BACKLOG.md, dan nol `CREATE TABLE user_profiles`
-- di seluruh backend/migrations/. Ia memang tak pernah ada.
--
-- Kolom diambil PERSIS dari yang kode tulis/baca (`routers/user.py:136,171`),
-- bukan dari ingatan:
--   SELECT display_name FROM user_profiles WHERE user_id = $1
--   INSERT INTO user_profiles (user_id, display_name, updated_at)
--   VALUES ($1,$2,NOW()) ON CONFLICT (user_id) DO UPDATE ...
-- `ON CONFLICT (user_id)` menuntut user_id PRIMARY KEY/UNIQUE.
--
-- Tipe user_id = text, mengikuti PK tabel `"User"` (Prisma; terukur:
-- `"User".id` bertipe text dan MEMANG PRIMARY KEY). Tak ada tabel `users`
-- huruf kecil di database ini.
--
-- ON DELETE CASCADE: profil adalah data turunan milik satu pengguna; kalau
-- penggunanya hilang, barisnya tak punya arti. Nol dampak akuntansi —
-- tabel ini tak pernah menyentuh jurnal.
-- Idempoten (IF NOT EXISTS). Nol data migration.

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id      text PRIMARY KEY
                 REFERENCES "User"(id) ON DELETE CASCADE,
    display_name varchar(100),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
