# KONTRAK BE — Uang Muka Pembelian seragam (P-U3, sisi BACA) — 22 Okt 2026

Kembaran uang muka Penjualan (`customer_deposits`, S2-UM). Aditif, dihitung SERVER, tanpa migrasi. Pola acuan: NetSuite Vendor Prepayment (Not Applied / Partially Applied / Fully Applied). Data produksi = 0 baris → bukti: sintetis (CTE) + uji tulis 'TES E2E' kaos.

## Per baris daftar `GET /api/vendor-deposits` + detail `GET /api/vendor-deposits/{id}` (paritas, fungsi sama `services/uang_muka_vendor_daftar`)
- `sisa` (float; null untuk draf/void) · `terpakai` · `dikembalikan` = TURUNAN JURNAL (akun peran AP_PREPAID; jurnal efektif VENDOR_DEPOSIT / DEPOSIT_APPLICATION / DEPOSIT_REFUND), BUKAN kolom cache `remaining_amount`/`applied_amount` (kolom lama tetap, tak diubah).
- `sisa_keadaan` ∈ `draf · utuh · sebagian · terpakai · dikembalikan · sebagian_dikembalikan · batal`; `status_text` == `position_text` ∈ `Draf · Belum diterapkan · Diterapkan sebagian · Diterapkan penuh · Dikembalikan · Dikembalikan sebagian · Dibatalkan`.
- `aksi` (pill, satu kunci) = `belum_diterapkan` bila terbit (bukan draf/void) ∧ sisa jurnal > 0, selain itu null.
- `documents[{type}]`/`doc_count` = bundel tanpa rekap, jenis `vendor_deposit` · `purchase_order` · `bill` (SATU definisi dgn bundel).

## Daftar
`?search=` (no. uang muka, vendor [nama/kode], referensi, catatan, no. PO; semua kata wajib cocok; predikat SAMA dgn `/summary?search=`) · `?aksi=belum_diterapkan` · `?with_totals=true` → `sum_total` (Σ amount tanpa void) + `sum_sisa` (Σ sisa jurnal terbit) atas SEMUA hasil filter · `page/per_page/offset` di samping `skip/limit` (prioritas offset > page > skip) + `has_more` · urutan deterministik (`ORDER BY <sort>, vd.id`; `sort_by` ∈ deposit_date·deposit_number·amount·created_at).

## Summary `GET /api/vendor-deposits/summary`
Medan lama dipertahankan tetapi dari jurnal: `total_deposits` (Σ amount tanpa void), `total_applied` (Σ terpakai), `total_remaining` (Σ sisa), `deposit_count` (tanpa void), `pending_count` (== `belum_diterapkan_count`). Tambah: `draft_count`, `void_count`, `belum_diterapkan_count/value`. `?search=` membatasi semuanya.

## Bundel `GET /api/vendor-deposits/{id}/documents`
Skema sama dgn bundel SO/faktur `{anchor, default, groups[{key,label,docs[]}]}`; grup `um` (deposit itu sendiri; draf=`draf`, void=`batal`) · `po` (PO bertaut) · `faktur` (tagihan yang dikenai penerapan). `anchor.kind="vendor_deposit"`, `total`=jumlah, `paid`=terpakai, `balance`=sisa (null untuk draf/void). Izin: `vendor_deposit` R.

## Temuan yang dibereskan di sini
Detail `GET /vendor-deposits/{id}` DIPARKIR (409) karena dua fungsi DB RUSAK sejak dibuat: `get_vendor_deposit_applications` (kolom `b.bill_number` tak ada) dan `get_vendor_deposit_refunds` (tipe BIGINT ≠ numeric). Diganti kueri langsung (`SQL_APLIKASI`/`SQL_REFUND`, tenant eksplisit) dan detail dibuka. `by-vendor/{id}` dan `available/{id}` tetap diparkir (menunggu unit tulis). Summary lama (fungsi DB atas kolom cache BIGINT) tidak dipakai lagi.

## Belum (unit tulis berikut)
Unapply (lepas penerapan) agar deposit berpenerapan bisa di-void; idempotensi create/post/apply/refund; nominal 2 desimal (`amount: int` di request); unpark by-vendor/available.

