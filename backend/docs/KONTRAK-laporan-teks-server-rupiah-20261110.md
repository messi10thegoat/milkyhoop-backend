# KONTRAK gelombang 4 — Laporan: teks dari server + SATU pemformat Rupiah (BACKEND, 10 Nov 2026) — RANCANGAN, hasil ukur

Status: **RANCANGAN untuk putusan MASTER.** Belum ada kode.

Ukur:
- FE: origin/main ab8532ef1, dibaca lewat git saja.
- BE: master 53a49ea45.

## 0. Hasil ukur

### FE: teks laporan dirakit di FE

| Layar | Endpoint | Label dirakit FE | Hitung uang di FE |
|---|---|---|---|
| Laba Rugi `Reports/ProfitLoss` | `GET /api/reports/psak/financial-statements` (`income_statement`) + `/pdf` + `/drill-down` | ±25 (judul seksi, Total…, Laba Kotor/Usaha/Bersih, "Untuk Periode yang Berakhir…") | **ya**: `Math.abs` semua saldo (tanda server dibuang), neto lain-lain, jumlah drill |
| Neraca `Reports/BalanceSheet` | PSAK (`balance_sheet`); `period_start` = 1 Jan diturunkan FE | ±25 (Aset Lancar…, Total Liabilitas dan Ekuitas, baris maya "Laba Periode Berjalan") | jumlah drill |
| Arus Kas `Reports/CashFlow` | PSAK (`cash_flow`) | ±20 (judul aktivitas, "Kas neto yang digunakan…" apa pun tandanya; kata mobile ≠ desktop) | tidak |
| Umur Piutang/Hutang (mobile + desktop `grosir/LaporanUmur`) | `/api/reports/aging-receivable` / `aging-payable` | label ember 3 versi berbeda; "Per hari ini"; tanggal "per" dari browser | **mobile ya**: persen ember, jumlah per pihak, ringkasan; desktop tidak |
| Penjualan per Pelanggan (grosir) | `/api/reports/sales-by-customer` | judul periode, tanggal awal/akhir dihitung FE | tidak |
| Profitabilitas | `/api/reports/profitability/items` | ±20 | tanda saja |
| Neraca Saldo (2 versi) | `/trial-balance`, `/trial-balance/full` | ±25 ("Seimbang/Tidak Balance", "Selisih … (Debit lebih)") | **ya**: kolom D/K dari normal_balance; seimbang + selisih dihitung FE (server `is_closing_balanced` diabaikan) |
| Buku Besar `GeneralLedger` | `/api/ledger`, `/ledger/summary`, `/ledger/{id}` | ±40; tipe/kategori akun DITEBAK dari prefiks kode di FE (2 salinan) | **ya**: total ringkasan fallback, persen |
| Laporan Pajak | `/api/tax-reports/ppn`, `/pph` | ±35 ("Kurang/Lebih Bayar/NIHIL" dari tanda, nama bulan) | **ya**: jumlah DPP cetak, hitungan |
| Panel Laba Rugi (chat), AgingCard, InsightPanel (lama) | `/reports/laba-rugi/{p}`, `/dashboard/all`, `/reports/profit-loss|neraca|arus-kas/{p}` | ±25 / ±7 / ±90 (InsightPanel menanam kode akun di label) | **ya** (abs; ember 61–90+90+ digabung; Insight membagi beban dengan pencocokan kata nama akun) |

Peta label ganda di FE:
- 3 versi label ember umur.
- Ekuitas disebut "Modal" di 3 peta dan "Ekuitas" di 3 peta lain.
- Tebak kategori dari kode akun: 2 salinan.
- Nama bulan: 5 salinan.
- Drill sumber → label: 1.

### FE: pemformat uang

Ada **±25 pemformat berbeda**:
- 4 gaya "Rp": "Rp x", "Rp -x", "−Rp x", "(Rp x)";
- 3 aturan desimal: selalu 2, hanya bila pecahan, dibulatkan 0;
- singkatan tak seragam: jt / Jt / " jt", titik atau koma desimal.

