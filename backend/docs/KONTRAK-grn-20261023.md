# KONTRAK Penerimaan Barang (GRN) — kode di balik flag `penerimaan_barang_v1` (BACKEND2, 23 Okt 2026)

Dasar: RANCANGAN-penerimaan-barang-grn-20261022.md (putusan pemilik P1–P6 §6, tambahan MASTER §7); migrasi V452 (TERAPAN 23 Okt 10:39Z).

## 0. GERBANG FLAG (syarat MASTER, MENGIKAT)
**Flag `penerimaan_barang_v1` TIDAK BOLEH dinyalakan untuk tenant mana pun sampai sisi tagihan BACKEND (posting & void tagihan memanggil
`grn_tagih` -> Dr GRNI, tanpa inbound ganda) LIVE dan terverifikasi.** Tanpa itu: GRN mengkredit 2-10150, tagihan lama tetap Dr Persediaan
+ inbound -> stok & persediaan DOBEL dan GRNI menumpuk tak pernah dibersihkan. Menyalakan flag = migrasi bernama (izin pemilik) sesudah
gerbang ini.

## 1. Flag MATI (semua tenant sesudah rilis ini)
`POST /purchase-orders/{id}/receive` dan `/receive/preview` BYTE-IDENTIK dengan sebelum rilis (harness uji-nyata-grn-flagmati: LAMA vs
BARU). Tidak ada dokumen, jurnal, atau stok baru. Router `/api/goods-receipts` hidup tapi kosong.

## 2. Flag HIDUP
- `/receive` (+ badan opsional `warehouse_id`): dokumen `goods_receipts` (nomor GRN-YYMM-NNNN), baris ber-stok (item_type goods +
  track_inventory) -> `record_inventory_inbound` (WAC, source GOODS_RECEIPT) + jurnal Dr Persediaan (akun produk/INVENTORY_MERCHANDISE) /
  Cr 2-10150 (peran GOODS_RECEIVED_NOT_INVOICED); nilai = qty x harga PO neto diskon baris tanpa PPN. Baris non-stok/jasa: dokumen saja,
  tanpa jurnal & tanpa stok (P2; seluruh grapgrap). Respons + `goods_receipt_id`, `receipt_number`, `journal_id`.
- `/receive/preview` + `goods_receipt` {warehouse, items[stocked, unit_cost, amount], journal[], grni_amount}; blok GRN digabung
  (GUDANG_WAJIB, PERIODE_TUTUP, PERAN_AKUN_BELUM_DIPETAKAN).
- `GET /api/goods-receipts` (purchase_order_id, status, limit, offset), `GET /api/goods-receipts/{id}` (+ items, quantity_billed),
  `POST /{id}/void` (reason wajib, X-Idempotency-Key), `POST /{id}/void/preview` (blok + jurnal pembalik). Izin modul purchase_order R/V.
- Void: ditolak GRN_SUDAH_DITAGIH / ALASAN_WAJIB / PERIODE_TUTUP / STOK_SUDAH_KELUAR; pembalik jurnal (asli tetap POSTED + reversed_by_id),
  `record_inventory_reversal`, qty diterima PO turun, status PO lewat po_daftar.sinkron_tagih.

## 3. Kontrak tagihan (BACKEND memanggil; BACKEND2 memiliki `services/grn_tagih.py`)
`rencana_posting_baris(conn, tenant_id, bill_items)` -> per baris tagihan bertaut baris PO dengan GRN ber-stok terbuka: FIFO per GRN,
`nilai_grni` (Dr 2-10150), `selisih` (Dr/Cr 5-10400 PURCHASE_PRICE_VARIANCE), `qty_inbound` (bagian yang belum diterima = perilaku lama),
`tautan`. Tautan penghabis baris GRN memakai sisa nilai persis (GRNI kembali 0 tepat). `tulis_tautan` (posting) / `balik_tautan` (void).
Flag mati -> []. Usul BACKEND (disetujui): tolak posting bila baris ber-stok ber-GRN punya `qty_inbound > 0` (3-way match ketat §7c).
Urutan kunci: tagihan -> PO (jalur GRN tak pernah mengunci tagihan; tak ada siklus).

## 4. Penjaga GRNI (`services/grn_penjaga.periksa`)
Saldo GL efektif 2-10150 == Σ (qty − retur) x harga terima baris GRN ber-stok tak-void − Σ nilai GRNI tautan aktif. Fail-closed = GALAT bila
peran tak terpetakan. Fungsi PEMERIKSA saja: TIDAK dipanggil di jalur tulis mana pun -> tidak memblok tagihan/penerimaan apa pun
(termasuk tenant flag mati). Dipakai harness; pemasangan ke cek harian = tiket sesudah flag hidup.

## 5. Belum di rilis ini
Retur penerimaan sebelum ditagih (§7d), dokumen PDF/bundel "Penerimaan" di PO, `quantity_received` turunan (langkah 4 §7b), sisi tagihan BACKEND.