## Sisi TULIS (P-U3b) — LIVE 22 Okt 2026 (izin pemilik LANGSUNG: aturan jurnal lepas, V437, kode, V438)
Acuan: QBO/Xero "unapply" prepayment, NetSuite Vendor Prepayment Application (unapply = reversal, bukan hapus). Kembaran pelanggan:
`POST /customer-deposits/{id}/applications/{app_id}/reverse` (FIX_P1_DEPOSIT opsi B).

### Pengukuran (baca-saja, 22 Okt)
- `vendor_deposits` = 0 baris, `vendor_deposit_applications` = 0 baris di produksi → migrasi berisiko rendah, tak ada data dipulihkan.
- Trigger `update_vendor_deposit_status` (applications + refunds): variabel **BIGINT** (sen terpotong, Law 9); menjumlah SEMUA penerapan
  (penerapan yang sudah dilepas tetap terhitung); status tak pernah kembali ke `posted` (`ELSE status`).
- `get_bill_remaining_from_journal` (vendor_deposits.py): tanpa saring `reversed_by_id`, dan `int()` memotong sen. Sesudah ada lepas,
  jurnal asli tetap terhitung → sisa tagihan terlalu kecil. **Diganti `compute_ap_outstanding` per tagihan** (arap Rule 5: satu sumber;
  bagian 6 sudah menyaring `reversed_by_id IS NULL`).
- Penjaga `guard_arap_requires_obligation`: `DEPOSIT_APPLICATION` TIDAK di daftar putih → jurnal lepas (Cr Utang) WAJIB ber-`source_id` =
  id tagihan (kewajiban yang nyata ada), sama dengan opsi B pelanggan. Sisa uang muka tetap benar: `SQL_AGREGAT` membuang jurnal asli
  (`reversed_by_id`) DAN pembaliknya (`reversal_of_id`) → hanya jurnal VENDOR_DEPOSIT + penerapan hidup yang tersisa.
- Void deposit ditolak bila "ada penerapan" (C5); penentunya harus mengabaikan penerapan berstatus `reversed`.

### Migrasi (nomor BACKEND2: V437, V438) — keduanya butuh izin pemilik langsung
- **V437 `vendor_deposit_applications_lepas`** (tambah saja, expand): kolom `status varchar NOT NULL DEFAULT 'active'` CHECK (`active`,`reversed`),
  `reversed_by_id uuid` (FK journal_entries), `reversed_at timestamptz`; `CREATE OR REPLACE update_vendor_deposit_status`: NUMERIC,
  hanya penerapan `status='active'`, status kembali `posted` bila terpakai+refund = 0 (dan bukan draft/void). ROLLBACK = fungsi lama + DROP kolom.
- **V438 `drop_fungsi_um_vendor_rusak`** (contract): DROP kelima fungsi `get_vendor_deposit_applications(uuid)`, `get_vendor_deposit_refunds(uuid)`,
  `get_vendor_deposits(uuid,varchar)`, `get_available_vendor_deposits(uuid)`, `get_vendor_deposit_summary(text)`. Diukur: 4 galat saat dipanggil
  (kolom hantu `b.bill_number`; BIGINT ≠ numeric(18,2)), summary membaca kolom cache BIGINT (bukan jurnal). Nol pemanggil (grep kode +
  `pg_proc.prosrc` + `pg_views`). ROLLBACK = definisi V054 apa adanya.

### Endpoint
- **`POST /vendor-deposits/{id}/applications/{app_id}/unapply`** (izin `vendor_deposit` U) — lepas SATU penerapan.
  Kunci: `VENDOR_DEPOSIT_SALDO:{deposit}` (sama dgn apply/refund/void) → penerapan FOR UPDATE → tagihan FOR UPDATE. Law 5: periode tanggal
  bisnis hari ini terbuka. Jurnal pembalik DRAFT→POSTED: `source_type='DEPOSIT_APPLICATION'`, `source_id`=tagihan, `reversal_of_id`=jurnal
  asli, baris ditukar (Dr Uang Muka Pembelian / Cr Utang Usaha); jurnal asli tetap POSTED + `reversed_by_id` (Law 2/26). Penerapan →
  `status='reversed'`, `reversed_by_id`, `reversed_at` (trigger menyegarkan cache deposit). `segarkan_cache_hutang_tagihan(tagihan)`.
  Riwayat `VD_UNAPPLIED`. **Idempoten alami**: lepas kedua = 200 "sudah dilepas" + jurnal pembalik yang sama, nol jurnal baru.
  404 bila penerapan bukan milik deposit/tenant; 409 `PERIODE_TERTUTUP`.
