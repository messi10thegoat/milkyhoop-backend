# AUDIT angka dashboard (T0) — 22 Okt 2026, BACKEND, baca-saja

Lingkup: `GET /api/dashboard/all` + `/sales-today` (routers/dashboard.py, master da575cfd), dibandingkan dengan hitung ulang INDEPENDEN dari
`journal_lines` (POSTED + `is_effective_journal`), tenant **grapgrap-manado** dan **kaos-biru-konveksi**, periode BULAN INI (1–22 Okt) dan YTD.
Alat: `scripts/ops/uji-nyata-dashboard-angka.sh` (handler NYATA; SEMUA koneksi `default_transaction_read_only=on`; izin dashboard di-stub
"boleh" karena yang diaudit angka, bukan izin). Hipotesis FRONTEND (kas /all vs v2, arus kas YTD, pengeluaran Okt) diukur di §3. Tak ada data diubah.

## 1. Ringkasan temuan (urut bahaya)

| # | Temuan | Dampak | Bukti |
|---|---|---|---|
| D1 | **Laba-rugi basis KAS selalu 0.** `get_revenue_by_basis(...,'cash')` menyaring `source_type IN ('PAYMENT_RECEIPT','CASH_SALE')` pada akun REVENUE; kedua source_type itu TIDAK pernah mengkredit pendapatan (yang mengkredit REVENUE hanya INVOICE_REVENUE / CREDIT_NOTE / INVOICE_REVENUE_REVERSAL / INVOICE). Beban kas sama: `PAYMENT_MADE/CASH_EXPENSE` ke akun beban -> 0. | Tab "Cash" widget W2 menampilkan Pendapatan 0 / Pengeluaran 0 / Laba 0 untuk semua tenant. Bawaan kedua tenant = accrual (accounting_settings), jadi hanya saat pengguna pindah tab. | grapgrap Okt: endpoint 0 vs uang masuk dari pelanggan 145.017.000; YTD 0 vs 305.639.000. kaos YTD 0 vs 75.722.600. |
| D2 | **v2/summary (dashboard baru) kas kaos +65.218 (salah); /all benar.** `report_engine.compute_balance` = `status='POSTED'` saja (benar menurut Law 2: asli tetap POSTED + pembalik -> neto 0). 16 void beban LAMA (kaos, Sep) membalik status jurnal asli ke **'VOID'** (pelanggaran Law 2 lama) sementara pembaliknya POSTED -> compute_balance menghitung pembalik (+kas) tanpa aslinya. | Kas v2 kaos 84.276.248 vs benar 84.211.030; Neraca kaos (report_engine yang sama) ikut lebih 65.218. grapgrap 0 kasus. | REV-EXP-2609-0001..0026 (16 jurnal, Σ kas 65.218); contoh JV-EXP-EXP-2609-0001 status VOID, REV-EXP-2609-0001 POSTED (Dr BCA 12.345). |
| D3 | **Arus kas v2 menggelembung oleh transaksi yang dibatalkan.** v2 menetto kas PER JURNAL atas SEMUA jurnal POSTED -> asli + pembalik muncul sebagai masuk DAN keluar. /all (bruto baris kas, jurnal efektif, tanpa transfer murni) = nilai sebenarnya. Neto keduanya sama (kecuali D2). | Hipotesis FE "masuk/keluar YTD beda ±2 jt" = ini. | YTD grapgrap: /all 393.753.590 / 17.283.896 vs v2 412.274.090 / 35.804.396 (selisih 18.520.500 di dua sisi). kaos: 105.242.030 / 21.031.000 vs 107.250.248 / 22.974.000. |
| D4 | **"Okt pengeluaran Rp 0 padahal kas keluar 472.000" (kaos) = BUKAN salah laba-rugi.** Kas keluar EFEKTIF Okt = 11.000 (refund NK 1.000 + refund uang muka 10.000 — bukan beban). 461.000 sisanya = pembalik deposit/penerimaan yang dibatalkan (D3). | Laba-rugi akrual Okt beban 0 = benar. Angka 472.000 = v2 menggelembung. | CUSTOMER_DEPOSIT non-efektif keluar 453.000, RECEIVE_PAYMENT non-efektif 8.000. |
| D5 | Piutang kaos endpoint 6.065.000 vs GL Piutang 6.066.000 (−1.000). | Kecil. | Tiket V442 (cn_unapplied AR mengabaikan CREDIT_NOTE_REFUND; CN-2610-0005). |
| D6 | Hutang kaos endpoint 1.805.006 vs GL Hutang 1.704.006 (+101.000). | Kecil, data lama. | 100.000 = BP-2609-0001 dialokasikan ke BILL-2609-0002 yang void (tiket lama); 1.000 = VENDOR_OPENING_BALANCE debit (saldo awal vendor di sisi debit) tak muncul di compute_ap_outstanding. |
| D7 | `hutang.nearest_supplier/nearest_days` memilih tagihan TERLAMA lewat tempo, bukan "terdekat": kueri tanpa `due_date >= hari ini` / `outstanding > 0`. | kaos: "UJI Maklun 3B09D3, −22 hari" ditampilkan sebagai terdekat. | dashboard.py `nearest_ap_query` (get_dashboard_summary). |
| D8 | KPI: DPO = Hutang / (pengeluaran P&L / hari) — memakai SEMUA beban, bukan pembelian; DSO/DPO `days_in_period = (end − start).days` (kurang 1 hari, inklusif). | kaos Okt DPO 0 padahal hutang 1,8 jt; DSO 216 hari. Menyesatkan. | dashboard.py §5 KPI. |
| D9 | Widget gagal = DIAM. `/all` memakai `asyncio.gather(return_exceptions=True)`; sub-kueri gagal -> kunci = `null` + log server; tak ada penanda di respons (beda dari `omitted` untuk izin). FE tak bisa membedakan "kosong" vs "gagal". | Terbukti saat alat audit pertama (satu koneksi bersama): 6 widget jadi `null` tanpa galat di respons. | dashboard.py `get_dashboard_all`. |
| D10 | Uang dibulatkan ke rupiah (`int(...)`, fungsi P&L `::BIGINT`). | Sen hilang (data kini tanpa sen; risiko bila ada sen). | |
| D11 | P&L akrual: beban memasukkan OTHER_EXPENSE, pendapatan TIDAK memasukkan OTHER_INCOME (asimetri). Top pengeluaran memilih akun per **kode** `5-%`/`6-%`, bukan account_type (Law 27). | Hari ini 0 selisih (OTHER_INCOME = 0; kode cocok). Rapuh. | |
| D12 | `change_pct` membandingkan definisi berbeda: kini = compute_ar/ap_summary, sebelumnya = saldo GL akun Piutang/Hutang. Selisih D5/D6 ikut mengotori persentase. | Kecil. | |

