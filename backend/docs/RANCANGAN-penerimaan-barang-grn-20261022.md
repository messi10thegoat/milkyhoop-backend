# RANCANGAN — Penerimaan Barang (GRN, PEMBELIAN-SERAGAM T3) — 22 Okt 2026, BACKEND2

Status: **RANCANGAN, tanpa kode. PUTUSAN PEMILIK P1–P6 sudah diberikan (bagian 6).**
Acuan: NetSuite *Item Receipt* + akun *Accrued Purchases / GRNI* (Goods Received Not Invoiced); SAP MIGO (GR) → MIRO (IR) dengan
akun GR/IR clearing. Kembaran internal: sisi Penjualan sudah memakai pola yang sama — Surat Jalan sebelum faktur
(`services/sj_so.py`, V417): Dr **Barang Terkirim** (peran `GOODS_DELIVERED_NOT_INVOICED`, 1-10660) / Cr Persediaan.

## 1. Keadaan sekarang (diukur 22 Okt, baca-saja)
- `POST /purchase-orders/{id}/receive` hanya menaikkan **penghitung** `purchase_order_items.quantity_received`. Tanpa dokumen, tanpa
  tanggal tersimpan, tanpa gudang, tanpa stok, tanpa jurnal. Tidak ada tabel penerimaan barang (receipt/grn) di skema.
- Stok & jurnal persediaan baru lahir saat **tagihan diposting** (`bills_service`: `record_inventory_inbound` per baris barang
  `item_type='goods' AND track_inventory`, Dr INVENTORY_MERCHANDISE / Cr AP_TRADE). `inventory_ledger` source BILL = 7 baris (kaos).
- Produk: kaos 78 `goods` + track_inventory dan 6 non_inventory; grapgrap 118 non_inventory + 11 service (SENGAJA non_inventory,
  putusan pemilik 26 Sep). Tenant lain non_inventory.
- Peran akun yang ada: AP_TRADE (2-10100), INVENTORY_MERCHANDISE (1-10600), GOODS_DELIVERED_NOT_INVOICED (1-10660), PURCHASE_DISCOUNT,
  VAT_INPUT, … **Tidak ada** peran/akun "Barang Diterima Belum Ditagih" (GRNI) di sisi beli.
- PO nyata: kaos 3 (2 closed, 1 cancelled); penghitung diterima pada PO lama tidak punya dokumen.

## 2. Usulan (pola NetSuite Item Receipt + GRNI)
### Dokumen
- Tabel `goods_receipts` (GRN-YYMM-NNNN per tenant): tenant_id, nomor, purchase_order_id, vendor_id, receipt_date, warehouse_id,
  status (posted | void), journal_id, notes, idempotency, created_*, voided_*.
- `goods_receipt_items`: goods_receipt_id, purchase_order_item_id, item_id, quantity, unit_cost (Decimal 2dp+), amount, inventory_ledger_id.
- `purchase_order_items.quantity_received` menjadi **TURUNAN** Σ baris GRN tak-void (pola `PD.sinkron_tagih` untuk qty tertagih),
  bukan penghitung yang ditulis tangan.

### Jurnal & stok saat GRN diposting (baris barang ber-stok)
- Stok: `inventory_helpers.record_inventory_inbound` (WAC, Rule 3) per baris, source_type `GOODS_RECEIPT`, gudang wajib (Rule 1).
- Jurnal DRAFT→POSTED (Law 20), **Dr Persediaan** (akun produk → INVENTORY_MERCHANDISE) / **Cr Barang Diterima Belum Ditagih (GRNI)**,
  nilai = qty × harga PO neto diskon baris (tanpa PPN — PPN Masukan diakui di tagihan, saat faktur pajak).
- Satu transaksi atomik (ledger + jurnal), kunci `PO:{tenant}:{po}` (Law 13), idempotensi X-Idempotency-Key (Law 14), periode
  tanggal terima terbuka (Law 5), source_type `GOODS_RECEIPT` + `GOODS_RECEIPT_VOID` didaftarkan (Law 6, FK).

### Saat tagihan dari PO diposting (3-way match)
- Baris tagihan bertaut baris PO (V436 `bill_items.purchase_order_item_id`) yang SUDAH diterima: **Dr GRNI** (nilai penerimaan) /
  **Cr Hutang Usaha**; PPN Masukan seperti sekarang. **Tanpa** `record_inventory_inbound` lagi (stok sudah masuk di GRN; mencegah dobel).
