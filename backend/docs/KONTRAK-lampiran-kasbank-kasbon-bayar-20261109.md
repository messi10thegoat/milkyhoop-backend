# KONTRAK Lampiran Kas & Bank + Kasbon + Penerimaan/Pembayaran keluar — BACKEND, 9 Nov 2026

Status: **DIKODE** (cabang feat/lampiran-kasbank-kasbon-bayar). V493 LIVE (MASTER, master 7dac48377).
Dasar: master df1ff2620 (+kontak-4). Unit LAMPIRAN disetujui pemilik; temuan ukur dari WORKSPACE.

## 0. Hasil ukur

- **Pabrik ada:** `routers/lampiran_induk.buat_router(SpecLampiran)`. Sudah dipakai NK, penawaran, PO, ND, GRN, UM pembelian; SO/SJ memakai kontrak lama.
  - Bentuk: `files[]` 1..n, maks 10 berkas × 10 MB.
  - Isi: sukses sebagian per berkas, MinIO, nama per tautan (V411), lepas = audit, tenant eksplisit.
- **Constraint** `document_attachments.chk_da_entity` (diukur dari pg_constraint) TIDAK memuat `bank_transaction`, `bank_transfer`, `employee_advance`.
- **`_ENTITAS_HUB`** (hub `documents.py`) juga tidak memuat ketiganya.
- **`payment`** dipakai dua tabel (`receive_payments`, `bill_payments_v2`). Hari ini lampirannya lewat hub 2 langkah per berkas (unggah ke /documents lalu attach).
- **Mutasi manual** tak punya `transaction_number` (kaos/nyawit 24/24 NULL), jadi nomornya = nomor jurnal. Hitung fisik = baris `bank_transactions` jenis adjustment.

## 1. Rute (pabrik yang SAMA; bentuk respons identik dengan lampiran NK/PO)

| Modul | Rute dasar | entity_type | Tabel induk | Nomor | Izin (huruf tulis/baca) |
|---|---|---|---|---|---|
| Mutasi masuk/keluar + hitung fisik | `/api/bank-transactions/{id}/attachments` | `bank_transaction` (BARU) | `bank_transactions` | transaction_number ?? nomor jurnal | kas_bank U / R / D |
| Transfer | `/api/bank-transfers/{id}/attachments` | `bank_transfer` (BARU) | `bank_transfers` | transfer_number | kas_bank U / R / D |
| Kasbon | `/api/employee-advances/{id}/attachments` | `employee_advance` (BARU) | `employee_advances` | advance_number | huruf modul kasbon (sama dengan PATCH-nya) + pay-group (aturan employee_id) |
| Penerimaan pembayaran | `/api/receive-payments/{id}/attachments` | `payment` (lama) | `receive_payments` | payment_number | modul penerimaan |
| Pembayaran keluar | `/api/bill-payments/{id}/attachments` | `payment` (lama) | `bill_payments_v2` | payment_number | modul pembayaran keluar |

Tiap rute dasar punya empat bentuk:
- `POST …/attachments`: multipart `files`, menjawab `{success, hasil[{nama, ok, id?, galat?}], kuota{terpakai, maks}}`;
- `GET …/attachments`: menjawab `{data[{id, nama, ukuran, mime, url, tersedia, diunggah}], kuota}`;
- `GET …/attachments/{aid}/download`: stream berkas;
- `DELETE …/attachments/{aid}`: lepas tautan + audit.

**`payment` aman tanpa migrasi:** tiap rute pabrik men-JOIN tabel induknya sendiri (induk, daftar, unduh, lepas). Lampiran penerimaan tak pernah tampil di pembayaran keluar, dan sebaliknya. Kuota dihitung per (entity_type, id), dan id-nya UUID unik. Hub lama tetap jalan (tidak dihapus).

**Penerimaan / pembayaran keluar:** pabrik hanya memasang `POST …/attachments` (unggah banyak, kuota) dan `DELETE …/attachments/{aid}`. `GET` daftar dan unduh LAMA tetap (bentuk respons lama) supaya FE lama tidak rusak dan tidak ada rute ganda yang saling membayangi.

**Kasbon:** setiap rute (unggah/daftar/unduh/lepas) memeriksa pay-group karyawan kasbon (`employee_in_scope`); di luar lingkup → 404.

**Hub `_ENTITAS_HUB`: TIDAK ditambah** (keputusan gerbang 9 Nov, preseden V466 goods_receipt/vendor_deposit). Hub wajib berpasangan dengan trigger lepas-tautan V308 (penjaga `test_lampiran_yatim`), dan menambah trigger = migrasi kedua di luar persetujuan pemilik. Pabrik menyaring lewat JOIN induk, sehingga tautan yatim (mis. transfer draf yang dihapus) tak pernah tampil. Lampiran modul ini HANYA lewat rute pabrik.

## 2. Migrasi (DISETUJUI pemilik LANGSUNG 9 Nov)

`V493__chk_da_entity_kasbank_kasbon.sql` (**DITERAPKAN MASTER 9 Nov**, uji salinan 16/16) hanya MENAMBAH 3 nilai pada `chk_da_entity`; nol baris berubah. ROLLBACK GAGAL KERAS bila jenis baru sudah dipakai (pola V466). Diuji di salinan dulu, lalu diterapkan MASTER di jendela.

## 3. Bukti (HASIL)

- Uji unit: spec per modul; pagar tenant di SQL; izin rute; inventaris rute tulis; hub.
- Salinan: migrasi diterapkan di klon, lalu unggah/daftar/unduh/lepas per modul di kaos/nyawit (MinIO tiruan atau kunci uji), kuota 10, pemisahan `payment` (lampiran penerimaan ≠ pembayaran keluar), dan ROLLBACK gagal keras sesudah dipakai.
- Sabotase, suite, gate_izin_rute.

## 4. Tiket (TIDAK sekarang; MASTER 9 Nov)

- Faktur penjualan/pembelian di tabel lama tanpa kuota.
- Faktur pembelian maks 5 MB vs modul lain 10 MB.
- Biaya maks 5 berkas.