## 2. Yang BENAR (cocok dengan hitung ulang independen, kedua tenant, BULAN & YTD)
laba_rugi akrual (pendapatan, pengeluaran, laba); salesToday/periode == pendapatan REVENUE; kas_bank /all == GL is_cash efektif (grapgrap 376.469.694, kaos
84.211.030); piutang/hutang Σ aging == total; jumlah jatuh tempo == compute_*_outstanding (>0, lewat); overdueInvoices/overdueBills total & jumlah;
grapgrap piutang == GL (5.676.000), hutang 0; top pengeluaran == beban P&L.

## 3. Hipotesis FRONTEND — jawaban
- kas /all 84.211.030 vs v2/summary 84.276.248 (kaos): **/all benar**; v2 salah 65.218 karena D2 (data void lama + compute_balance hanya POSTED).
- kas masuk/keluar YTD beda ±2 jt: **D3** (v2 menghitung pasangan asli+pembalik sebagai arus). /all = nilai sebenarnya.
- laba Okt "pengeluaran Rp 0" padahal kas keluar 472.000: **D4** — laba-rugi benar; 472.000 = angka v2 menggelembung; kas keluar riil 11.000 dan itu refund, bukan beban.
- tenant_features `dashboard_v2`: grapgrap = **ON**, kaos = **ON**.

## 4. Latensi produksi (log nginx, 8 hari, `rt=`)
| Rute | n | p50 | p95 | p99 | maks | status |
|---|---|---|---|---|---|---|
| GET /api/dashboard/all | 42 | 0,49 dtk | 1,30 dtk | 2,18 dtk | 6,49 dtk | 39×200, 3×503 |
| GET /api/dashboard/sales-today | 30 | 0,10 dtk | 0,67 dtk | 0,92 dtk | 6,52 dtk | 27×200, 3×503 |
503 = jendela restart (gateway sedang start). Lalu lintas dashboard prod rendah (≈5/hari).

## 5. Data yang SUDAH ada untuk widget Pembelian & Persediaan
| Data | grapgrap | kaos | Sumber siap pakai |
|---|---|---|---|
| Pesanan Pembelian | 0 | 3 (0 terbuka) | `GET /purchase-orders` + `/summary` (pill aksi perlu_dikirim/telat_terima/perlu_ditagih/perlu_ditutup) |
| Penerimaan barang (GRN) | — | 3 baris PO sudah diterima | TIDAK ada tabel GRN; diterima = `purchase_order_items.quantity_received` |
| Faktur pembelian / hutang aging | 18 (lunas semua) | 33 (26 aktif), 11 hutang terbuka | `compute_ap_outstanding`, `GET /bills/summary` (pill lewat_jatuh_tempo / jatuh_tempo_minggu_ini / perlu_diposting, tagihan_terbuka_*), aging sudah di summary.hutang |
| Uang muka vendor | 0 | 0 | `GET /vendor-deposits/summary` |
| Persediaan | 129 produk, 0 dilacak (semua non_inventory, putusan pemilik) | 84 produk, 78 dilacak; warehouse_stock 11 baris (7 > 0); inventory_ledger 77 baris / 10 produk | routers/inventory.py, items.py, warehouses.py (nilai & stok); tak ada endpoint dashboard persediaan |

## 6. Usul perbaikan (BUTUH putusan; tidak dikerjakan)
1. D1: basis kas = pendapatan/beban DIAKUI saat kas berpindah (pola QBO cash basis: alokasikan penerimaan ke pendapatan faktur yang dilunasi). Aturan uang -> pemilik.
2. D2: data — 16 jurnal void beban lama kaos (kembalikan asli ke POSTED + reversed_by_id; tenant uji) ATAU compute_balance memakai is_effective_journal. Uang -> pemilik.
3. D3: arus kas v2 memakai jurnal EFEKTIF (sama dgn /all) atau menetto pasangan asli+pembalik.
4. D7/D8/D9/D11: perbaikan kode kecil (filter nearest, DPO dari pembelian, penanda widget gagal `failed:[...]` sejajar `omitted`, OTHER_INCOME, account_type).
