# KONTRAK dashboard T2 — pembanding periode, tren 12 bulan, umur piutang/hutang (BACKEND2, 23 Okt 2026)

Status: **USULAN untuk MASTER** (audit BACA-SAJA; belum ada kode). Masukan: FRONTEND `docs/rancangan-dashboard-t2` @ 772214169 §6–§8,
putusan MASTER §7 (tahun kalender bila tak ada kolom; kunci aksi umur per bucket; beban tak pernah merah; kas bersih = arus kas D3).
Iron Laws: Law 1/16 (semua angka turunan jurnal), Law 9/25 (Decimal, string 2dp), Law 29 (AR/AP dari `compute_ar/ap_outstanding`).
Pola pembanding: QBO "Previous period / Previous year" dan Xero "Compare with".

## 1. Hasil ukur (BACA-SAJA, prod 23 Okt 2026)

| # | Hal | Terukur | Akibat ke kontrak |
|---|---|---|---|
| U1 | Tahun fiskal | ADA: `accounting_settings.fiscal_year_start_month` (int, bawaan 1, dibaca/ditulis `routers/accounting_settings.py`) + kembar `"Tenant".fiscal_year_start` (hanya ditulis onboarding, tak dibaca). Semua tenant = 1 | `tahun_ini`/`kuartal_ini` = tahun/kuartal FISKAL dari `accounting_settings.fiscal_year_start_month` (tanpa baris = 1). Tanpa migrasi. Kolom kembar Tenant = catatan, tak dipakai |
| U2 | Penentu angka | `services/dashboard_angka`: `laba_rugi` (jurnal EFEKTIF, REV+OI / COGS+EXP+OE), `arus_kas_harian` (bruto akun kas, transfer antar-kas dikecualikan), `kas_per_akun`, `saldo_kontrol` | Pembanding & tren memakai fungsi INI saja (tren = versi `GROUP BY bulan` dari filter yang sama, diuji setara) |
| U3 | Umur AR (laporan) | `ar_aging_open_invoices`: hanya baris ber-faktur, sisa > 0; due NULL → '120+'; 6 bucket | Σ bucket laporan ≠ total piutang bila ada kredit pelanggan: kaos 6.111.000 vs `compute_ar_outstanding` 6.065.000 (selisih = kredit −46.000, 2 baris) |
| U4 | Umur AP per vendor | `get_ap_aging_detail` **GALAT saat dijalankan**: kolom hantu `bills.bill_date` (yang ada `issue_date`), `b.total_amount`, `b.paid_amount` (yang ada `amount`, `amount_paid` = cache); juga `::BIGINT` (memotong sen), `SECURITY DEFINER` | Perbaikan = tulis ulang di atas `compute_ap_outstanding` (kembar `get_ar_aging_detail`) → migrasi, IZIN PEMILIK (§6) |
| U5 | Umur AP ringkas | `get_ap_aging_summary` di atas `compute_ap_outstanding`: kaos 1.805.006 == total | sehat |
| U6 | Daftar seragam | Faktur & Tagihan: jatuh tempo EFEKTIF = due ?? tanggal dokumen; sisa = `compute_*_outstanding` | Bucket umur dashboard memakai definisi DAFTAR (bukan laporan), supaya hitungan bucket == baris `?aksi=`. Hari ini 0 baris due NULL di kedua tenant → tak ada selisih angka dengan laporan |
| U7 | GL kontrol vs subledger (kaos) | piutang GL efektif 6.066.000 vs `compute_ar_outstanding` 6.065.000 (Δ 1.000); hutang GL 1.704.006 vs `compute_ap_outstanding` 1.805.006 (Δ −101.000). grapgrap: 0 selisih | Dashboard tetap memakai subledger (sama dengan daftar & laporan umur). Selisih = tiket audit terpisah (§6 F2), bukan bagian T2 |
| U8 | Jurnal bertanggal > hari ini | 0 (kaos, grapgrap) | "s.d. hari ini" == kalender penuh untuk periode berjalan hari ini; kontrak tetap eksplisit (§3.2) |
| U9 | Jurnal efektif pertama | kaos 2026-07-01, grapgrap 2026-09-01 | Pembanding sebelum tanggal ini = `null` (bukan "0.00") |

