# KONTRAK Penyesuaian Stok + Gudang standar SO (gelombang 2) — BACKEND, 9 Nov 2026

Status: **RANCANGAN; §4 butir 1–3 DIPUTUS pemilik langsung 9 Nov**. Kode baru ditulis setelah kontak-3 live.
Dasar: master ef04f22a7. Diukur read-only, dan skill inventory + ironlaws sudah dibaca.

Rujukan bentuk = Kas & Bank gelombang 1 (`KONTRAK-kasbank-standar-so-20261109.md` §0): ?aksi= = {k}_count, with_totals, position_text, aksi/aksi_semua, aksi_tersedia di dalam `data`, history, dan documents. Pratinjau memakai bentuk B8 `{ok, blocks, jurnal, …}` dalam transaksi READ ONLY. X-Idempotency-Key opsional.

**Tanpa aturan HPP/uang baru dan tanpa migrasi.**
- Nilai, WAC, akun, dan arah jurnal persis seperti hari ini. Penentu bersama hanya memindahkan kode posting/void yang ada supaya pratinjau == tulis.
- Hal yang MENGUBAH angka/stok dicantumkan di §4 sebagai pertanyaan untuk pemilik dan TIDAK dikerjakan tanpa jawabannya.

## 0. Hasil ukur (master ef04f22a7)

| # | Temuan | Dampak |
|---|---|---|
| U1 | Penyesuaian tidak punya pratinjau, idempotensi, ?aksi=, with_totals, position_text, aksi, history, maupun documents | celah standar SO |
| U2 | Posting memakai **gudang TERTUA** (`ORDER BY created_at LIMIT 1`), bukan gudang utama (`is_default`). Kolom `stock_adjustments.warehouse_id` ada (V043) tapi tak pernah diisi | stok bisa tercatat di gudang yang salah bila gudang utama ≠ gudang pertama. Kaos/nyawit hari ini: tertua == utama, belum ada salah tempat |
| U3 | `quantity_before` dan hitung ulang (recount) memakai stok **semua gudang**, sedangkan gerak ledger ditulis ke satu gudang | hitung ulang salah bila stok tersebar di >1 gudang |
| U4 | Nilai baris = harga pokok (WAC) **saat draf dibuat**, dibekukan; posting memakai nilai itu walau WAC sudah berubah | jurnal dan ledger konsisten satu sama lain, tetapi bisa berbeda dari WAC saat posting |
| U5 | Void hanya memeriksa periode **hari ini**; void modul lain (Kas&Bank, Biaya, bayar) juga memeriksa periode **tanggal dokumen** | ketidakseragaman Law 5 |
| U6 | Tanpa baris `fiscal_periods` untuk tanggal itu, penyesuaian DITOLAK ("Tidak ada periode akuntansi"), sedangkan modul lain menganggapnya terbuka | dibiarkan (perilaku lama, bukan celah keamanan) |
| U7 | `total_value`/`item_count` induk diisi trigger `trg_update_sa_totals` (diukur `pg_trigger`, ENABLED) | aman; jangan ditulis ulang di kode |
| U8 | ~~Gudang `set-default` tidak mencabut is_default gudang lain~~ **GUGUR (uji salinan 9 Nov):** trigger DB `trg_single_default_warehouse` (BEFORE INSERT/UPDATE WHEN is_default, ENABLED) sudah menjamin satu gudang utama; pohon lama juga tepat satu | tanpa perubahan; set-default hanya menolak gudang nonaktif (== aksi_tersedia) + saring tenant |
| U9 | Hapus gudang mengecek `warehouse_stock` (cache, floor 0) **tanpa saring tenant** | Law 24 + stok minus ledger lolos |
| U10 | Nomor jurnal = `SA-{nomor penyesuaian}` → bisa jadi "SA-SA-2611-0001" (kelas BUG-083) | kosmetik; ikut diperbaiki lewat `nomor_jurnal_dokumen` bila MASTER setuju |