- Selisih harga tagihan vs harga penerimaan → akun selisih (lihat P3).
- Baris tagihan tanpa penerimaan (barang belum datang / jasa) → perilaku sekarang.
- Berkas `bills_service.py` milik BACKEND → perubahan jalur posting dikerjakan bersama BACKEND (kontrak antarberkas).

### Void GRN
- Hanya bila baris penerimaannya belum ditagih (tagihan tak-void) dan stoknya belum keluar melebihi saldo (Rule 7 tetap berlaku).
- Jurnal pembalik + `record_inventory_reversal` (Rule 9, `GOODS_RECEIPT_VOID`), jurnal asli tetap POSTED + reversed_by_id (Law 2/26).

### Endpoint (pola kontrak PO tahap 2: penentu bersama, pratinjau read-only)
- `POST /purchase-orders/{id}/receive` → membuat GRN (badan sama + `warehouse_id`); respons + `goods_receipt_id/number`.
- `POST /purchase-orders/{id}/receive/preview` → tambah nilai jurnal yang akan terbentuk (Dr/Cr per akun).
- `GET /goods-receipts`, `GET /goods-receipts/{id}`, `POST /goods-receipts/{id}/void` (+ `/void/preview`), `GET /goods-receipts/{id}/documents`;
  PDF `/documents/goods_receipt/{id}/pdf` (Bukti Terima Barang). Bundel PO mendapat kelompok "Penerimaan".
- Penjaga harian: Σ saldo GRNI per tenant == Σ (nilai GRN tak-void − nilai yang sudah ditagih) (rekonsiliasi akun antara, pola Check 16).

## 3. Pertanyaan — PUTUSAN PEMILIK (aturan akun/jurnal & migrasi)
- **P1 Akun GRNI.** Buat peran baru `GOODS_RECEIVED_NOT_INVOICED` + akun **2-10150 "Barang Diterima Belum Ditagih"** (LIABILITY,
  bukan PAYABLE supaya tak dihitung sebagai hutang vendor di compute_ap_outstanding) di SEMUA tenant lewat migrasi (pola V417 GDNI)?
  Usul: **ya**.
- **P2 Barang non-stok / jasa** (seluruh grapgrap). NetSuite memberi pilihan "accrue on receipt". Usul: **GRN tetap dibuat
  (dokumen + qty), TANPA jurnal** untuk baris non-stok; beban diakui saat tagihan seperti sekarang. Alternatif: akru beban saat
  terima (Dr Beban / Cr GRNI). Pilih?
- **P3 Selisih harga tagihan vs penerimaan.** Usul (biaya rata-rata/WAC, tanpa standard cost): selisih ke **Persediaan** bila
  barangnya masih ada, sisanya ke **HPP** (pola NetSuite untuk average cost); paling sederhana: seluruh selisih ke akun baru
  **"Selisih Harga Pembelian"** (beban). Pilih satu?
- **P4 Data lama.** Penghitung `quantity_received` pada 3 PO kaos (2 closed, 1 cancelled) tanpa dokumen: usul **dibiarkan** (tanpa
  backfill GRN/jurnal ke belakang); hanya penerimaan baru yang membuat GRN. Setuju?
- **P5 Tagihan tanpa PO / tanpa penerimaan** tetap memasukkan stok saat diposting (perilaku sekarang) — hanya baris yang bertaut
  penerimaan memakai GRNI. Setuju?
- **P6 Flag.** Rilis di balik flag tenant `penerimaan_barang_v1` (kaos dulu, uji "TES E2E"), grapgrap menyusul setelah nyata kaos lulus?

## 4. Migrasi (nomor BACKEND2 V439 + minta blok baru ke MASTER bila perlu)
1. V439 tabel goods_receipts/goods_receipt_items + source_type GOODS_RECEIPT/GOODS_RECEIPT_VOID + (P1) peran & akun GRNI per tenant.
2. Kode GRN + perubahan posting tagihan (bersama BACKEND), di balik flag.
3. (sesudah live) `quantity_received` turunan: penulisan tangan dihapus dari /receive.

## 5. Gerbang
Unit (penentu + jurnal seimbang + idempotensi + void), harness DB scratch (PO → GRN sebagian → GRN sisa → tagihan dari PO → void
GRN ditolak bila sudah ditagih; stok/WAC/jurnal/GRNI cocok; kontrol merah), harness read-only (GRNI == penjaga), nyata kaos "TES E2E".

