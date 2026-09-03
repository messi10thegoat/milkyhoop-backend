# Rencana: migrasi `TEXT` → `uuid` untuk dua kolom id sesi chat

Tanggal ukur: 3 September 2026. Dasar kode: BE master `5886a7e7` (pembakuan
id sesi sudah aktif). **Dokumen ini RENCANA. Tidak ada DDL yang dijalankan.**

Kolom sasaran:

| tabel | kolom | tipe sekarang |
|---|---|---|
| `pending_actions` | `conversation_id` | `text` |
| `chat_workflow_state` | `chat_session_id` | `text` |

Keduanya menyimpan nilai yang sama dengan `chat_sessions.id`, yang bertipe
`uuid`. Satu identitas, dua tipe — akar cacat 3 Sep 2026 (153 baris terbaca
"yatim" padahal induknya hidup, semata karena huruf heks kapital).

---

## 1. Ukuran: apa yang masih bisa MASUK dari luar lewat HTTP?

Ditembakkan dari akun uji non-owner ke `POST /api/v3/chat/message`
(HTTP nyata ke kontainer, bukan in-process):

| yang dikirim klien | HTTP | yang mendarat di kolom |
|---|---|---|
| uuid huruf KAPITAL | 200 | uuid kanonik huruf kecil |
| uuid TANPA tanda hubung | 200 | uuid kanonik **berdash** |
| string kosong `""` | 200 | id baru yang dibangkitkan server |
| uuid dalam `{kurawal}` | **500** | tidak ada |
| sentinel `"unknown"` | **500** | tidak ada |
| teks sembarang | **500** | tidak ada |
| angka `"12345"` | **500** | tidak ada |

**Kesimpulan: NOL nilai non-uuid bisa mendarat di kedua kolom itu lewat HTTP.**
Batasnya sudah tertutup — tapi tertutup dengan cara yang salah.

### 1a. Temuan ikutan: penolakannya 500, bukan 422

`session_manager.py:309 get_or_create_session` menyisipkan ke
`chat_sessions` yang `id`-nya `uuid`; asyncpg melempar `DataError` dan
permintaan berakhir **500 Internal Server Error**.

```
ValueError: invalid UUID '12345': length must be between 32..36 characters, got 5
asyncpg.exceptions.DataError: invalid input for query argument $1: '12345'
```

Ini **BUKAN akibat pembakuan** (`bakukan_session_id` menangkap `ValueError`
dan meneruskan nilai apa adanya); ia perilaku lama — `chat_sessions.id` sudah
`uuid` sejak awal. Artinya klien yang mengirim id cacat mendapat "server kami
rusak", padahal yang salah adalah permintaannya.

## 2. Sensus nilai yang ADA di produksi

| ukuran | `pending_actions.conversation_id` | `chat_workflow_state.chat_session_id` |
|---|---|---|
| tidak bisa di-cast ke `uuid` | **0** | **0** |
| string kosong `''` | **0** | 0 |
| `NULL` | 4 | 0 |
| huruf kapital | **0** | **0** |

**Migrasi jadi murah**: nol baris yang akan menolak di-cast. `NULL` tidak
menghalangi (kolom tetap nullable). Tabelnya juga kecil sesudah pemangkasan
3 Sep (`pending_actions` puluhan baris, `chat_workflow_state` satuan), jadi
`ALTER TABLE` selesai dalam milidetik — bukan operasi yang menahan kunci lama.

## 3. Prasyarat SEBELUM migrasi (urutannya mengikat)

**P1. Tutup batas dengan 422, bukan 500.** Validasi `conversation_id` dan
`session_id` di `ChatMessageRequest` (dan saudara-saudaranya: `Confirm`,
`Cancel`, `Edit`) sebagai uuid opsional. Ini WAJIB lebih dulu karena:

- ia memperbaiki 500 yang sudah ada hari ini, terlepas dari migrasi;
- sesudah kolom jadi `uuid`, jalur yang hari ini "cuma" 500 di
  `chat_sessions` akan punya lebih banyak tempat untuk meledak;
- ia memindahkan penjagaan ke tepi, tempat pesan galat masih bisa berguna
  bagi klien.

Gerbangnya: kirim `"12345"` → harus **422**, dan kontrol `uuid` sah → 200.
Hari ini gerbang itu MERAH (500), jadi ia mengukur sesuatu.

**P2. Putuskan nasib sentinel `"unknown"`.** `tool_executor.py:2761` dan
`:3052` memakai `self.session_id or "unknown"`. Terukur: sentinel itu
**nol baris di seluruh riwayat** (3.790 baris di cadangan pra-hapus) — jalur
mati. Tapi setelah kolom jadi `uuid`, jalur mati itu berubah dari "menulis
teks aneh" jadi "melempar `DataError`". Pilihan: hapus sentinelnya (kembalikan
lebih awal kalau `session_id` kosong), atau biarkan dan terima ia jadi galat
keras. **Jangan migrasi sebelum ini diputuskan** — kalau tidak, kita memindahkan
jalur mati ke bentuk kegagalan yang lebih berisik tanpa memilihnya.

