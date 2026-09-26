# Desain: tautan bagikan PDF proforma (WA) — 26 Sep 2026

Status: USULAN untuk review BACKEND3 (keamanan) SEBELUM kode ditulis. Penulis: BACKEND. Permintaan: MASTER (pemilik: CW SO
menerbitkan + membagikan proforma dari detail SO lewat WhatsApp).

## Tujuan
`GET /api/proformas/{id}/share-link` (dipanggil pengguna berlogin) → `{url, expires_at}`: tautan PUBLIK yang bisa dibuka
pelanggan TANPA login, hanya menyajikan PDF SATU dokumen, kedaluwarsa, bisa dicabut, tak bisa ditebak, dibatasi laju.

## Terukur (kode master 0cca1a2c)
- `permission_middleware.SKIP_PATTERNS` sudah memuat `^/api/public` (lepas cek IZIN MODUL) — tapi `AuthMiddleware` memakai
  `public_paths` EKSPLISIT + pengecualian khusus (`_is_public_invite`, tenant info GET) → rute publik baru WAJIB didaftarkan
  eksplisit di AuthMiddleware (regex sempit, GET saja), bukan prefiks.
- `RateLimitMiddleware` (Redis sliding window, per-IP/per-user; `_get_limits` punya kelas ketat untuk rute auth) → tambah
  kelas ketat untuk `/api/public/share/`.
- Pola token publik yang sudah ada: `invite_public` (`/api/invite/{token}`).
- PDF proforma: `pdf_service.generate_proforma_pdf` (kini bertanda DRAF/DIBATALKAN).

## Rancangan
### Data (migrasi baru — nomor dari MASTER)
`document_share_links(id uuid PK, tenant_id text NOT NULL, doc_type text NOT NULL CHECK (doc_type IN ('proforma')),
doc_id uuid NOT NULL, token_hash char(64) NOT NULL UNIQUE, created_by uuid, created_at timestamptz DEFAULT now(),
expires_at timestamptz NOT NULL, revoked_at timestamptz, revoked_by uuid, last_accessed_at timestamptz, access_count int DEFAULT 0)`
+ index (tenant_id, doc_type, doc_id). **Token TIDAK disimpan** — hanya sha256(token) (bocor DB ≠ bocor tautan).

### Rute BERLOGIN (izin modul penjualan/proforma seperti rute proforma lain)
- `POST /api/proformas/{id}/share-link` body opsional `{days}` (1–14, bawaan 7) → 201 `{id, url, expires_at}`.
  Tolak 409 bila status ≠ `issued` (draf/batal tak boleh dibagikan). Token = `secrets.token_urlsafe(32)` (256 bit), dikembalikan
  SEKALI. Maks 5 tautan aktif per dokumen (409 sesudahnya). Audit `audit_logs` SHARE_LINK_CREATED (tanpa token).
- `GET /api/proformas/{id}/share-links` → daftar {id, created_at, expires_at, revoked_at, last_accessed_at, access_count} (tanpa token).
- `DELETE /api/share-links/{id}` → cabut (revoked_at); berpagar tenant; audit SHARE_LINK_REVOKED.
- Respons proforma (detail + daftar per SO) ditambah `customer_phone` (customers.mobile_phone → telepon) untuk wa.me di FE.

### Rute PUBLIK
`GET /api/public/share/{token}` (regex token `^[A-Za-z0-9_-]{43}$`, GET saja):
1. Pembatas laju per-IP ketat (usul 30/menit, 300/jam) + log kegagalan.
2. Cari baris lewat `token_hash = sha256(token)` (tak ada perbandingan string token → tak ada kebocoran waktu per karakter).
3. Tidak ada / dicabut / kedaluwarsa / dokumen tak lagi `issued` → **404 seragam** (tak ada oracle keadaan). (Pesan ramah di sisi
   pemilik lewat daftar tautan.)
4. tenant_id DIAMBIL dari baris tautan (tak ada input tenant dari luar — Law 24); render PDF via generator yang sama.
5. Header: `Content-Type: application/pdf`, `Content-Disposition: inline; filename=...`, `Cache-Control: no-store`,
   `X-Robots-Tag: noindex, nofollow`, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`.
6. UPDATE last_accessed_at/access_count (satu baris, bukan jurnal).
Tanpa daftar, tanpa pencarian, tanpa id dokumen di URL.

### Iron Laws / keamanan
- Tak menyentuh jurnal (Law 1–5/20 tak berlaku); Law 24 tenant dari baris; Law 12 audit append-only; Law 32 pool.
- Ancaman: tebak token (256 bit + laju), bocor lewat terusan WA (diterima: kedaluwarsa + cabut + hanya dokumen itu),
  cache/peramban (no-store), mesin cari (noindex), enumerasi (404 seragam), dokumen dibatalkan sesudah dibagikan (404).
- Uji: token acak 404; token dicabut/kedaluwarsa/dokumen batal 404; draf tak bisa dibuat (409); tenant lain tak bisa mencabut;
  laju terlampaui 429; tak ada token di respons daftar/audit; AuthMiddleware: hanya pola sempit yang publik (uji `/api/public/share/x/../` dll).

## Pertanyaan untuk review
1. 404 seragam vs 410 untuk dokumen dibatalkan? (usul 404 seragam)
2. Batas laju yang pas untuk pelanggan membuka ulang?
3. Nomor migrasi.