## 6. PUTUSAN PEMILIK (LANGSUNG di sesi BACKEND2, 22 Okt 2026) — mengikat sebelum kode
- **P1 YA**: peran `GOODS_RECEIVED_NOT_INVOICED` + akun **2-10150 "Barang Diterima Belum Ditagih"** (LIABILITY, BUKAN hutang vendor /
  subledger AP) di SEMUA tenant lewat migrasi. Terima: Dr Persediaan / Cr 2-10150. Tagihan dari PO: Dr 2-10150 / Cr Hutang Usaha.
- **P2 TANPA JURNAL** untuk baris non-stok/jasa: GRN = dokumen + qty; beban lahir saat tagihan (bawaan NetSuite non-inventory).
  Akibat: grapgrap (seluruhnya non_inventory/service) nol jurnal GRN.
- **P3 AKUN BARU "Selisih Harga Pembelian"** (beban; pola NetSuite Bill Price Variance) untuk selisih harga tagihan vs harga terima;
  v1 TIDAK menyesuaikan Persediaan/HPP (tanpa revaluasi stok). Penyesuaian biaya rata-rata = tiket berikutnya.
- **P4 DIBIARKAN**: penghitung qty diterima pada 3 PO lama kaos tanpa dokumen/jurnal ke belakang.
- **P5 SETUJU**: tagihan tanpa PO / tanpa penerimaan tetap seperti sekarang (stok masuk saat tagihan diposting).
- **P6 SETUJU**: flag `penerimaan_barang_v1`, kaos dulu (uji "TES E2E"), grapgrap sesudah nyata kaos lulus.

## 7. Tambahan wajib (MASTER 22 Okt)
- **(a) Penjaga GRNI** per tenant: saldo jurnal akun 2-10150 == Σ baris GRN tak-void ber-stok (qty diterima − qty tertagih dari
  penerimaan itu) × harga satuan penerimaan. Fail-closed, dua sisi (sehat HIJAU + injeksi MERAH) sebelum dipercaya (Law 15).
- **(b) Urutan expand-contract + pemilik berkas**:
  1. V439 (BACKEND2): tabel GRN + source_type + peran/akun GRNI & Selisih Harga Pembelian per tenant (aditif).
  2. Kode GRN (BACKEND2: purchase_orders.py, po_rencana.py, services/grn_*.py baru) di balik flag — tagihan BELUM berubah;
     selama flag mati perilaku = sekarang.
  3. Posting tagihan atas baris yang sudah diterima (BACKEND: bills_service.py / bills.py) memakai kontrak fungsi dari BACKEND2
     `grn_tagih.rencana_posting_baris(conn, tenant, bill_items) -> [{bill_item_id, nilai_grni, selisih, perlu_inbound}]` — SATU penentu
     nilai GRNI/selisih; BACKEND memanggilnya di jalur posting & void tagihan. Rilis bersama dalam satu window.
  4. (sesudah live) `quantity_received` turunan Σ GRN; penulisan tangan di /receive dihapus.
- **(c) Tagihan sebagian atas penerimaan sebagian**: per baris PO, qty tagih ≤ (Σ diterima − Σ tertagih) untuk baris ber-stok
  (pola NetSuite "bill based on receipt"); nilai GRNI yang dibalik = qty tagih × harga satuan penerimaan **FIFO per GRN** (penerimaan
  terlama ditagih lebih dulu); selisih (harga tagihan − harga terima) × qty → Selisih Harga Pembelian. Baris tagihan bisa menutup
  beberapa GRN — rincian tautan di tabel `goods_receipt_bill_links` (gr_item_id, bill_item_id, qty, nilai).
- **(d) Retur sebelum ditagih**: dokumen "Retur Penerimaan" (atau void sebagian baris GRN) = kebalikan penerimaan: stok keluar
  (record_inventory_outbound pada biaya penerimaan itu, bukan WAC, agar GRNI kembali nol tepat), Dr 2-10150 / Cr Persediaan; qty
  diterima turun. Retur SESUDAH ditagih tetap lewat Nota Debit (BACKEND, putusan 21–22 Okt). Ditolak bila qty retur > qty belum
  tertagih dari GRN itu.
