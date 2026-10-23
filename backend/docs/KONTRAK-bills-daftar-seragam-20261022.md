# KONTRAK BE — Faktur Pembelian (bills) daftar seragam — 22 Okt 2026

Menutup celah WORKSPACE no. 1 (diukur di master 47ae217a). Acuan: kontrak Penjualan seragam (`KONTRAK-penjualan-seragam-20261020.md`, sales_invoices) dan
Pesanan Pembelian (`KONTRAK-pembelian-po-seragam-20261022.md`, kind dokumen K3). Semua perubahan **aditif** (medan lama tak berubah), dihitung **SERVER**,
FE hanya memformat. "Hari ini" = tanggal BISNIS tenant. Uang = journal-derived lewat `compute_ap_rincian` (V440/V441; Law 1/16/29).
Satu modul sumber: `services/tagihan_daftar.py` (aksi, jatuh tempo, posisi, cari, bundel) + `services/tagihan_riwayat.py`.

## 0. Putusan (pola SaaS, tanpa tanya — aturan pemilik 22 Okt)

| # | Putusan | Acuan |
|---|---|---|
| B1 | Pill aksi = **partisi** 3 kunci: `lewat_jatuh_tempo` · `segera_tempo` (23 Okt; dulu `jatuh_tempo_minggu_ini`, kini ALIAS USANG — §7) · `perlu_diposting`. Tagihan terbuka yang jatuh tempo SESUDAH N hari = bukan pill (keadaan normal, derau; tampil lewat `jatuh_tempo_*`). Payung semua tagihan terbuka ada di summary (`tagihan_terbuka_*`) | NetSuite Bills: Overdue / Due this week / Pending Approval; alasan sama dgn `menunggu_barang` PO K1 |
| B2 | `segera_tempo` = terbuka ∧ hari ini ≤ jatuh tempo efektif ≤ hari ini + **N**, N = `pengingat.ambang_segera` (tenant, `accounting_settings.pengingat_segera_hari` V450, bawaan 7) — SATU definisi dgn faktur/proforma/pengingat dashboard (putusan pemilik 22 Okt, KONTRAK-dashboard-pengingat §7). Dulu: s.d. akhir minggu | Xero/QBO "due soon" N hari |
| B3 | Jatuh tempo efektif untuk aksi = `due_date`, kosong → `issue_date` (due on receipt) | faktur_aksi Penjualan |
| B4 | Kata posisi = Penjualan: Draf · Batal · Lunas · Sebagian · Belum lunas. Telat BUKAN posisi | NetSuite Status vs Days Overdue |
| B5 | `sum_total` = Σ `amount` **tanpa batal** (PO juga tanpa cancelled); `sum_sisa` = Σ `outstanding_amount` (draf/batal tak menyumbang) | QBO/Xero total list tak menghitung voided |
| B6 | Kind dokumen: `bill` · `purchase_order` · `vendor_deposit` · `payment_out` (K3 PO) + **`debit_note`** (Nota Debit). Status dok bill: `draf · batal · lunas · diposting` (== faktur Penjualan; PO bundel masih `diposting` tanpa `lunas` → BACKEND2 diberi tahu) | — |
| B7 | Summary pill dihitung **sepanjang waktu** (== daftar yang tak berperiode); medan periode lama tetap per `period` | — |

## 1. `GET /api/bills` (daftar)

Param **tambahan**: `?aksi=<kunci B1>` (422 kunci asing; PARTISI: baris dengan `aksi == k`), `?with_totals=true` → `sum_total`, `sum_sisa`
atas SEMUA hasil filter, angka (float, == sales_invoices; tanpa `with_totals` = `null`). `?search=` kini satu predikat (`TD.syarat_cari`) dengan `/summary?search=`: tiap kata wajib cocok salah
satu dari nomor, `vendor_name`, `ref_no`, `notes`, nama/kode vendor master.

Per item **tambahan** (`TD.fakta_baris`, fungsi SAMA dengan detail):
- `aksi` (kunci B1 atau null) · `aksi_semua` (`[aksi]` atau `[]`; partisi → paling banyak satu).
  - `perlu_diposting` = draf.
  - `lewat_jatuh_tempo` = terbuka (aktif, ber-kredit AP, sisa jurnal > 0) ∧ jatuh tempo efektif < hari ini.
  - `jatuh_tempo_minggu_ini` (USANG, bool) = terbuka ∧ hari ini ≤ jatuh tempo ≤ hari ini + N (== aksi segera_tempo).
- `position_text` (B4) · `outstanding_amount` (string; draf/batal = null; lunas = "0.00").
- `jatuh_tempo_terbuka` · `jatuh_tempo_hari` (due − hari ini; null bila tak terbuka) · `jatuh_tempo_keadaan` ∈ `telat | hari_ini | akan | selesai | tanpa` ·
  `jatuh_tempo_teks` (`"Hari ini"` / `"Besok"` / `"24 Okt"` / `"12 Okt, lewat tempo"` / `"—"`; selesai = tanggal) · `jatuh_tempo_minggu_ini` (bool).
  `selesai` = lunas bertanggal; `tanpa` = draf/batal/tanpa tanggal. `status == "overdue"` ⇔ `jatuh_tempo_keadaan == "telat"`.
