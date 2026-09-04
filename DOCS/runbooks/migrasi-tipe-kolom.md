# Runbook: mengubah TIPE sebuah kolom

Berlaku untuk `ALTER COLUMN ... TYPE ...` pada kolom yang sudah dipakai kode
berjalan. Ditulis 3 September 2026 setelah V236 (`text` → `uuid` untuk
`pending_actions.conversation_id` dan `chat_workflow_state.chat_session_id`).

## Langkah 0 (WAJIB): sisir cast EKSPLISIT ke tipe LAMA

**Sebelum menyentuh DDL apa pun**, cari setiap kueri yang memaksa kolom itu ke
tipe lamanya:

```bash
grep -rn "<kolom> = ANY(\$[0-9]*::<tipe_lama>\[\]\)\|<kolom> = \$[0-9]*::<tipe_lama>\|<kolom>::<tipe_lama>" \
     --include=*.py --include=*.sql .
```

**Kenapa ini langkah 0 dan bukan pemeriksaan sesudahnya.** Inferensi parameter
menolong bentuk polos: `WHERE kolom = $1` tetap bekerja, karena driver melihat
tipe kolom dan mengirim nilainya sesuai. Tapi inferensi **tidak menolong cast
yang kau tulis sendiri**. Setelah tipenya berubah, `WHERE kolom = $1::text`
menjadi `uuid = text` — operator yang tidak ada, dan kuerinya galat:

```
ERROR:  operator does not exist: uuid = text
```

V236 memecahkan tiga kueri dengan cara ini (`orchestrator.py` ×2,
`unified_chat.py` ×1) dan ketiganya lolos dari pembacaan kode. Perbaikannya
harus masuk **commit yang sama** dengan migrasinya — kalau tidak, ada jendela
saat trunk memuat migrasi tanpa perbaikannya.

## Membaca kode TIDAK CUKUP — tembakkan kuerinya

Ketiga kerusakan di atas ditemukan dengan menjalankan bentuk kuerinya langsung
ke basis data **sesudah** migrasi, bukan dengan membacanya:

```sql
SELECT 1 FROM <tabel> WHERE <kolom> = ANY(ARRAY['<nilai>']::text[]) LIMIT 1;
```

Dua dari tiga tidak akan tertangkap oleh pembacaan. Suite unit juga tidak
menangkapnya (ia tidak menyentuh basis data), dan `healthz` tetap 200 karena
yang patah hanya sebagian jalur.

## Gerbang yang ikut mati karena migrasi

Perubahan tipe bisa membunuh penegak yang menjaga kolom itu. Setelah V236,
`cek_sesi_id_beda_huruf.py` **meledak** (`lower(uuid)` bukan fungsi yang ada) —
bukan merah, tapi galat yang tak berhubungan dengan yang diukur. Itu sama tak
bergunanya dengan gerbang yang tak bisa merah.

Setelah mengubah tipe, jalankan setiap penegak yang menyentuh kolom itu, lalu:

- kalau ia meledak → **tulis ulang**, jangan tambal;
- kalau kontrol merahnya jadi mustahil → **katakan begitu**, jangan pura-pura
  merah. Sesudah V236, mencabut pembakuan di sisi baca tak lagi membuat
  pencarian meleset (Postgres membakukan sendiri saat mencocokkan `uuid`), jadi
  kontrol negatif itu memang tak bisa merah lagi. Itu fakta, bukan kerusakan.

## Urutan yang dipakai V236

1. Sisir cast eksplisit (langkah 0).
2. Prasyarat kode lebih dulu: tolak masukan cacat di tepi (422, bukan 500), dan
   putuskan nasib nilai sentinel — sesudah tipe berubah, sentinel berubah dari
   "menulis nilai aneh" jadi galat keras.
3. **Ukur ulang sensusnya**, jangan pakai angka lama. Sensus lama adalah
   hipotesis (Iron Law 34).
4. `pg_dump` lebih dulu; verifikasi ISI cadangannya, bukan keberadaannya.
5. Migrasi dalam SATU transaksi dengan penjaga gagal-tutup yang `RAISE`
   memakai **jumlah baris** yang menyalahi, sebelum `ALTER` dijalankan.
6. Tulis berkas `_ROLLBACK.sql` yang benar-benar bisa jalan, dan sebutkan apa
   yang TIDAK dipulihkannya.
7. Nomor `V` diambil **fetch-before-apply** — `milkydb` dipakai bersama, dan
   nomor di repo bisa lebih tinggi daripada yang tercatat di DB. Catat di
   `schema_migrations` dengan checksum **md5** (konvensi baris-baris lain).
8. Verifikasi lewat **HTTP ke jalur yang benar-benar dipakai**, bukan lewat
   suite unit saja.

---

# Membaca respons daftar: kosong bukan berarti tidak ada

Diukur 5 September 2026 setelah sebuah laporan bug yang ternyata salah baca.

**Daftar kosong WAJIB dibaca bersama `total` dan `has_more`.** Halaman yang
melewati ujung data menjawab `200` dengan `data: []` — dan itu BENAR:

```
GET /api/journals?limit=100&page=1   -> 200, 100 baris, total=328, has_more=true
GET /api/journals?limit=100&page=4   -> 200,  28 baris, total=328, has_more=false
GET /api/journals?limit=100&page=99  -> 200,   0 baris, total=328, has_more=false
```

Responsnya tidak berbohong; `total` tetap 328. Yang menipu adalah klien yang
membaca `data` sendirian lalu menyimpulkan "tidak ada data". Sebuah laporan bug
lahir dari situ dan sempat dibaca sebagai "`limit>100` mengembalikan daftar
kosong" — padahal `limit>100` sudah ditolak `422` oleh `Query(..., le=N)`,
terbukti dari log akses produksi (`/api/sales-orders?limit=200` → 422).

## Jangan seragamkan `le=` tanpa mengukur pemakainya

Nilai `le=` memang BERBEDA per endpoint dan itu disengaja: 82× `le=100`,
39× `le=200`, 15× `le=50`, 6× `le=500`, 3× `le=2000`. Menyeragamkannya ke 100
akan mematikan lalu lintas yang sah — terukur dalam 48 jam:
`/api/production/fg-receipts?limit=500` → 200 (8×),
`/api/production/material-issues?limit=500` → 200 (7×), `/api/accounts` → 200.

Ini bentuk lain dari pelajaran yang sama: **mengetatkan kontrak tanpa memeriksa
siapa yang sudah memakainya.** Sebelum mengubah batas apa pun, baca log akses.
