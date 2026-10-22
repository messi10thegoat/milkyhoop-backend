# KONTRAK BE — Pesanan Pembelian seragam (P-U2) — RANCANGAN 22 Okt 2026 (belum dibangun)

Acuan: kontrak Penjualan seragam (`KONTRAK-penjualan-seragam-20261020.md` §0). Semua perubahan **aditif** (medan lama tak berubah), dihitung **SERVER**, FE hanya memformat. "Hari ini" = tanggal BISNIS tenant.
Data produksi PO = 0 baris → tidak ada backfill; bukti = data sintetis (CTE) + uji tulis 'TES E2E' kaos oleh WORKSPACE.

## 0. Keputusan yang dimintakan

| # | Keputusan | Usul |
|---|---|---|
| K1 | Set pill PO | `perlu_dikirim` · `telat_terima` · `perlu_ditagih` · `perlu_ditutup` (4 kunci, PARTISI). `menunggu_barang` BUKAN pill (keadaan normal tiap PO terkirim = derau permanen, sama alasannya dengan `belum_diterima` Pengiriman); tampil lewat `terima_*`/`position_text` |
| K2 | Migrasi **V435**: `DROP TRIGGER trg_update_po_totals` (+ fungsi). Trigger itu menimpa `purchase_orders.subtotal/tax_amount/total_amount` = Σ baris dalam variabel **BIGINT** setiap baris PO di-INSERT/UPDATE (juga saat `/receive`, `/to-bill`) → diskon & pajak header hilang, sen terpotong. Total dihitung Python (Decimal 2dp HALF_UP) di create/patch, kolom sudah `numeric(18,2)` | setuju |
| K3 | Nama jenis dokumen bundel | `purchase_order` · `bill` · `vendor_deposit` · `payment_out` (Pembayaran Keluar) · (nanti `goods_receipt`). Koordinasi BACKEND (pemilik bill/bayar) |
| K4 | `paid`/`balance` jangkar bundel PO | `null` di v1 (PO bukan dokumen uang; uang ada di bill) — seperti Pengiriman |

## 1. `GET /api/purchase-orders` (daftar)

Per item **tambahan**:
- `terima_terbuka` (status ∈ sent·partial_received ∧ ada sisa terima ∧ `expected_date` bertanggal) · `terima_hari` (expected − hari ini; null bila tak terbuka; <0 lewat) · `terima_keadaan` ∈ `telat | hari_ini | akan | selesai | tanpa` · `terima_teks` (`"Hari ini"` / `"Besok"` / `"24 Okt"` / `"12 Okt, lewat jadwal"` / `"—"`).
  - `selesai` = sudah diterima penuh (received·partial_billed·billed·closed) bertanggal (tanggal redup). `tanpa` = draf/batal/tanpa tanggal.
- `position_text` ∈ `Draf · Terkirim ke vendor · Diterima sebagian · Diterima · Ditagih sebagian · Ditagih · Selesai · Dibatalkan` (satu fungsi: baris, detail, anchor bundel).
- `aksi` (satu kunci PALING mendesak atau null): `perlu_dikirim` > `telat_terima` > `perlu_ditagih` > `perlu_ditutup`. PARTISI: `?aksi=k` = predikat yang menghasilkan `aksi == k` (bukan keanggotaan).
  - `perlu_dikirim` = status draft.
  - `telat_terima` = status ∈ sent·partial_received ∧ Σ(qty − diterima) > 0 ∧ `expected_date` < hari ini.
  - `perlu_ditagih` = status ∈ partial_received·received·partial_billed ∧ Σ(diterima − ditagih) > 0 ∧ bukan telat_terima.
  - `perlu_ditutup` = status billed (semua baris ditagih) — tinggal ditutup.
- `amount_received`/`amount_billed` TURUNAN dari baris (Σ qty_diterima/qty × total, 2dp HALF_UP), bukan kolom cache; `sisa_ditagih` = total − amount_billed (aktif saja).
- `documents`/`doc_count` = `[{type}]` urut bundel (K3), TANPA rekap; fungsi yang sama dengan bundel.
- Paginasi: `skip/limit` **dan** `page/per_page/offset` (prioritas offset > page > skip; `per_page` alias `limit`) + `has_more`. Urutan seri deterministik (`ORDER BY <sort>, id`).
- `?search=` kata-kata, semua wajib cocok: `po_number`, `vendor_name`, `ref_no`, `notes`, nama/kode vendor. Satu predikat (`syarat_cari`) untuk daftar DAN `/summary?search=`.
- `?aksi=<k>` (422 kunci asing), `?vendor_id=`, `?with_totals=true` → `sum_total` (Σ total_amount **tanpa cancelled**) + `sum_sisa` (Σ sisa_ditagih aktif) atas SEMUA hasil filter.

## 2. `GET /api/purchase-orders/summary`
Medan lama tetap. **Tambah**: `<k>_count` + `<k>_value` (Σ total) tiap kunci pill; `?search=` membatasi SEMUA hitungan/nilai. `total_value`/`total_received`/`total_billed`/`pending_amount` dihitung dari baris (turunan), bukan cache.

## 3. `GET /api/purchase-orders/{id}` (detail)
Semua medan baris di atas ada di detail dengan nilai SAMA (paritas detail == baris, fungsi sama; harness).

## 4. `GET /api/purchase-orders/{id}/documents` (bundel)
Skema sama dengan bundel SO/faktur: `{anchor, default, groups[{key,label,docs[]}]}`; tiap dok `sent`, `batal`, `status`; jangkar SELALU tampil sebagai dirinya (draf=`draf`, batal=`batal`).
- `anchor.kind="purchase_order"`, `position_text`, `total`, `paid=null`, `balance=null`, `vendor`.
- groups: `po` (PO itu sendiri) · `faktur` (bill bertaut `purchase_order_id`: draf→`draf`, void→`batal`) · `uang_muka` (vendor_deposits `purchase_order_id`) · `bayar` (Pembayaran Keluar atas bill-bill itu, via alokasi).
- `default={group:"po",index:0}`. Izin: `purchase_order` R.

## 5. Tulis (perbaikan P-U2, bukan aditif — uang)
- `POST /purchase-orders`: **idempotensi** `X-Idempotency-Key` (kunci {PREFIX}:{user}:{kunci}, sidik badan, 409 `IDEMPOTENCY_KEY_REUSED`, replay `X-Idempotent-Replay`), `vendor_id` WAJIB milik tenant (422 bila bukan; Law 24), `po_date` default tanggal bisnis, uang Decimal 2dp HALF_UP (`unit_price` boleh sen), diskon/pajak header ikut tersimpan (K2).
- `PATCH`/`DELETE`/`send`/`cancel`/`close`: kunci `PO:{tenant}:{id}` + `FOR UPDATE` + pemeriksaan di dalam lock; teks galat Indonesia berkode.
- Teks Indonesia untuk semua galat router PO (FE mencocokkan teks).

## 6. Bukti & alat
Tes unit murni (`purchase_orders_daftar`: keadaan/aksi/position_text/teks) + fake koneksi (lock sebelum baca, idempotensi, vendor tenant); harness `uji-nyata-po-seragam` (SQL pill pada data SINTETIS CTE dan SQL independen, baris == detail == summary == bundel; kontrol merah via sabotase); uji tulis 'TES E2E' kaos (WORKSPACE): buat→kirim→terima→tagih→batal/tutup, cek pill di tiap langkah.
