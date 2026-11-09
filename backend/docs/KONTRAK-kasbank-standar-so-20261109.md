# KONTRAK Kas & Bank standar SO (gelombang 1) — BACKEND, 9 Nov 2026

Status: **DITERIMA MASTER 9 Nov** (§7 diputus); dikode di cabang feat/kasbank-standar-so. Dasar = master 1eb4bd09b; worktree baru dibuat sesudah kontak-2 LIVE.
Rujukan:
- pola SO / Biaya: `services/biaya_daftar`, `biaya_aksi`, `biaya_riwayat`, `schemas/aksi_tersedia`;
- B8: `services/kasbank_tulis`;
- B1/B2: `services/kasbank_ringkas`.

Putusan yang berlaku dan TIDAK diubah:
- B8 pemilik 6 Nov:
  - kas minus ditolak pada uang KELUAR;
  - hitung fisik memakai jumlah_fisik;
  - X-Idempotency-Key opsional.
- K3 (lingkup MASTER 6 Nov): void/koreksi TIDAK dikunci. Pratinjau void hanya menampilkan isyarat "saldo akan minus".

**TANPA aturan uang baru, TANPA migrasi.** Semua yang di bawah ini bersifat BACA atau pratinjau, kecuali tiga hal:
- void manual dan void transfer kini memakai penentu bersama;
- void manual dan void transfer menerima X-Idempotency-Key opsional;
- void transfer membaca saldo dengan saring tenant (dulu tanpa saring).

Perilaku tulis tetap sama, dengan dua pengecualian yang disengaja (lihat §5).

## 0. Bentuk umum (sama untuk ketiga halaman)

- **Uang** di medan BARU = string desimal 2 angka (`"6513370.00"`), sama dengan B1/B2/B8. Medan lama tetap apa adanya (float/int).
- **?aksi=k** (daftar) = saring dengan predikat SQL yang SAMA dengan `{k}_count` di `/summary`. Pasangan Python ⇔ SQL diuji (pola biaya_daftar).
- **with_totals=true** (daftar) → tambah total terfilter tanpa yang dibatalkan, untuk baris kaki tabel.
- **position_text** = teks status server per baris dan di `documents.anchor`, dari SATU penentu.
- **aksi** (kunci terdesak pertama atau null) + **aksi_semua** (daftar kunci) per baris = dari penentu yang sama dengan `?aksi=`.
- **aksi_tersedia** (detail, di DALAM `data` seperti kontak) = `{kunci: {tersedia, alasan}}` (`schemas/aksi_tersedia.AksiTersediaPeta`). Alasan berasal dari penentu yang SAMA dengan jalur tulis. Label tombol menjadi milik resep FE.
- **history** = `{…_id, …_number, events[], total, omitted[]}`, dengan ekor `so_riwayat._selesaikan` (nama aktor, zona tenant, terbaru dulu).
- **documents** = `{anchor, default, groups[{key, label, docs[]}]}`, bentuk `_dok` sama dengan biaya_riwayat.
- **Pratinjau** = `{success, data:{ok, blocks[{code, message}], jurnal[], saldo[{bank_account_id, account_name, sebelum, sesudah, minus}], payload}}`. Bentuknya sama dengan B8 `KT.keluaran` dan dijalankan dalam transaksi READ ONLY, jadi tidak menulis apa pun. Badan pratinjau SAMA dengan badan tulis.

## 1. Uang masuk/keluar (BARU: daftar sendiri) — `/api/bank-transactions`

Lingkup baris adalah transaksi manual sah, dengan predikat SAMA dengan penyaring asal void (`asal_sah` di kasbank_v2):
- origin_type='MANUAL';
- reference_type/id NULL;
- jurnal milik transaksi itu sendiri (source_type='BANK_TRANSACTION', source_id=bt.id).

Satu helper SQL dipakai bersama oleh daftar dan void. Baris cermin pembatalan (`manual_void`, SYSTEM) TIDAK menjadi baris daftar; baris itu muncul di documents/history.