### Latensi SQL (psql `\timing`, 30× per kueri, prod, load 0,25–0,8)

| Kueri | grapgrap p50 / p95 | kaos p50 / p95 |
|---|---|---|
| tren 12 bulan (SATU `GROUP BY bulan`, jurnal efektif) | 7,4 / 9,5 ms | 7,9 / 8,6 ms |
| laba-rugi satu periode (Okt 2026) | 4,4 / 7,0 ms | 2,2 / 2,9 ms |
| laba-rugi periode tahun lalu (Okt 2025) | 0,6 / 1,1 ms | 0,6 / 0,9 ms |
| umur AR (`ar_aging_open_invoices`) | 6,4 / 19,2 ms | 5,5 / 6,4 ms |
| umur AP (`get_ap_aging_summary`) | 1,6 / 10,1 ms | 1,6 / 2,5 ms |

Target MASTER p95 tren < 300 ms: terpenuhi di tingkat SQL (9,5 ms). Tambahan T2 ≈ 2 periode × (laba-rugi + arus kas) + 1 tren;
umur = 0 kueri tambahan (baris `compute_*_outstanding` sudah diambil `ringkasan`). **Belum diukur**: p95 HTTP `/v2/summary` sekarang
(garis dasar) vs sesudah — diukur di harness kontainer sekali-pakai (minta slot) sebelum FE mulai; syarat FRONTEND ≤ 1,5× garis dasar.

## 2. Bentuk: perluas `GET /api/dashboard/v2/summary` (setuju opsi FE, +0 permintaan)

- `summary_versi: 2` (aditif). Medan lama TIDAK berubah. Respons tanpa versi = BE lama (FE menyembunyikan blok baru).
- Parameter: `period` ∈ `bulan_ini | bulan_lalu | kuartal_ini | tahun_ini | kustom` (+ `from`,`to` ISO untuk `kustom`). Kunci lama
  `day|week|month|year` tetap diterima (makna lama). `kustom`: `from <= to`, panjang ≤ 366 hari, selain itu 422
  `{"code":"PERIOD_INVALID"}`. Cache 60 dtk per (tenant, period, from, to, hari bisnis) — kunci `dashboard:*` ikut dibatalkan
  invalidasi tulis finansial yang sudah ada.
- `kuartal_ini`/`tahun_ini` mengikuti tahun fiskal (U1). `period_label` dari server ("Oktober 2026", "Kuartal 4 2026", "Tahun 2026",
  "1–15 Okt 2026").
- Cadangan `/v2/insight` TIDAK diperlukan (latensi U-tabel); dipakai hanya bila p95 HTTP melanggar 1,5×.

## 3. `comparison`

```json
"comparison": {
  "available": true,
  "range":       {"label": "1–23 Okt 2026", "start": "2026-10-01", "end": "2026-10-23"},
  "prev_period": {"label": "1–23 Sep 2026", "start": "2026-09-01", "end": "2026-09-23"},
  "prev_year":   {"label": "1–23 Okt 2025", "start": "2025-10-01", "end": "2025-10-23"},
  "metrics": {
    "revenue":  {"current": "589000.00", "prev_period": {"value": "…", "delta": "…", "delta_pct": 12.3}, "prev_year": null},
    "expense":  {"current": "0.00",      "prev_period": {…}, "prev_year": null},
    "profit":   {"current": "589000.00", "prev_period": {…}, "prev_year": null},
    "net_cash": {"current": "…",         "prev_period": {…}, "prev_year": null, "needs_action": false}
  }
}
```
1. **Rentang** (pola QBO "period to date"): `range` = [awal periode, min(akhir periode, hari ini)]. `prev_period` = rentang yang sama
   digeser satu unit periode (bulan → bulan sebelumnya hari-ke-hari, tanggal dijepit ke akhir bulan; kuartal → kuartal sebelumnya;
   tahun → tahun sebelumnya); `kustom` → `DA.periode_sebelumnya` (panjang sama). `prev_year` = `range` digeser 12 bulan.
2. **Angka**: `revenue/expense/profit` = `DA.laba_rugi(range)`; `net_cash` = Σ (masuk − keluar) `DA.arus_kas_harian(range)` (== arus kas
   D3). `delta` = current − value (string 2dp); `delta_pct` = round(delta ÷ |value| × 100, 1), `null` bila value 0.
