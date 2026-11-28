# BUG-450 — tabel (a): jalur keluar stok vs FIFO (dibaca dari kode deploy/master, 28 Nov 2026)
Satu-satunya pemakai `fifo_lapisan` = `inventory_helpers` (`record_inventory_outbound`/`inbound`/reverse). Jalur lain menulis `tulis_kartu` langsung dengan WAC.
| Jalur | Berkas | HPP | FIFO? |
|---|---|---|---|
| Retur beli (vendor credit) | vendor_credits.py:1436 -> record_inventory_outbound | FIFO bila aktif | YA |
| Void/pembalik umum | inventory_helpers.py:~647 (rencana_balik/catat_balik) | pulih dari lapisan | YA (tapi jalur lain tak membuat moves) |
| Kirim dari Faktur (/fulfill, SO-fulfill, auto-fulfill) | sales_invoices._execute_fulfillment | WAC | TIDAK -> DIPERBAIKI di cabang be/bug450-fifo-kirim-faktur |
| SJ dari SO | sj_so.py:427 | WAC (wac) | TIDAK (menyusul) |
| Penjualan POS | transactions.py:233 | ? (kartu langsung) | TIDAK (perlu ukur biaya) |
| Penerimaan penjualan (receipt) SALE + void | sales_receipts.py:489/965 | WAC/ snapshot | TIDAK |
| Penyesuaian stok keluar | stock_adjustments.py:931 | WAC | TIDAK |
| Pemindahan antar gudang | pemindahan_barang.py:505 | WAC dibawa (netto stok 0) | n/a (nilai tetap; lapisan per tenant) |
| Produksi: bahan keluar MATERIAL_ISSUE | production.py:1909 (+reversal 1398) | WAC | TIDAK |
| Kernel dokumen (SALE generik) | kernel_document_executor.py:915 | unit_cost dokumen | TIDAK |
| Revaluasi PPV | revaluasi_ppv.py:83 | selisih nilai | n/a |
Akibat: barang FIFO aktif yang keluar lewat jalur TIDAK memakai WAC & tak membuat inventory_layer_moves -> lapisan tak berkurang (stok lapisan > stok kartu), void ditolak FIFO_SEBELUM_MULAI. Perbaikan butir ini: hanya Kirim dari Faktur (kode+tes). Jalur TIDAK lain = tiket lanjutan (tiap satu mengubah HPP -> GO pemilik).