| Rute | Isi |
|---|---|
| `GET /api/bank-transactions` | Query: `arah` (masuk\|keluar), `bank_account_id`, `status` (tercatat\|dibatalkan\|semua, **bawaan tercatat** = seragam tanpa_batal/BUG-141), `date_from`, `date_to`, `search` (nomor/keterangan/pihak), `aksi`, `with_totals`, `skip`, `limit` (≤100), `sort_by` (tanggal\|nominal), `sort_order`. Respons `{items, total, has_more, sum_masuk?, sum_keluar?, sum_neto?}`. |
| `GET /api/bank-transactions/summary` | Query `search` (predikat sama). Respons `{masuk_bulan_ini_count/_value, keluar_bulan_ini_count/_value, neto_bulan_ini_value, total_count}`. |
| `GET /api/bank-transactions/{id}` | Detail + `aksi_tersedia` + `position_text`. Bukan baris manual → 404 "Mutasi manual tidak ditemukan." |
| `GET /api/bank-transactions/{id}/history` | Kejadian: dicatat (jurnal created_at/created_by) · dibatalkan (voided_at, voided_by, void_reason). |
| `GET /api/bank-transactions/{id}/documents` | Kelompok: mutasi · jurnal (asli + pembalik). |
| `POST /api/bank-transactions/{id}/void/preview` | Badan `{reason}`, sama dengan void. Jurnal pembalik + saldo rekening sebelum/sesudah (+`minus`; isyarat K3, BUKAN blok). |
| `POST /api/bank-transactions/{id}/void` | (sudah ada) + X-Idempotency-Key opsional. Penentu = SAMA dengan pratinjau. |

Baris daftar:
- id;
- `nomor` = COALESCE(transaction_number, nomor jurnal), karena transaksi manual lama tak punya transaction_number;
- `transaction_date`;
- `arah` masuk|keluar;
- `amount` (string, positif);
- `bank_account` {id, account_name, account_type};
- `akun_lawan` {id, kode, nama}, dari baris jurnal yang bukan CoA rekening;
- `pihak` (teks bebas payee_payer atau null);
- `description`;
- `status` tercatat|dibatalkan;
- `position_text`;
- `aksi`, `aksi_semua`;
- `documents[{type}]`, `doc_count`;
- `journal_id`, `journal_number`.

**?aksi= kunci** (DIPUTUS MASTER 9 Nov):
- kartu `masuk_bulan_ini`, `keluar_bulan_ini`, plus `neto_bulan_ini_value`;
- pill: TIDAK ADA. Mutasi manual tak punya keadaan "perlu tindakan" (tanpa draf/lampiran); `dibatalkan` bukan desakan.

Baris `aksi` selalu null dan `aksi_semua` selalu [], tetap ada demi bentuk seragam.

**position_text:**
- "Masuk ke {rekening}" / "Keluar dari {rekening}";
- "Dibatalkan" (+" · {alasan ringkas}" bila ada, maks 60 karakter).

**aksi_tersedia** (urut tampil):
- `batalkan`: alasan dari penentu void, dengan urutan sama dengan tulis:
  1. rekonsiliasi;
  2. sudah dibatalkan;
  3. saldo awal;
  4. periode tanggal mutasi;
  5. periode hari ini (jurnal pembalik);
  6. jurnal sudah dibalik.
- `ubah`: SELALU tidak tersedia, dengan alasan "Mutasi tercatat tidak bisa diubah; batalkan lalu catat ulang." (Law 2).

## 2. Transfer — `/api/bank-transfers`

