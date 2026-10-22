# AUDIT — kesiapan data DASHBOARD T0 (22 Okt 2026, BACKEND2, baca-saja)

Semua angka diukur di DB produksi dengan `default_transaction_read_only = on`; biaya = `EXPLAIN (ANALYZE)` pada tenant terbesar
(kaos-biru-konveksi: 826 jurnal, 101 faktur, 84 produk/78 ber-stok; grapgrap-manado: 595 jurnal, 77 faktur, 129 produk non-stok).
Data masih kecil: biaya di bawah ini terutama memberi **bentuk** rencana kueri (fungsi per-tenant memindai seluruh jurnal tenant), bukan batas.

| Widget | Siap? | Sumber yang ada | Biaya (kaos) | Celah utama |
|---|---|---|---|---|
| Aging AR | ✅ siap | `get_ar_aging_summary` / `get_ar_aging_detail` → `ar_aging_open_invoices` → `compute_ar_outstanding` (jurnal) | 11,6 ms (grapgrap 13,2 ms) | ember jatuh-tempo beda dengan permintaan (lihat 1) |
| Aging AP | ⚠️ ringkasan siap; per vendor RUSAK | `get_ap_aging_summary` → `compute_ap_outstanding` (jurnal) | 8,7 ms | `get_ap_aging_detail` galat kolom hantu `b.bill_date` (lihat 1) |
| Nilai persediaan | ⚠️ ada, angka tak cocok | GL peran INVENTORY_MERCHANDISE; `inventory_ledger`; `warehouse_stock` | <10 ms | GL ≠ ledger Rp 47.994 di kaos (lihat 2) |
| Di bawah stok minimum | ❌ tanpa data | `warehouse_stock.min_stock/reorder_level`, `products.reorder_level` | — | 0 dari 11 baris stok & 0 dari 78 produk terisi |
| PO terbuka / belum diterima | ✅ siap (data kosong) | `services/po_daftar` (SQL_AGREGAT sisa_terima, pill telat_terima) | tak diukur (3 PO) | 0 PO terbuka saat ini |
| Top pelanggan per periode | ✅ siap dari jurnal | jurnal INVOICE_REVENUE (source_id = faktur) → `sales_invoices.customer_id` | 1,1 ms | penjualan tunai/POS tak lewat faktur (lihat 4) |
| Top barang per periode | ⚠️ dari dokumen | `sales_invoice_items.recognized_amount` + `item_id` | tak diukur | baris pendapatan per barang tak ada di jurnal (lihat 4) |
| Laba kotor per bulan | ⚠️ siap, makna beda per tenant | jurnal: REVENUE vs COGS per `journal_date` | 7,9 ms (setahun) | HPP grapgrap = biaya beli, bukan HPP terjual (lihat 5) |

## 1. Aging AR/AP — journal-derived (Law 1/16/29)
- **AR**: `ar_aging_open_invoices(tenant, as_of)` membaca `compute_ar_outstanding` (jurnal) dan memberi ember berdasar **hari lewat jatuh tempo**:
  `current` (belum jatuh tempo), `1-30`, `31-60`, `61-90`, `91-120`, `120+`. Ringkasan kaos hari ini: current 600.000 · 1–30 3.686.000 ·
  31–60 1.825.000 · total 6.111.000 (12 faktur lewat tempo).
- **AP**: `get_ap_aging_summary` membaca `compute_ap_outstanding` (jurnal), ember yang sama. Kaos: 1–30 1.805.006 (11 tagihan).
- **CACAT**: `get_ap_aging_detail(tenant, as_of)` (per vendor) dan `get_ap_aging_vendor` membaca `bills.bill_date / total_amount /
  paid_amount` (kolom HANTU + cache, bukan jurnal) → **galat `column b.bill_date does not exist`**. Rute `GET /reports/ap-aging/detail`
  memang DIPARKIR (`fitur_belum_tersedia`), jadi tak terpapar; tapi widget per-vendor tak boleh memakainya. Perbaikan = tulis ulang di atas
  `compute_ap_outstanding` (kembar `ar_aging_open_invoices`), migrasi + izin pemilik.