3. **null**: pembanding yang rentangnya berakhir SEBELUM jurnal efektif pertama tenant (U9) → seluruh objek `null` (bukan "0.00").
   `available: false` bila `prev_period` dan `prev_year` keduanya `null` di semua metrik.
4. **needs_action** HANYA di `net_cash` (`current < 0`). Tidak ada di `expense`, `revenue`, `profit` (putusan MASTER §7.3).
5. `comparison.metrics.*.current` dihitung atas `range`; kartu `pnl` lama tetap atas periode kalender penuh. Hari ini sama (U8);
   harness menegaskan kesetaraannya selama jurnal masa depan = 0, selain itu FE membaca `comparison` untuk baris pembanding.
6. Izin: `comparison` ← `report` + `kas_bank` (tanpa salah satu → `null`, dicatat `omitted`).

## 4. `trend_12m`

```json
"trend_12m": {"basis": "accrual", "points": [
  {"month": "2025-11", "label": "Nov", "revenue": null, "expense": null, "profit": null, "partial": false},
  {"month": "2026-07", "label": "Jul", "revenue": "5000000.00", "expense": "3500000.00", "profit": "1500000.00", "partial": false},
  {"month": "2026-08", "label": "Agu", "revenue": "0.00", "expense": "0.00", "profit": "0.00", "partial": false},
  {"month": "2026-09", "label": "Sep", "revenue": "52690000.00", "expense": "9385451.00", "profit": "43304549.00", "partial": false},
  {"month": "2026-10", "label": "Okt", "revenue": "589000.00", "expense": "0.00", "profit": "589000.00", "partial": true}]}
```
(angka kaos terukur 23 Okt.) 12 bulan kalender berakhir bulan hari ini; SATU kueri `GROUP BY date_trunc('month')` dengan filter
`DA.laba_rugi` (jurnal efektif, tipe akun sama) → fungsi baru `DA.laba_rugi_bulanan`; tes setara: tiap bulan == `DA.laba_rugi(bulan)`.
Bulan sebelum jurnal efektif pertama → `null`. `partial` = bulan berjalan. Izin ← `report`.

## 5. `aging` (+ `payables` disamakan dengan `receivables`)

```json
"aging": {"as_of": "2026-10-23",
  "receivables": {"total": "6065000.00", "unapplied_credits": "-46000.00", "buckets": [
    {"key": "current",  "count": 6,  "amount": "600000.00",  "list": {"modul": "sales_invoices", "aksi": "umur_current"}},
    {"key": "d1_30",    "count": 11, "amount": "3686000.00", "list": {"modul": "sales_invoices", "aksi": "umur_1_30"}},
    {"key": "d31_60",   "count": 1,  "amount": "1825000.00", "list": {"modul": "sales_invoices", "aksi": "umur_31_60"}},
    {"key": "d61_90",   "count": 0,  "amount": "0.00",       "list": {"modul": "sales_invoices", "aksi": "umur_61_90"}},
    {"key": "d90_plus", "count": 0,  "amount": "0.00",       "list": {"modul": "sales_invoices", "aksi": "umur_90_plus"}}]},
  "payables": {"total": "1805006.00", "unapplied_credits": "0.00", "buckets": [
    {"key": "d1_30", "count": 11, "amount": "1805006.00", "list": null}, "… 5 bucket, list null sampai kunci umur_* tagihan ada"]}}
```
(angka kaos terukur 23 Okt; d1_30 + d31_60 = 12 faktur / 5.511.000 == pill `jatuh_tempo` daftar; current = segera_tempo + belum_lunas.)
1. Sumber: baris `compute_ar/ap_outstanding` yang SUDAH diambil `ringkasan` (0 kueri tambahan). Bucket per dokumen sisa > 0 menurut
   umur dari jatuh tempo EFEKTIF (due ?? tanggal dokumen, = daftar): `current` (≥ hari ini), `d1_30`, `d31_60`, `d61_90`, `d90_plus` (> 90).
2. **Invarian (server menjamin, harness menegaskan)**: Σ `buckets[].amount` + `unapplied_credits` == `total` == Σ `compute_*_outstanding`
   == `receivables.total`/`payables.total` kartu lama. `unapplied_credits` = baris tanpa dokumen (kredit pelanggan/nota debit bebas) +
   dokumen bersisa negatif (pola QBO: kredit tampil terpisah, tidak dimasukkan ke bucket).