| Rute | Perubahan |
|---|---|
| `GET ""` | + `aksi`, `with_totals` (`sum_total` = Σ amount, `sum_biaya` = Σ fee_amount, tanpa void); baris + `position_text`, `aksi`, `aksi_semua`, `documents[{type}]`, `doc_count`. Medan lama tetap. |
| `GET /summary` | + `{draf_count/_value, bulan_ini_count/_value}` (aditif; draft_count lama tetap). Query `search` (predikat sama). |
| `GET /{id}` | + `aksi_tersedia`, `position_text`. |
| `GET /{id}/history` | BARU: dibuat · diposting (posted_at/by) · dibatalkan (voided_at/by, voided_reason). Draf yang dihapus tak berjejak (Law 34, diakui). |
| `GET /{id}/documents` | BARU: transfer · jurnal (asli + pembalik) · mutasi bank (keluar dari asal, masuk ke tujuan, + cerminnya bila batal). |
| `POST /{id}/void/preview` | BARU, badan `{reason}`. Draf → `ok=true`, jurnal kosong, `catatan` "Draf akan dihapus (belum berjurnal)." (sama dengan tulis: void draf = hapus). Posted → jurnal pembalik + saldo asal/tujuan (+`minus`, isyarat K3). |
| `POST /{id}/void` | + X-Idempotency-Key opsional; penentu SAMA dengan pratinjau. |

- **?aksi= kunci:** pill `draf` (perlu diposting); kartu `bulan_ini` (tanpa void).
- **position_text:** "Draf · belum diposting" / "Diposting · {asal} → {tujuan}" / "Dibatalkan".
- **aksi_tersedia:**
  - `posting`: alasan = penentu `rencana_posting_transfer`, termasuk KAS_MINUS, SALDO_TIDAK_CUKUP, dan periode;
  - `ubah` dan `hapus`: hanya draf;
  - `batalkan`: penentu void.

## 3. Rekening — `/api/bank-accounts`

| Rute | Perubahan |
|---|---|
| `GET ""` | + `aksi`, `with_totals` (`sum_saldo` = Σ saldo jurnal non-kartu, `sum_kewajiban_kartu`); baris + `saldo` (string), `position_text`, `aksi`, `aksi_semua`. Medan lama tetap (current_balance float, deprecated). |
| `GET /summary` | (B1, sudah ada) + `{saldo_minus_count, nonaktif_count}`. |
| `GET /{id}` | + `aksi_tersedia`, `position_text`. |
| `GET /{id}/history` | BARU: dibuat · saldo awal · penyesuaian/hitung fisik (jurnal ADJ) · dinonaktifkan/diaktifkan bila tercatat. Perubahan metadata TIDAK diaudit hari ini, jadi diakui di `omitted`. |
| documents | **n/a**: rekening bukan dokumen. Mutasinya = `/{id}/statement` + `/api/bank-transactions?bank_account_id=` (§1). |

- **?aksi= kunci:** pill `saldo_minus` (aktif, non-kartu, saldo jurnal < 0; bank overdraft, atau kas lama yang tercatat salah); kartu `nonaktif`.
- **position_text:**
  - "Nonaktif";
  - "Saldo minus" (non-kartu, < 0);
  - kartu kredit: "Terutang {Rp}" / "Lunas";
  - lainnya: "Aktif" (+" · utama" bila is_default).
- **aksi_tersedia:**
  - `uang_masuk`, `uang_keluar`, `transfer`, `hitung_fisik` → tidak tersedia bila nonaktif (alasan dari penentu B8 REKENING_NONAKTIF);
  - `uang_keluar` pada kas bersaldo ≤ 0 tetap TERSEDIA. Blok KAS_MINUS datang dari pratinjau per nominal, bukan dari tombol;
  - `ubah`;
  - `nonaktifkan` / `aktifkan`;
  - `hapus`: alasan dari penentu delete lama (hard bila tanpa transaksi, soft bila ada). Penentu itu di-ekstrak, bukan disalin.

## 4. Izin (permission_middleware, huruf sama dengan tulisnya)

- `GET /api/bank-transactions…` R;
- `POST /api/bank-transactions/{id}/void/preview` V (sama dengan void);
- `POST /api/bank-transfers/{id}/void/preview` V;
- `GET /api/bank-transfers/{id}/history|documents` R;
- `GET /api/bank-accounts/{id}/history` R.

Masuk ke `inventaris_rute_tulis.txt` + `test_izin_rute_pratinjau`; gerbang `gate_izin_rute`.

## 5. Perubahan perilaku tulis yang disengaja (BUKAN aturan uang)