Sudah ada pemformat baku FE `utils/uang.ts formatUang`, yang sama dengan BE `teks_galat.rp`, dan `formatDasborV2.rpPenuh`.

### BE

- Router laporan **hampir tanpa teks**: ±5 medan label di 6 router.
- `reports.py` memakai `float(` 13×. Perlu dicek terhadap Law 9/25 di jalur uang.
- Pasangan endpoint ganda: `/profit-loss` vs `/profit-loss/{periode}`; `/ar-aging` vs `/aging-receivable`; `/neraca/{p}` + `/arus-kas/{p}` + `/laba-rugi/{p}` lama vs PSAK.
- Pemformat TEKS BE:
  - `teks_galat.rp` = baku ("Rp 1.250.000" / "Rp 1.250,50" / "Rp -5.000"). Alias (so_riwayat, so_pelunasan, so_ubah_terkonfirmasi, tagihan_aksi, pihak_helpers) sudah mendelegasi; penjaga `test_rupiah_satu_pemformat`.
  - Pemformat lain: `dashboard_v2.rp` (WA), `anomalies._rp` (tanpa spasi/sen), **`employee_advances._rp` (float)**, `unified_agent/projection_engine._fmt_rp`, milkybook (`tafsir._rp`, `cari_barang.rupiah`), `transactions.format_rupiah` (singkatan chat "50rb").
  - String desimal JSON, BUKAN teks: kasbank_ringkas / kasbank_daftar / kasbank_tulis / stok_sesuai `rp`. Tetap; itu kontrak angka.

## 1. Usul bentuk (aditif; FE beralih per layar, medan lama tetap)

### L1 — Laporan keuangan PSAK

Endpoint: `GET /api/reports/psak/financial-statements`. Tiap laporan diberi:
- `judul`, `periode_text` (mis. "Untuk periode 1 Jan – 31 Okt 2026" / "Per 31 Okt 2026"), `basis_text`;
- `baris[]`: `{jenis: judul|akun|subtotal|total, label, nilai: "desimal", tanda_tampil, kedalaman, account_id?, kode?}`, urut = urutan tampil PSAK.

Aturan:
- Label seksi/total dari SATU peta server.
- Tanda + abs diputus server; FE berhenti `Math.abs`.
- Baris "Laba Periode Berjalan" dibuat server.
- Pesan validasi (`validation_errors`) jadi kalimat.

### L2 — Umur piutang/hutang

- `ember[]` `{kunci, label, total, persen}` (SATU peta label).
- `per_tanggal` + `per_text` dari tanggal bisnis tenant (bukan browser).
- Jumlah per pihak dan ringkasan dari server.

### L3 — Neraca saldo + Buku besar

- `type_label`, `category_label` (dari CoA, bukan tebak prefiks), `saldo_normal_text`.
- `seimbang` + `selisih` + `selisih_text` dari server.
- Kolom D/K per akun dari server.

### L4 — Pajak

- `status_text` (Kurang Bayar / Lebih Bayar / NIHIL), `periode_text`.
- Hitungan dan jumlah DPP cetak dari server.

### L5 — Panel / kartu dasbor

Laba Rugi panel, AgingCard: label dari server. **InsightPanel lama: usul DIPENSIUNKAN, bukan dipoles** (label menanam kode akun + pencocokan kata). Butuh putusan.

## 2. SATU pemformat Rupiah

- **BE teks**: semua kalimat berangka → `teks_galat.rp`.
  - Pindahkan `anomalies._rp`, `employee_advances._rp` (float → Decimal), `dashboard_v2.rp` (bila format sama).
  - Singkatan ("jt") = SATU fungsi `teks_galat.rp_ringkas` bila dibutuhkan.
  - Penjaga AST: tak ada idiom `:,` / `float(` di pemformat uang.