3. **Kunci aksi** `umur_current|umur_1_30|umur_31_60|umur_61_90|umur_90_plus` = kunci SARING `?aksi=` di registri daftar yang sama, BUKAN
   pill: partisi pill (`jatuh_tempo > segera_tempo > belum_lunas`) dan nilai `aksi` baris tak berubah; `/summary` daftar menambah
   `{k}_count/_value`; hitungan bucket == `total` daftar `?aksi=umur_*` (harness). Predikat SQL + Python setara (pola faktur_aksi).
   Faktur Penjualan = BACKEND2 (`faktur_aksi`); Faktur Pembelian = BACKEND (`tagihan_daftar`, milik mereka) → `list: null` sampai live.
4. `payables` memperoleh `not_due`, `overdue_count`, `due_7d[]` dengan rumus `piutang()` yang sama (lewat = jatuh tempo efektif < hari ini).
   CATATAN: kartu lama memakai aturan due-NULL-lama (`_jatuh_tempo_lewat`); hari ini 0 baris due NULL → angka sama; penyelarasan ke
   jatuh tempo efektif ikut unit ini (tes berpasangan).
5. Merah: bucket `d1_30…d90_plus` > 0 dan `receivables/payables.overdue_*` = "perlu tindakan" — keputusan nada di FE dari `key`, bukan
   hitungan FE. Izin: `aging.receivables` ← `sales_invoice`, `aging.payables` ← `purchase_invoice`.

## 6. Perbaikan yang menyentuh uang / laporan (= PEMILIK)

| # | Perbaikan | Jenis | Catatan |
|---|---|---|---|
| F1 | `get_ap_aging_detail` ditulis ulang di atas `compute_ap_outstanding` (kembar `get_ar_aging_detail`, numeric(18,2), tanpa SECURITY DEFINER) | migrasi V452 (blok BACKEND2) | Hari ini GALAT (U4) → endpoint `/reports/ap-aging/detail` & umur per vendor rusak. Buku tak berubah |
| F2 | Selisih GL vs subledger kaos (piutang Δ 1.000, hutang Δ −101.000) | audit baca-saja dulu, koreksi = putusan pemilik | Tak memblok T2 (dashboard = subledger), tapi harus dijelaskan sebelum rilis T2 ke grapgrap |
| F3 | Laporan umur: due NULL → jatuh tempo efektif (bukan '120+') + bucket 5 vs 6 | migrasi (fungsi laporan) | 0 baris terdampak hari ini; tanpa ini laporan & dashboard bisa berbeda kelak |
| — | `comparison`, `trend_12m`, `aging`, kunci `umur_*` faktur | kode baca-saja, tanpa migrasi | Butuh izin merge+restart biasa |

## 7. Uji (rencana, sebelum window)
Unit: rentang periode (bulan pendek/kabisat/kuartal & tahun fiskal ≠ 1, kustom ditolak), delta/null, `laba_rugi_bulanan` ==
`laba_rugi` per bulan, bucket Python == SQL `umur_*`, invarian Σ. Harness nyata (kaos + grapgrap, baca-saja): comparison.current ==
`DA.laba_rugi(range)` == pnl (jurnal masa depan 0); tren bulan == `DA.laba_rugi(bulan)`; Σ bucket + kredit == total == Σ
`compute_*_outstanding`; count bucket == `?aksi=umur_*`. Kontrol merah: geser bucket satu hari; buang `unapplied_credits`;
tren tanpa filter efektif. Latensi HTTP p95 garis dasar vs sesudah (≤ 1,5×).

## 8. Urutan usulan
1) MASTER menerima kontrak → 2) kode `comparison` + `trend_12m` + `aging` (+ `umur_*` faktur) di satu cabang, tes + harness + merah →
3) izin pemilik → window → 4) JSON LIVE ke FRONTEND. F1 (V452) terpisah dengan izin pemilik; F2 audit baca-saja paralel; F3 ditunda
sampai pemilik memutuskan. BACKEND: `umur_*` tagihan (tagihan_daftar) bila MASTER setuju.
