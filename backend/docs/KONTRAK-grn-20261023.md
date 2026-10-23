# KONTRAK Penerimaan Barang (GRN) — flag `penerimaan_barang_v1` (BACKEND2, 23 Okt 2026). §6 ATURAN D + §7 SERAGAM = keadaan terkini

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
Review BACKEND 23 Okt (WAJIB, putusan MASTER): pemanggil mengirim `nilai_neto` per baris (neto SEMUA diskon termasuk header invoice/tunai, tanpa PPN, dari tagihan_rencana.hitung = fungsi jurnal) dan `qty_dasar` (satuan PO/GRN); satuan beda tanpa qty_dasar -> butir `blok` SATUAN_TAK_TERKONVERSI. Retur/void GRN SESUDAH ditagih DITOLAK (lewat Nota Debit, pola NetSuite/QBO); qty/nilai terbuka tak pernah negatif (dijepit + tes). Flag mati -> []. Usul BACKEND (disetujui): tolak posting bila baris ber-stok ber-GRN punya `qty_inbound > 0` (3-way match ketat §7c).
Urutan kunci: tagihan -> PO (jalur GRN tak pernah mengunci tagihan; tak ada siklus).

## 4. Penjaga GRNI (`services/grn_penjaga.periksa`)
Saldo GL efektif 2-10150 == Σ (qty − retur) x harga terima baris GRN ber-stok tak-void − Σ nilai GRNI tautan aktif. Fail-closed = GALAT bila
peran tak terpetakan. Fungsi PEMERIKSA saja: TIDAK dipanggil di jalur tulis mana pun -> tidak memblok tagihan/penerimaan apa pun
(termasuk tenant flag mati). Dipakai harness; pemasangan ke cek harian = tiket sesudah flag hidup.

## 5. Belum di rilis ini
Retur penerimaan sebelum ditagih (§7d), dokumen PDF/bundel "Penerimaan" di PO, `quantity_received` turunan (langkah 4 §7b), sisi tagihan BACKEND.

---

## 6. ATURAN D — urutan terima/tagih BEBAS (putusan LANGSUNG pemilik 23 Okt, pola SAP GR/IR) — MENGGANTIKAN §2–§4 di atas bila bertentangan
LIVE `0012b264` (15:19Z); flag kaos NYALA `4b813193` (V454, 16:01Z); tenant lain MATI.
- **Terima dulu**: GRN Dr Persediaan R (qty × harga PO neto diskon baris, tanpa PPN) / Cr GRNI 2-10150. Tagihan sesudahnya (baris PO ber-stok):
  Dr GRNI R (tautan `terima_dulu`, FIFO GRN terlama) + Dr/Cr Selisih Harga 5-10400 (B − R) / Cr Hutang. **Tagihan TIDAK PERNAH memasukkan stok.**
- **Tagih dulu**: tagihan Dr GRNI B (neto tagihan, `tagihan_aksi.neto_tagihan_terposting`) tanpa stok = *tertunda*. GRN sesudahnya memasang diri
  ke tertunda (FIFO jurnal tagihan; `grn_pasang.pasangkan`): stok masuk qty penuh @ R; jurnal Dr Persediaan R / Dr|Cr Selisih Σ(B−R) /
  Cr GRNI (R bebas + Σ B); tautan `tagih_dulu` (grni_amount = R, nilai_tagih = B).
- **Legacy**: tagihan lama (kartu stok BILL, sebelum flag) — GRN = baris `ber_stok=false` + tautan `legacy`, tanpa stok/jurnal. Tagihan lama
  dengan produk sama > 1 baris = blok `TAGIHAN_LAMA_PRODUK_GANDA` (tak ditebak).
- **PO yang sudah ditagih** (billed/partial_billed) tetap bisa DITERIMA hanya saat flag nyala (`po_rencana.STATUS_BISA_TERIMA_D`); status
  tagih tak diturunkan (`po_daftar.sinkron_tagih`).
- **Void simetris**: dokumen yang LEBIH DULU tak bisa dibatalkan selagi tercocok — GRN diblok tautan `terima_dulu` (`GRN_SUDAH_DITAGIH`);
  tagihan diblok tautan `tagih_dulu`/`legacy` (`TAGIHAN_TERCOCOK_GRN`, BACKEND). GRN yang datang sesudah tagihan BOLEH dibatalkan → tautan
  dibalik, tagihan kembali tertunda.
- **Penjaga**: #1 GL GRNI == Σ GRN ber-stok − Σ grni_amount tautan aktif − Σ nilai_terbuka tertunda bukan-legacy; #2 ganda/tanpa_tautan;
  #3 pemasangan terlewat (baris PO dengan GRN ber-stok terbuka DAN tagihan tertunda) — `grn_penjaga.periksa` / `periksa_tagihan`.
- Bukti: e2e gabungan `scripts/ops/jalan-gerbang-grn-d.sh` (53 PASS; merah/merah_fifo/merah_d MERAH); flag mati LAMA == BARU
  (`scripts/ops/jalan-grn-flagmati-lama-baru.sh`, kaos + grapgrap-manado).

## 7. SERAGAM (FE GRN, ANDROID) — ADITIF, `services/grn_daftar.py` + `routers/goods_receipts.py`
| Rute | Medan |
|---|---|
| `GET /api/goods-receipts?purchase_order_id=&status=&aksi=belum_ditagih&limit&offset` | baris + `billing_state` (`batal`/`belum_ditagih`/`sebagian_ditagih`/`sudah_ditagih`/null), `status_text`, `grni_open_amount` (2dp), `aksi`, `aksi_semua` |
| `GET /api/goods-receipts/summary?purchase_order_id=` | `total`, `posted`, `void`, `belum_ditagih_count`, `belum_ditagih_value` (Σ grni_open; predikat SAMA ?aksi=) |
| `GET /api/goods-receipts/{id}` | header (medan baris) + `items[]` + `bills[]` {bill_id, bill_number, bill_status, kind, goods_receipt_item_id, quantity, received_amount, billed_amount (null legacy), variance} |
| `GET /api/goods-receipts/{id}/documents` | `{anchor{kind:"goods_receipt", position_text, total, paid:null, balance: grni_open (null bila batal)}, default{group:"grn"}, groups[po, grn, faktur]}` |
| `GET /api/goods-receipts/{id}/history?limit=` | `{events[{at, jenis, ringkas, aktor, dokumen}], total, omitted}`; jenis GRN_DITERIMA / GRN_DIBATALKAN / GRN_DITAGIH / GRN_TAUTAN_DILEPAS |
| `POST /api/goods-receipts/{id}/void/preview` | + `reversal_journal[].account_code/account_name`, `before`/`after` {status_text, grni_open_amount} |
- `billing_state` dari qty baris yang DAPAT ditautkan (ber-stok atau legacy). GRN berisi barang non-stok/jasa saja → `null` + "Diterima"
  (tagihan atas baris non-stok tak bertaut GRN).
- `grni_open_amount` = rumus penjaga #1 per dokumen; Σ atas GRN posted == penjaga #1 terbuka_dokumen + tagihan_tertunda (harness p2).
- Izin: GET `purchase_order` R (termasuk documents|history); batal `purchase_order` V. Flag FE = flag BE `penerimaan_barang_v1`.
- Harness: `scripts/ops/jalan-gerbang-grn-seragam.sh` (kaos rollback; paritas p1/p2/p3; merah/merah_grni).