**P3. Pembakuan tetap dipakai walau kolomnya `uuid`.** Tipe `uuid`
membakukan yang TERSIMPAN, bukan yang DIBANDINGKAN di Python. Kode yang
menyusun daftar id (`_t171_baris_batch` memakai `= ANY($2::text[])`) tetap
harus lewat `bakukan_session_id`. Menghapus helper sesudah migrasi = mengulang
cacat yang sama dari sisi lain.

## 4. Migrasi (DDL — BELUM dijalankan)

Nomor V diambil dengan **fetch-before-apply**: `milkydb` dipakai bersama
beberapa sesi, dan nomor V bertabrakan. Saat diukur (3 Sep), repo sudah
memuat s/d `V235`, sedangkan DB baru mencatat s/d `V232` — jadi **V236 hanya
DUGAAN**; ambil ulang nomornya tepat sebelum menulis berkasnya.

```sql
-- V236__sesi_id_text_ke_uuid.sql
BEGIN;
-- Gagal-tutup: kalau ada satu saja baris yang tak bisa di-cast, batalkan
-- SEBELUM ALTER, dengan pesan yang menyebut jumlahnya.
DO $$
DECLARE n bigint;
BEGIN
  SELECT count(*) INTO n FROM pending_actions
   WHERE conversation_id IS NOT NULL AND conversation_id <> ''
     AND conversation_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
  IF n > 0 THEN RAISE EXCEPTION 'pending_actions: % baris tak bisa di-cast ke uuid', n; END IF;
  SELECT count(*) INTO n FROM chat_workflow_state
   WHERE chat_session_id IS NOT NULL AND chat_session_id <> ''
     AND chat_session_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
  IF n > 0 THEN RAISE EXCEPTION 'chat_workflow_state: % baris tak bisa di-cast ke uuid', n; END IF;
END $$;

UPDATE pending_actions SET conversation_id = NULL WHERE conversation_id = '';
UPDATE chat_workflow_state SET chat_session_id = NULL WHERE chat_session_id = '';

ALTER TABLE pending_actions
  ALTER COLUMN conversation_id TYPE uuid USING conversation_id::uuid;
ALTER TABLE chat_workflow_state
  ALTER COLUMN chat_session_id TYPE uuid USING chat_session_id::uuid;
COMMIT;
```

**Sengaja TIDAK ditambahkan: FOREIGN KEY ke `chat_sessions`.** Godaannya
besar, tapi FK akan membuat pemangkasan sesi berikutnya meng-CASCADE atau
menolak — perubahan perilaku yang jauh lebih besar daripada perubahan tipe,
dan harus diputuskan terpisah. Hari ini FK ke `chat_sessions` = NOL (diukur
3 Sep); rencana ini tidak mengubahnya.

```sql
-- V236__sesi_id_text_ke_uuid_ROLLBACK.sql
BEGIN;
ALTER TABLE pending_actions
  ALTER COLUMN conversation_id TYPE text USING conversation_id::text;
ALTER TABLE chat_workflow_state
  ALTER COLUMN chat_session_id TYPE text USING chat_session_id::text;
COMMIT;
```

Rollback-nya bersih dan tanpa kehilangan data: `uuid → text` selalu berhasil,
dan bentuk yang keluar adalah kanonik huruf kecil. Yang TIDAK dipulihkan
rollback: string kosong yang sudah dijadikan `NULL` di langkah maju. Nol baris
saat diukur, jadi hari ini biayanya nol — ukur ulang sebelum menjalankan.

## 5. Gerbang sesudah migrasi (harus bisa MERAH)

1. `scripts/cek_sesi_id_beda_huruf.py` harus tetap **hijau** — dan kontrol
   merahnya (`--kontrol`) kini akan gagal menyisipkan huruf kapital karena
   tipe kolom menolaknya. **Kontrol itu harus diubah**, kalau tidak ia berubah
   dari "membuktikan gerbang bekerja" jadi "selalu merah karena alasan lain".
2. `scripts/uji_bakukan_dua_ujung.py` harus tetap hijau tanpa diubah.
3. Gerbang batas P1: `"12345"` → 422 (bukan 500), `uuid` sah → 200.

## 6. Yang TIDAK diklaim dokumen ini

Semua angka di atas diukur pada 3 Sep 2026 di `milkydb` produksi. Tabel
`pending_actions` dan `chat_workflow_state` baru saja dipangkas hari itu,
jadi sensusnya kecil **karena riwayatnya baru dihapus**, bukan karena sistem
ini selalu bersih. Sebelum menjalankan migrasi, **ukur ulang** — sensus lama
adalah hipotesis, bukan fakta (Iron Law 34).