- **Angka laporan** = string desimal (pola dasbor v2 `rpPenuh`); FE memformat dengan SATU `formatUang`.
- **PUTUSAN DIBUTUHKAN — gaya angka di LAPORAN keuangan:**
  - (i) "Rp 1.250.000" seperti teks;
  - (ii) gaya akuntansi tanpa Rp, negatif "(1.250.000)", seperti FE `fmtPSAK` hari ini dan Accurate.
  - Desimal: dibulatkan ke rupiah utuh di tabel laporan (hari ini `fmtPSAK` membulatkan) ATAU sen bila ada.
  - Ini aturan PENYAJIAN uang, jadi pemilik/MASTER memutus; usul ikut Accurate.

## 3. Urutan usul

1. L1, tiga laporan PSAK: dampak terbesar, satu endpoint.
2. L2.
3. L3.
4. L4.
5. Pemformat BE.
6. L5.

Tiap unit: kontrak → kode → uji salinan (angka lama == baru; teks baru dari satu peta) → sabotase → suite → gate.

## 4. Temuan sampingan (tiket)

- FE Laba Rugi `mapAkun` mengisi `accountId: ''`, sehingga baris L/R tak pernah bisa drill-down.
- PSAK endpoint memakai `get_db_connection()` langsung, `SET LOCAL` f-string di luar transaksi (tak berefek), dan `detail=str(e)` pada 500 (membocorkan galat internal).
- `Reports/GeneralLedger` (ReportsPanel) masih data MOCK.
- Neraca Saldo desktop mengabaikan `is_closing_balanced` server.

## 5. PUTUSAN MASTER 10 Nov

- (a) Gaya angka = ikut ACCURATE. TESTER mengukur tampilan Laba Rugi/Neraca/Neraca Saldo Accurate. Sampai itu:
  - server mengirim nilai desimal mentah + tanda;
  - SATU pemformat BE (`teks_galat.angka_laporan`, gaya `GAYA_ANGKA_LAPORAN` diputus belakangan; sementara "akuntansi" = gaya tabel hari ini);
  - rupiah utuh, kecuali tenant `uang_bersen`.
- (b) InsightPanel lama: disembunyikan saat panel baru ada (bukan dipoles, bukan dihapus); hapus nanti lewat tiket.
- (c) L1 dulu. Sekalian rapikan endpoint PSAK: pool, SET LOCAL, 500 tanpa str(e), float di jalur tersentuh. Ironlaws wajib. Paritas Σ baris == jurnal per akun di salinan.

## 6. L1 — bentuk akhir (cabang `feat/laporan-psak-teks`)

`GET /api/reports/psak/financial-statements`: ketiga laporan (`balance_sheet`, `income_statement`, `cash_flow`) menambah:
- `judul`;
- `periode_text`: "Untuk Periode yang Berakhir 31 Oktober 2026" / "Per Tanggal 31 Oktober 2026";
- `basis_text` (L/R, Neraca);
- `satuan_text` "(Disajikan dalam Rupiah)";
- `baris[]`: `{jenis: judul|akun|total|hasil|info, kunci, label, nilai: "desimal 2 angka"|null, tanda: -1|0|1, kedalaman, nilai_tampil, account_id?, account_code?}`.

Aturan nilai dan label:
- `nilai` = nilai SAJIAN (tanda menurut PSAK: pendapatan positif; beban lain-lain dan akumulasi penyusutan negatif; liabilitas/ekuitas positif bila normal).
- FE berhenti `Math.abs` / menegasi.
- Label dari SATU peta `report_engine/baris.py`, sama dengan teks layar desktop sebelum L1.
- Kecuali arus kas: "Kas neto yang **diperoleh dari** / **digunakan untuk** aktivitas …" mengikuti tanda (dulu FE selalu "digunakan untuk").
- Baris maya "Laba Periode Berjalan" dibuat server.
- `account_id` ada di baris akun, sehingga drill-down L/R kini mungkin.

Perubahan internal:
- Hitungan engine Decimal penuh:
  - neraca: total liabilitas jangka pendek dulu float + float; laba periode berjalan dulu lewat float;
  - arus kas: penggabungan butir dulu float.