1. **Void transfer:** blok periode tertutup kini **403 PERIODE_TERTUTUP**, seperti semua jalur lain (dulu 400). Saldo dibaca dengan saring tenant (Law 24; dulu subkueri jurnal tanpa tenant). Hasil jurnal dan cermin tetap sama.
2. **Void manual dan void transfer:** galat kini BERKODE (`GalatBerkode`: code + kalimat). Status HTTP lama tetap, kecuali butir 1.

## 6. Bukti yang akan dilampirkan (HASIL)

- Uji unit per penentu, termasuk pasangan Python⇔SQL ?aksi=.
- Uji rute bentuk kontrak.
- Sabotase per penentu.
- Salinan DB (dua klon, lama vs baru) pada kaos dan nyawit:
  - jumlah baris daftar manual = baris yang boleh di-void;
  - `sum_neto` daftar manual + jalur lain = saldo jurnal (cek silang B1);
  - void lewat pratinjau → tulis, dengan jurnal dan saldo sama persis dengan pratinjau.
- Suite penuh.
- gate_izin_rute.

## 7. Putusan MASTER 9 Nov (dulu pertanyaan terbuka)

1. Uang masuk/keluar TANPA pill; cukup kartu masuk/keluar bulan ini + neto.
2. Status bawaan daftar = `tercatat`; `semua` tetap tersedia sebagai pilihan.
3. Perubahan tulis §5 diterima dan dicatat di HASIL. Void tidak diblok (K3) bukan aturan baru.

## 8. Jawaban pertanyaan FE (CONVERSATIONAL, lewat MASTER 9 Nov)

1. **Rekening nonaktif di `GET /api/bank-accounts`.** Bawaan TETAP semua rekening (aktif + nonaktif); bawaan ini tidak diubah, dengan dua alasan:
   - Halaman lama (`api/queries/bank-accounts.ts:106`) memanggil tanpa parameter.
   - Kontak juga berbawaan semua: `customers.py` / `vendors.py` memakai `is_active` null = semua. Usul "seragam dengan kontak" justru berarti semua.

   Halaman baru mengirim **`?is_active=true`** (parameter yang SUDAH ada) untuk aktif saja; tanpa parameter berarti semua. TIDAK ada parameter `status` baru (nol medan baru). Kartu `nonaktif` di `/summary` + `?aksi=nonaktif` tetap seperti §3.
2. **Nama `type` dokumen.** `documents[{type}]` baris daftar sama dengan `kind` di `groups[].docs[]` bundel `/documents`:
   - `bank_transaction`: mutasi manual atau mutasi bank transfer, termasuk cermin batal (`batal=true` pada asli; `status` "pembalik" pada cermin);
   - `bank_transfer`: transfer itu sendiri;
   - `journal`: jurnal asli (`status` "diposting") + pembaliknya (`status` "pembalik").

   Kunci kelompok bundel:
   - mutasi manual: `mutasi` (label "Mutasi"), `jurnal` ("Jurnal");
   - transfer: `transfer` ("Transfer"), `jurnal` ("Jurnal"), `mutasi` ("Mutasi bank").
   - `default` = kelompok pertama, indeks 0.
3. **Teks asal/tujuan baris transfer.** Tidak ada medan baru. Pakai medan yang SUDAH ada, `from_bank_name` / `to_bank_name` (nama rekening, bukan uang). `position_text` posted = "Diposting · {asal} → {tujuan}".
4. **`?status=` daftar transfer.** YA, parameter ini SUDAH ada: `status` = `all` | `draft` | `posted` | `void`, bawaan `all` (tidak diubah). Nilainya Inggris karena mengikuti kolom DB; teks tampilnya = `position_text`.
   - `?aksi=draf` setara dengan `status=draft`, tapi dihitung di kartu `/summary`.

**T2 (putusan FE):** satu kolom Jumlah, arah di `position_text`, nol medan baru. Ini cocok dengan §1:
- `amount` selalu positif;
- `position_text` "Masuk ke {rekening}" / "Keluar dari {rekening}";
- `arah` tetap ada sebagai kunci saring (`?arah=`), bukan teks tampil.
