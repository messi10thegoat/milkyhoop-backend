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