- Medan lama float tetap; nilainya sama sampai sen.
- Rute data + PDF:
  - pool bersama;
  - transaksi READ ONLY;
  - tanpa `SET LOCAL` f-string (tak berefek di luar transaksi; tiap kueri report_engine menyaring tenant_id eksplisit);
  - 500 tanpa `str(e)`;
  - PDF dirender di luar koneksi.

Bukti:
- unit `tests/unit/test_laporan_psak_baris.py`;
- salinan `scripts/ops/uji-salinan-laporan-psak.sh`: medan lama lama == baru per sen; paritas tiap baris akun == Σ jurnal akun; Σ akun per seksi == total; hasil == medan lama; laba periode neraca == laba bersih L/R; nilai_tampil == pemformat;
- sabotase, suite, gate.

## 7. L2 — umur piutang/hutang (cabang `feat/laporan-umur-teks`)

`GET /api/reports/aging-receivable` dan `/aging-payable` memakai SATU pembantu `services/laporan_umur.tambah_tampilan`. Aditif; medan lama identik.

Medan baru:
- `per_tanggal` + `per_text` ("Per 10 November 2026"), dari tanggal BISNIS tenant (dulu FE memakai tanggal browser).
- `ember[]` `{kunci, label, total, persen}`. SATU peta label: "Belum jatuh tempo", "1–30 hari", "31–60 hari", "61–90 hari", "> 90 hari" (dulu 3 versi FE berbeda). Persen 1 desimal, HALF_UP, dihitung server.
- `belum_jatuh_tempo_total`, `jatuh_tempo_total`; `jumlah_pihak`, `jumlah_pihak_text` ("12 pelanggan" / "5 vendor").
- Per pihak: `jatuh_tempo_total`.
- Per dokumen: `nomor_text` ("Kredit tanpa dokumen" bila tanpa nomor), `jatuh_tempo_text` ("jatuh tempo 12 Okt 2026"), `telat_text` ("telat 29 hari" / null).

Aturan:
- Uang = angka medan lama (compute_ar/ap_outstanding); string desimal di medan baru.
- FE berhenti menjumlah / menghitung persen.
- `teks_galat.tanggal_pendek` = bulan 3 huruf dari BULAN_PANJANG (tanpa larik bulan baru).

## 8. L3 — Neraca Saldo + Buku Besar (RANCANGAN 10 Nov 05:40; PUTUSAN MASTER: kerjakan, pemilik diberi tahu)

### Hasil ukur (read-only @3529e8dd6; prod RO kaos/nyawit; grapgrap TIDAK dibaca)
- `GET /api/reports/trial-balance` (Laporan → Neraca Saldo) → `facade.get_trial_balance` (definisi KEDUA menang) → fungsi DB `get_trial_balance()`.
  Saringan `journal_entries` (POSTED, tanggal ≤ as_of, periode) ada di ON LEFT JOIN, `journal_lines` di-join tanpa syarat → baris jurnal VOID/DRAFT/masa depan IKUT dijumlah; as_of praktis tak berefek.
  Ukur kaos: fungsi Σ debit 1.069.966.777,76 vs benar 1.069.901.559,76 → lebih Rp 65.218 = 32 baris jurnal VOID. Debit & kredit sama-sama lebih → tetap "Seimbang". nyawit: sama (0 VOID hari ini).
  Fungsi juga membuang akun NONAKTIF bersaldo (BL-07 belum diterapkan) → selisih semu bila ada.
