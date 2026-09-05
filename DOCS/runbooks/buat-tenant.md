# RUNBOOK — Membuat tenant baru (produksi)

**JANGAN `INSERT` langsung.** Jalur sah `services/onboarding_service.py`
→ `create_tenant_and_user` mengisi dalam SATU transaksi: `"Tenant"`,
`User."tenantId"` + `last_active_tenant_id`, `user_tenant_roles`, lalu
`seed_default_coa`, `seed_default_account_roles`, `seed_default_tax_codes`,
dan gudang bawaan. Menyisipkan sendiri melahirkan tenant setengah jadi yang
gejalanya baru muncul minggu depan (lihat memori `signup-owner-role-missing`:
onboarding pernah tak menyisipkan peran OWNER → sidebar kosong).

## Dua hal yang memakan waktu kalau tidak tahu

1. **Prefiksnya `/api/auth/signup`, BUKAN `/api/signup`.** Salah prefiks
   memberi `401 MISSING_TOKEN` — terbaca seperti masalah izin, padahal
   rutenya memang tidak ada di situ.
2. **Kode verifikasi tersimpan TER-HASH** di `pending_registrations`, jadi
   tidak bisa dibaca dari basis data. Yang bisa dipakai `magic_token` di baris
   yang sama — tautan yang SAMA dengan yang diklik pemilik dari emailnya.

## Urutan

```bash
POST /api/auth/signup/register        {"email": "..."}          # -> 200
# ambil magic_token (bukan kodenya):
docker exec -i milkyhoop-dev-postgres-1 psql -U postgres -d milkydb -At -c \
  "SELECT magic_token FROM pending_registrations WHERE email='...' ORDER BY created_at DESC LIMIT 1"
GET  /api/auth/signup/verify-link/<magic_token>                 # -> 302, Location memuat ?token=<setup_token>
POST /api/auth/signup/complete-setup  {"password","business_name"}   # Authorization: Bearer <setup_token>
```

Medan wajib hanya **email, password, business_name** (min 2 huruf). Zona waktu
dan mata uang TIDAK diminta — jangan mengarang nilai untuk medan yang tidak
ada. **Slug diturunkan dari `business_name` dan PERMANEN**: jangan mengarang
namanya; kalau pemilik belum menyebutkan, berhenti dan tanyakan.

## Kredensial

Jangan tulis ke repo, runbook, atau pesan commit. Alirkan lewat **stdin** —
argumen baris perintah terlihat di `ps` dan tersimpan di riwayat shell. Pilihan
paling aman: **tidak menyimpannya sama sekali**; pemilik sudah memilikinya.

## Gerbang — buktikan lewat HTTP, bukan dari DB saja

- `POST /api/auth/login` → 200 + token
- `GET /api/dashboard/all` → 200
- peran: `user_tenant_roles` ⋈ `roles` → `Owner`, `status='ACTIVE'`,
  `is_primary=true` **DAN** `User."tenantId"` terisi. Keduanya, bukan salah
  satu: login membaca `User."tenantId"`, jadi baris peran sendirian tetap
  `409 ROLE_NOT_PROVISIONED`.
- bagan akun tidak kosong (rujukan: 71 akun, 9 kode pajak, 1 gudang)
- isolasi: dari akun baru, dokumen tenant lain **0** (sebut angka pembandingnya)

⚠️ Nama tabelnya `"User"` HURUF BESAR (gaya Prisma), bukan `users`; dan
`user_tenant_roles` menyimpan `role_id`, bukan kolom `role`. Gerbang versi
pertama salah keduanya dan melapor MERAH untuk data yang sehat — padahal login
dan dashboard sudah 200, yang mustahil kalau perannya hilang. **Hasil gerbang
yang bertentangan dengan fakta lain wajib dicurigai lebih dulu**, sama seperti
gerbang hijau yang terlalu rapi.