- `documents` = `[{type}]` urut bundel (§4, tanpa rekap) · `doc_count`.
Medan lama (`amount`, `amount_paid`, `amount_due`, `status`, …) tidak berubah.

## 2. `GET /api/bills/summary`
Medan lama tetap (per `period`). **Tambah**: `?search=` (membatasi SEMUA medan per tagihan, termasuk `overdue_count/total_overdue`);
`<k>_count` + `<k>_value` tiap kunci B1 (nilai: draf = `amount`, terbuka = sisa jurnal); `tagihan_terbuka_count/_value` (payung; == Σ baris tagihan
`compute_ap_outstanding` > 0). Pill = sepanjang waktu (B7).

## 3. `GET /api/bills/{id}` dan `GET /api/bills/v2/{id}` (detail)
Semua medan §1 ada di detail dengan nilai SAMA (`aksi`, `aksi_semua`, `position_text`, `outstanding_amount`, `jatuh_tempo_*`, `documents`, `doc_count`).

## 4. `GET /api/bills/{id}/documents` (bundel) — BARU
Skema SAMA dgn bundel faktur/PO: `{anchor, default, groups[{key,label,docs[]}]}`; tiap dok `sent`, `batal`, `status`.
- `anchor`: `kind="bill"`, `id`, `number`, `vendor`, `position_text`, `total` (= `amount`), `paid` (= `amount_paid`), `balance` (= `amount_due`), `vendor_phone`, `vendor_email`.
- groups (urutan bundel PO): `po` (PO asal: `bills.purchase_order_id` ATAU baris bertaut) · `faktur` (tagihan ITU sendiri, selalu; draf/batal tampil sebagai dirinya) ·
  `uang_muka` (UM vendor tak void yang DITERAPKAN; `amount` = bagian diterapkan) · `bayar` (Pembayaran Keluar posted beralokasi; `amount` = Σ alokasi ke tagihan ini) ·
  `nota_debit` (ND `original_bill_id` = tagihan ini, bukan draf/void).
- `default = {group:"faktur", index:0}`. Izin: `purchase_invoice` R.

## 5. `GET /api/bills/{id}/history` (riwayat) — BARU
Bentuk SAMA dengan `/sales-orders/{id}/history` & `/purchase-orders/{id}/history`: `{success, data:{bill_id, invoice_number, events[], total, omitted[]}}`.
Kejadian: `TAGIHAN_DIBUAT` (+ "dari pesanan X") · `TAGIHAN_DIPOSTING` · `TAGIHAN_DIBATALKAN` (aktor null: kolom tak ada) · `PEMBAYARAN_DIBAYAR`/`PEMBAYARAN_DIBATALKAN`
(izin `send_payment`) · `UANG_MUKA_DITERAPKAN` (izin `vendor_deposit`) · `NOTA_DEBIT_DITERAPKAN`/`NOTA_DEBIT_DILEPAS` (izin `debit_note`) + audit_logs entitas `bills`.
Tanpa izin → modul tercantum di `omitted`. 404 bila tagihan tak ada di tenant. Izin rute: `purchase_invoice` R. `/activity` lama tetap.

## 6. Bukti & alat
- Tes unit `tests/unit/test_tagihan_kontrak_seragam.py` (aksi/posisi/jatuh tempo/cari/dok/izin rute/satu sumber).
- Uji nyata BACA-SAJA `scripts/ops/uji-nyata-tagihan-kontrak.sh` (grapgrap + kaos, 3 "hari ini"): pasangan SQL `?aksi=` == Python per baris; summary == baris;
  payung == AP jurnal; detail v1 == v2 == baris; bundel == baris; with_totals; search daftar == summary; riwayat. Kontrol merah `MERAH=1` (Python disabotase → gagal).

## 7. TRANSISI `segera_tempo` (23 Okt 2026, aditif; BACKEND)
- `?aksi=segera_tempo` (kanonik) DAN `?aksi=jatuh_tempo_minggu_ini` (alias USANG) -> predikat & parameter IDENTIK (`TD.AKSI_USANG`).
- `/bills/summary`: kunci BARU `segera_tempo_count` (int) / `segera_tempo_value` (float, Σ sisa jurnal); `jatuh_tempo_minggu_ini_count/_value`
  TETAP ada = nilai segera_tempo (alias). Kunci lain TIDAK berubah (diuji lama-vs-baru pada data nyata: `scripts/ops/uji-nyata-tagihan-summary-lama-baru.sh`).
- Baris/detail: `aksi`/`aksi_semua` TETAP mengirim nama lama `jatuh_tempo_minggu_ini` untuk tagihan segera (`TD.AKSI_BARIS_USANG`; FE r304
  memetakan label kaki dari kunci lama). Sesudah rilis FE yang memakai `segera_tempo` TERVERIFIKASI: baris -> `segera_tempo`, alias dicabut.
- Pengingat dashboard (BACKEND2 `services/pengingat`): `TD.sql_aksi_berparam("segera_tempo", …, ambang)` WAJIB menerima ambang (tanpa = ValueError);
  Σ sisa == `/bills/summary` segera_tempo_value (uji nyata (i)).
- Uji nyata (i) + tanggal PEMBEDA (jatuh tempo terbuka tertua − 5 hari) + kontrol merah `MERAH_AMBANG=1` (ambang diabaikan -> WAJIB gagal).