- FE mengirim `?as_of_date=`, rute membaca `?as_of=` → tanggal diabaikan; bawaan `date.today()` (UTC), bukan tanggal bisnis.
- FE merakit: kolom Debit/Kredit dari balance + normal_balance; "Neraca Seimbang/Tidak Seimbang" (judul salah); "Per dd/mm/yyyy" tanggal browser.
- Buku Besar FE: label tipe sendiri ("Kewajiban" ≠ L1 "Liabilitas"); saldo normal diturunkan dari TIPE, padahal CoA punya akun kontra (ASSET/CREDIT 2, COGS/CREDIT 4, EQUITY/DEBIT 2, REVENUE/DEBIT 4 di kaos+nyawit).
- `/trial-balance/full` (halaman Dashboard): kueri BENAR (CASE menyaring non-POSTED). `/api/ledger/{id}`: saldo berjalan server, benar.

### Bentuk
Rute `/trial-balance`: hitung sendiri lewat SATU kueri di `services/neraca_saldo.py` (fungsi DB lama dibiarkan, tak dipanggil; hapus = migrasi → tanya pemilik, tiket).
- Sumber: `journal_lines` JOIN `journal_entries` (INNER) `status='POSTED'`, `journal_date ≤ as_of`, tenant. POSTED SAJA (tanpa is_effective), sama dengan `/full` dan `/api/ledger`; pasangan pembalik bersih nol per akun, kolom bruto jujur.
- Akun: aktif ATAU nonaktif bersaldo (BL-07). Baris nol disaring seperti dulu.
- Tanggal: `as_of` ATAU `as_of_date` (alias; dua-duanya dihormati); bawaan `tanggal_dokumen` tenant.
- Decimal penuh; medan lama tetap (float, nama sama).

Medan baru (aditif):
- `judul` "NERACA SALDO", `per_tanggal`, `per_text` ("Per Tanggal 10 November 2026", sama dengan Neraca L1 `baris.per_tanggal`).
- `status_text`: "Seimbang" / "Tidak seimbang — selisih {angka_laporan}"; `selisih` (string desimal).
- per akun: `tipe_label` (SATU peta di report_engine/baris), `saldo_normal_label` ("Debit"/"Kredit" dari CoA `normal_balance`, bukan tipe), `debit_saldo`/`kredit_saldo` (string; kolom sajian dihitung server), `debit_tampil`/`kredit_tampil` (angka_laporan; "" bila nol).
- `total_debit_saldo`/`total_kredit_saldo` + tampil.

Buku besar (`/api/ledger`, `/api/ledger/{id}`) + `/trial-balance/full`: tambah `tipe_label` + `saldo_normal_label` dari CoA (aditif).

Peta tipe (SATU, `baris.TIPE_AKUN`): ASSET Aset · RECEIVABLE Piutang · LIABILITY Liabilitas · PAYABLE Utang · EQUITY Ekuitas · REVENUE Pendapatan · COGS Harga Pokok Penjualan · EXPENSE Beban · OTHER_INCOME Pendapatan Lain-lain · OTHER_EXPENSE Beban Lain-lain.

### Bukti (syarat MASTER)
- Per tenant: selisih lama − baru == Σ baris VOID/DRAFT/masa depan PERSIS (kaos Rp 65.218 / 32 baris).
- Baru == `/trial-balance/full` (closing, periode memuat as_of) == Σ ledger per akun.
- Akun nonaktif bersaldo ikut (salinan: nonaktifkan satu akun bersaldo → tetap tampil, total tetap seimbang).
- `as_of` dan `as_of_date` sama-sama dihormati (tanggal lampau mengubah angka sesuai jurnal ≤ tanggal); bawaan = tanggal bisnis tenant.
- Unit + sabotase + suite + gate SESUDAH jam kerja.

Temuan sampingan L3 (tiket, bukan unit ini): `/trial-balance/full` memotong sen dengan `int()` di semua kolom uang (medan lama; ubah tipe = unit tersendiri). `str(e)` di 500 `/full` DIHAPUS di L3.
Skema: `tipe_label`/`saldo_normal_label` = `@computed_field` (pydantic 2.11) di TrialBalanceAccountRow, TrialBalanceAccountEnhanced (+ medan `normal_balance` dari CoA), AccountInfoResponse, LedgerAccountSummary → semua tempat pembentuk ikut tanpa disentuh.