## 1. Penyesuaian stok — `/api/stock-adjustments`

| Rute | Isi |
|---|---|
| `GET ""` | + `?aksi=` (`draf` pill; `bulan_ini` kartu), `with_totals` (`sum_total` = Σ total_value non-void). Baris + `position_text`, `jenis_text`, `aksi`, `aksi_semua`, `documents[{type}]`, `doc_count`. |
| `GET /summary` | + `{draf_count/_value, bulan_ini_count/_value}` + `search` (predikat sama dengan daftar). Medan lama tetap. |
| `GET /{id}` | + `position_text`, `jenis_text`, `aksi_tersedia{posting, ubah, hapus, batalkan}`. |
| `GET /{id}/history` | BARU: dibuat · diposting (posted_at/by) · dibatalkan (voided_at/by/reason). Ubah draf dan hapus draf tak berjejak (Law 34, diakui). |
| `GET /{id}/documents` | BARU: penyesuaian · jurnal (+pembalik) · gerak stok (baris inventory_ledger STOCK_ADJUSTMENT / _VOID). |
| `POST /preview` | BARU: pratinjau buat (baris: stok sebelum, sesudah, nilai; badan == POST). |
| `POST /{id}/edit/preview` | BARU: pratinjau ubah draf (== PATCH). |
| `POST /{id}/delete/preview` | BARU: pratinjau hapus draf (== DELETE). |
| `POST /{id}/post/preview` | BARU: jurnal (Dr/Cr persis seperti posting) + rencana gerak stok per barang (masuk/keluar, gudang, stok sebelum → sesudah) + `isyarat[]` (lihat di bawah). |
| `POST /{id}/void/preview` | BARU, badan `{reason}`: jurnal pembalik + cermin gerak stok + stok sesudah. Draf → catatan "Draf akan dihapus." |
| `POST ""`, `/{id}/post`, `/{id}/void` | + X-Idempotency-Key opsional (buat: `idem_buat.mulai`, tabel stock_adjustments; aksi: `mulai_aksi` STOCK_ADJ_POST / STOCK_ADJ_VOID). |

- **position_text:** "Draf · belum diposting" / "Diposting · {jenis_text}" / "Dibatalkan".
- **jenis_text:** increase "Penambahan" · decrease "Pengurangan" · recount "Hitung ulang" · damaged "Rusak" · expired "Kedaluwarsa".
- **aksi_tersedia:** alasan dari penentu tulis yang SAMA: status, periode (penentu lama `check_period_is_open` stock_adjustments, perilaku U6 dipertahankan), tanpa baris, dan peran akun belum diatur.
- **isyarat pratinjau posting** (BUKAN blok, perilaku tulis tak berubah):
  1. stok sesudah < 0 untuk pengurangan. Ledger boleh minus (Rule 7), jadi tetap diposting;
  2. WAC barang kini ≠ harga pokok draf (U4), dengan teks "Harga pokok berubah sejak draf dibuat (Rp a → Rp b); nilai memakai harga draf."
- Penentu bersama `services/stok_sesuai.py`: `rencana_posting`/`rencana_batal`. Tulis memakai `inventory_helpers.record_inventory_inbound` / baris keluar / `record_inventory_reversal` persis seperti hari ini.

## 2. Gudang — `/api/warehouses`