- **Idempotensi (Law 14)** header `X-Idempotency-Key` (pola idem_buat): create `VD_CREATE` (SUMBER_BUAT vendor_deposits: deposit terhapus →
  kunci basi), `VD_POST`, `VD_APPLY`, `VD_REFUND` via `mulai_aksi`/`simpan`. Lock tetap (anti-race) — lock ≠ dedup.
- **Nominal 2 desimal**: request `amount` Decimal (>0, 2 desimal, ditolak 422 bila lebih), hitungan Decimal HALF_UP; respons float
  (pola PO). `ApplyDepositResponse.deposit_remaining`/`bill_remaining` dari jurnal (`UMV.sisa_jurnal`, `compute_ap_outstanding`), bukan cache.
- **Buka parkir** `GET /by-vendor/{vendor_id}` dan `GET /available/{vendor_id}`: kueri langsung + `fakta_vd` (sisa jurnal), tenant eksplisit;
  `available` = predikat pill `belum_diterapkan` YANG SAMA (sql_aksi). Bentuk respons float.

### Gerbang
Unit: lepas (jurnal pembalik seimbang, source_id tagihan, idempoten), trigger NUMERIC (sen), void sesudah lepas boleh, apply/refund replay
kunci sama = respons asli. Harness nyata: skema nyata + sintetis (CTE), kontrol merah (sabotase `reversed_by_id`). Nyata kaos "TES E2E":
buat→post→apply→unapply→void → sisa jurnal == layar == DB, AP tagihan pulih.

### Rilis (pemetaan sha -> uji yang BENAR-BENAR lulus; judul commit "wip ... belum diuji" di master ditulis SEBELUM uji, riwayat tak ditulis ulang)
| langkah | sha / objek | waktu (UTC) | uji yang lulus |
|---|---|---|---|
| bacaan sebelum | /root/apum-sebelum.txt | 11:18:12 | AP/UM per tenant (grapgrap, kaos) |
| V437 migrasi-saja | f7d290bd (merge_branch) + apply_mig | 11:28:50 | gerbang 5112 passed; kolom + constraint + fungsi NUMERIC/aktif/kembali posted terverifikasi |
| kode P-U3b | 949ea413 (f846883d "wip", 29d38f1a "wip", 949ea413) | restart 11:36:27 | tes terfokus 197; harness scratch uji-nyata-um-tulis 24/24 (pemicu nyata) + kontrol merah 6 FAIL; gerbang 5134 passed; marker + healthz + 401 |
| V438 DROP | apply_mig --require-marker | 11:40:45 | nol pemanggil (pg_proc/pg_views/pg_matviews/pg_trigger/pg_depend = 0; git grep hanya dok/harness); 5 fungsi = 0 |
| bacaan sesudah | /root/apum-sesudah.txt | ~11:41 | IDENTIK dgn sebelum; harness read-only um-vendor-seragam, pembelian-p0, po-seragam, po-pratinjau, pdf-po gagal=0 |

### Dokumen tagihan di bundel (22 Okt 2026, sesudah kontrak bills BACKEND 95d7b901)
Bundel uang muka vendor (dan PO) memakai `po_daftar.dok_bill` = `tagihan_daftar.dok_tagihan` dengan keadaan `TD.sql_keadaan_dok()`:
status draf | batal | lunas | diposting + `paid` — SAMA dengan bundel tagihan sendiri (dulu tagihan lunas tampil "diposting").
Penjaga: harness `uji-nyata-dok-bill` (setiap tagihan nyata: dok bundel PO/UM == dok bundel tagihan).