- **Putusan perlu**: permintaan widget "0–30/31–60/61–90/>90" — berdasar **umur dokumen** (tanggal faktur) atau **lewat jatuh tempo**?
  Fungsi yang ada = lewat jatuh tempo (pola NetSuite/QBO A/R Aging). Usul: pakai yang ada; "0–30" = current + 1–30, ">90" = 91–120 + 120+.

## 2. Nilai persediaan
- Hanya kaos yang punya barang ber-stok. **GL peran INVENTORY_MERCHANDISE = Rp 8.139.545; nilai ledger Σ(qty×biaya) = Rp 8.187.539 →
  selisih Rp 47.994.** Qty `warehouse_stock` == qty ledger (81,62). Ini Inv-3 (GL vs ledger) yang TIDAK nol — perlu ditelusuri sebelum widget
  menampilkan "nilai persediaan" (dua sumber, dua angka). Widget wajib memakai GL (Law 1/16), ledger = rincian per barang.
- grapgrap: tagihan sempat mendebit 1-10600 (Rp 4.667.990) lalu direklas penuh (`RECLASSIFY_BILL_INVENTORY`) → saldo 0, konsisten.
- `get_inventory_balance` membaca baris ledger terakhir (`quantity_balance`), bukan Σ — jangan dipakai untuk widget.

## 3. Stok minimum
Kolom ada (`warehouse_stock.min_stock`, `reorder_level`; `products.reorder_level`) tetapi **tak satu pun terisi** (kaos 0/11 baris stok,
0/78 produk). Widget "di bawah stok minimum" akan selalu kosong. Celah = UI/impor untuk mengisi batas (pola NetSuite Reorder Point per
barang per lokasi), bukan kueri.

## 4. Top pelanggan / top barang
- **Pelanggan**: jurnal `INVOICE_REVENUE` (144 jurnal) ber-`source_id` = faktur → `sales_invoices.customer_id`; Σ kredit akun REVENUE per
  periode (`journal_date`). 1,1 ms. Pendapatan **saat diakui** (pola PSAK 72; pengakuan saat kirim), bukan saat faktur.
- **Barang**: tak ada baris pendapatan per barang di jurnal. Sumber terbaik = `sales_invoice_items.recognized_amount` (terisi 55/58 kaos,
  88/88 grapgrap) + `item_id` (57/58, 88/88). `total_cost` baris faktur = 0 di semua baris → **margin per barang tak tersedia** dari faktur;
  HPP per barang harus dari `inventory_ledger` (keluar INVOICE_FULFILLMENT/DELIVERY_NOTE) — hanya barang ber-stok.

## 5. Laba kotor per bulan
Kueri jurnal REVENUE − COGS per bulan (jurnal efektif): 7,9 ms setahun kaos. Hasil hari ini:

| tenant | bulan | pendapatan | HPP |
|---|---|---|---|
| grapgrap | 2026-09 | 189.495.000 | 10.072.495 |
| grapgrap | 2026-10 | 121.820.000 | — |
| kaos | 2026-07 | 5.000.000 | 3.500.000 |
| kaos | 2026-09 | 52.690.000 | 5.286.921 |
| kaos | 2026-10 | 589.000 | — |

**Celah makna**: grapgrap seluruhnya non-stok (putusan pemilik) → akun COGS terisi oleh **biaya beli saat tagihan** (5-10110 Beban Pokok
Jasa, 5-10100 HPP Pembelian), bukan HPP barang terjual; bulan tanpa tagihan = HPP kosong → "laba kotor" naik-turun mengikuti waktu tagihan,
bukan penjualan. Widget wajib memberi catatan metode per tenant (perpetual vs periodik) — putusan tampilan.

## 6. Ringkasan celah (urut dampak)
1. Inv-3 kaos: GL persediaan ≠ ledger Rp 47.994 (telusuri dulu; dua angka untuk satu widget).
2. `get_ap_aging_detail`/`get_ap_aging_vendor` rusak (kolom hantu) — tulis ulang di atas `compute_ap_outstanding` sebelum widget AP per vendor.
3. Stok minimum tak terisi — butuh fitur isi batas, bukan kueri.
4. Laba kotor non-stok = biaya beli periodik — catatan metode / putusan tampilan.
5. Margin per barang tak tersedia untuk non-stok; untuk ber-stok via ledger.
6. Ember aging: konfirmasi "lewat jatuh tempo" (fungsi yang ada) vs "umur dokumen".