| Rute | Isi |
|---|---|
| `GET ""` | + `?aksi=` (`stok_minus` pill: ada barang dengan saldo ledger < 0 di gudang itu, yang tersembunyi oleh cache warehouse_stock floor 0; `nonaktif` kartu), `with_totals` (`sum_nilai` = Σ nilai stok, ekspresi WAC_GUDANG/NILAI_GUDANG yang SAMA dengan `/stock-value`). Baris + `position_text`, `aksi`, `aksi_semua`, `nilai_stok`. |
| `GET /summary` | BARU: `{stok_minus_count, nonaktif_count, total_count}`. |
| `GET /{id}` | + `position_text`, `aksi_tersedia{ubah, jadikan_utama, nonaktifkan, aktifkan}`. |
| `POST /{id}/set-default` | U8 gugur (trigger DB). Penentu sama dengan `aksi_tersedia.jadikan_utama`: gudang nonaktif ditolak; UPDATE bersaring tenant. |
| `DELETE /{id}` (nonaktifkan) | PERBAIKAN U9: cek stok dari **ledger** per gudang (saldo ≠ 0), bersaring tenant. Penentu sama dengan `aksi_tersedia.nonaktifkan`. Kalimat Indonesia. |
| history / documents | n/a: gudang = master data tanpa jejak audit hari ini (diakui, bukan dikarang). |

**position_text:** "Utama" / "Aktif" / "Nonaktif" (+ " · cabang" bila is_branch).

## 3. Izin

Pratinjau memakai huruf yang sama dengan tulisnya: buat C, ubah U, hapus D, posting P, batal V. Rute baca = R. Semua rute baru masuk `inventaris_rute_tulis.txt` + `gate_izin_rute`.

## 4. PERTANYAAN — dijawab sebelum kode

**DIPUTUS PEMILIK LANGSUNG (sesi BACKEND, 9 Nov 2026 malam):**
1. U4: **biarkan + isyarat**. Nilai tetap memakai harga pokok draf; pratinjau posting memberi isyarat bila WAC berubah.
2. U2/U3: **pilih gudang, bawaan UTAMA**. `warehouse_id` diterima; bawaan = is_default (cadangan gudang tertua); stok sebelum dan hitung ulang dihitung PER gudang.
3. U5: **samakan**. Void penyesuaian juga memeriksa periode tanggal penyesuaian.

(Pertanyaan asli:)
1. **U4 harga pokok penyesuaian.** Usul: TIDAK diubah gelombang ini; hanya isyarat di pratinjau. Alternatif (pola Accurate): nilai dihitung ulang dengan WAC saat posting.
2. **U2/U3 gudang penyesuaian.** Usul:
   - penyesuaian menerima `warehouse_id` (kolom sudah ada);
   - bawaan = gudang UTAMA (`is_default`, cadangan gudang tertua);
   - stok sebelum dan hitung ulang dihitung PER gudang itu.

   Ini mengubah stok/nilai pada tenant yang punya >1 gudang berisi.
3. **U5 void penyesuaian.** Usul: juga memeriksa periode tanggal penyesuaian, seragam dengan void lain (lebih ketat).

**DIPUTUS MASTER 9 Nov:**
- (4) Ya: nomor jurnal penyesuaian = nomor dokumen (`nomor_jurnal_dokumen`), HANYA data baru; uji salinan memastikan nomor lama tetap.
- (5) Setuju: penyesuaian pill `draf`; gudang pill `stok_minus`. `nonaktif` = filter/kartu, bukan pill.
- U8/U9 = perbaikan integritas, wajib dengan uji.

(Pertanyaan asli MASTER/FE:)
4. U10 nomor jurnal penyesuaian ikut pola `nomor_jurnal_dokumen` (jurnal = nomor dokumen, tanpa awalan ganda), hanya untuk data baru?
5. Pill penyesuaian: `draf` saja (kartu `bulan_ini`). Pill gudang: `stok_minus`. Setuju?

## 5. Bukti yang akan dilampirkan

- Uji unit penentu (pasangan Python⇔SQL ?aksi=, placeholder == argumen).
- Uji rute.
- Salinan dua klon (lama vs baru):
  - baca identik + aditif;
  - posting dan void penyesuaian di klon: jurnal + inventory_ledger + warehouse_stock IDENTIK lama vs baru;
  - pratinjau == tulis;
  - Inv-1 (warehouse_stock == GREATEST(0, Σ ledger)) dan Inv-3 sebelum/sesudah;
  - set-default menyisakan tepat 1 utama.
- Sabotase.
- Suite penuh.
- gate_izin_rute.
