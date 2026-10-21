# AUDIT PEMBELIAN-SERAGAM — Tahap 0 (BACA-SAJA, tanpa kode) — 22 Okt 2026

Pemilik paket: MASTER. Berkas ini DIBAGI dua sesi BE (agar tak bentrok): **BAGIAN B (BACKEND2)** = Pesanan Pembelian, Uang Muka Pembelian,
Vendor/Suppliers, Recurring Bills, rancangan GRN. **BAGIAN A (BACKEND)** = Bill, Bayar Bill, Vendor Credit — ditambahkan BACKEND di atas bagian ini
(rebase ke master sebelum menambah; jangan menyunting bagian lain).

Metode: kode `master 4c896500` dibaca; data produksi diukur BACA-SAJA (kontainer sekali-pakai, `default_transaction_read_only=on`); tidak ada tulisan.
Acuan = kontrak Penjualan seragam (`KONTRAK-penjualan-seragam-20261020.md` §0 + pola SO/UM): aksi, keadaan, position_text, with_totals, documents/bundel,
paritas detail==baris, kode order, tiebreak, paginasi, idempotensi, teks Indonesia, uang dari jurnal.

---------------------------------------------------------------------------------------------------------------------------------------

# BAGIAN A — BACKEND (Faktur Pembelian · Pembayaran Keluar · Nota Debit)

Dasar: master `ea085526`. Tolok ukur: `KONTRAK-penjualan-seragam-20261020.md` + `KONTRAK-detail-faktur-s1-20261020.md`
(Faktur ↔ Faktur Pembelian, Penerimaan ↔ Pembayaran Keluar, Nota Kredit ↔ Nota Debit). Tanpa kode, tanpa tulis DB (SELECT saja).
Status: **ADA** · **SEBAGIAN** · **TIDAK**. Klaim berangka di bawah DIUKUR ULANG oleh BACKEND (bukan hanya dibaca dari kode) — ditandai ✔.

## A0. Data (22 Okt)

| modul | tabel | grapgrap-manado | kaos-biru-konveksi |
|---|---|---|---|
| Faktur Pembelian | `bills` | 18 paid | 33: draft 4 · posted 17 · paid 10 · void 2 |
| Pembayaran Keluar | `bill_payments_v2` (+ `bill_payment_allocations`) | 18 posted | 9: posted 8 · voided 1 |
| Nota Debit | `vendor_credits` (+ items/applications/refunds) | 0 | 0 (**0 di SEMUA tenant — belum pernah dipakai**) |

**`bills.status` vs `status_v2` ✔ (dua kolom status, tak selaras):** grapgrap paid/posted 18; kaos draft/draft 3 · draft/NULL 1 · paid/paid 2 · **paid/posted 7** · **paid/void 1** · posted/posted 17 · void/void 2. Angka per status di tabel atas = kolom `status`; bagian B memakai `status_v2` (karena itu hitungannya berbeda). Satu sumber status = bagian dari U0 Faktur Pembelian.

Tabel lama `bill_payments` = 0 baris, tanpa rujukan kode. Jurnal: grapgrap BILL 18 / BILL_PAYMENT 18; kaos BILL 29 / BILL_PAYMENT 10 /
VENDOR_OPENING_BALANCE 1. Fungsi AP: `compute_ap_outstanding`, `compute_ap_summary` (satu overload). Belum ada flag tenant Pembelian.

## A1. Faktur Pembelian — `routers/bills.py`, `services/bills_service.py`

| # | butir | status | bukti | celah |
|---|---|---|---|---|
| 1 | `aksi` baris + `?aksi=` + summary `{k}_count/_value` satu predikat | TIDAK | list hanya `status` all/active/paid/unpaid/partial/overdue (bills.py:549-575); summary punya FILTER sendiri (bills_service.py:2334-2470) — partial & overdue TUMPANG-TINDIH di summary, eksklusif di list | modul aksi (SQL+Python kembar) seperti faktur |
| 2 | keadaan/teks server (`jatuh_tempo_*`, `position_text`) | SEBAGIAN | list: enum `status` + `doc_status`; detail: `is_overdue/overdue_days` saja; tanpa jatuh_tempo_*/position_text | kembar `posisi_faktur` + `jatuh_tempo_*` dari sisa jurnal |
| 3 | `with_totals` sum_total/sum_sisa | TIDAK | tak ada param; `BillListResponse` ketat | tambah (sisa jurnal) |
| 4 | `documents[]`/`doc_count` + `/bills/{id}/documents` | TIDAK | dokumen.py & dokumen_bundel tanpa jenis pembelian; "documents" di bills.py = LAMPIRAN | jenis bundel pembelian (tagihan, bayar keluar, nota debit, DP vendor, PO, penerimaan barang) |
| 5 | `?search=` daftar + summary | SEBAGIAN | list: nomor/nama vendor; summary tanpa search & dibatasi periode | `_syarat_cari` bersama; putuskan summary per periode atau tidak |
| 6 | paginasi + has_more + tiebreak id | SEBAGIAN | skip/limit/total/has_more ADA; ORDER BY tanpa `, b.id` (utils/sorting.py:43-64) | tiebreak |
| 7 | paritas detail == baris | SEBAGIAN | list terbayar = CTE + jalur ADJUSTMENT; 2 detail (`/{id}`, `/v2/{id}`) tanpa ADJUSTMENT; predikat void beda (list `status`, v2 `status OR status_v2`); tipe uang beda (int vs string) | satu pembentuk baris; gabung v1/v2 |
| 8 | **uang dari jurnal** | SEBAGIAN (dasar salah) | list/detail: sisa = `b.amount` (CACHE) − `BILL_JOURNAL_PAID_CTE`; hanya overdue summary & `/outstanding-summary` memakai `compute_ap_outstanding`; `mark_paid` & pagar ubah/hapus memakai cache `amount_paid` | satu sumber (`compute_ap_rincian`, kembar V430) |
| 9 | teks galat Indonesia | SEBAGIAN | ±30/55 detail router Inggris + ±30 pesan service Inggris; galat mentah bocor (bills.py:1205, 1586, 1592); tanpa `teks_galat` | pindah ke teks_galat |
| 10 | idempotensi | SEBAGIAN | `POST /bill-payments` ADA; `POST /bills`, `/bills/v2`, legacy `POST /bills/{id}/payments` TIDAK | execute_idempotent; pensiunkan/jaga jalur legacy |
| 11 | pagar periode | SEBAGIAN | hanya trigger DB (pesan Inggris → 500 "Failed to post bill"); tanpa pra-cek | pra-cek → 400 Indonesia |
| 12 | response_model | SEBAGIAN | list KETAT (medan baru dibuang); detail/summary `Dict` | perluas skema |

**Uang.** `BILL_JOURNAL_PAID_CTE` (list/detail/summary) dibanding `compute_ap_outstanding` KURANG: pembagian proporsional pembayaran multi-tagihan
(perbaikan V245), aplikasi nota debit, aplikasi DP vendor, dan dasar = kredit AP jurnal tagihan (memakai `b.amount`). Hari ini 0 pembayaran
terbagi / 0 nota debit / 0 DP vendor → laten. **Yang sudah nyata ✔: kaos 8 tagihan `posted` (BILL-2609-0028…0035, Rp 6.580.000) yang
jurnal BILL-nya sudah DIBALIK (`reversed_by_id`) — daftar/detail menampilkan terutang penuh, `compute_ap_outstanding` = 0.** grapgrap 18/18 cocok.

**Risiko.** Status void dibaca dari `b.status` tapi draf dari `status_v2` → BILL-2609-0002 (status paid, status_v2 void) tampil "paid" & masuk
"active"; BILL-2609-0007 (draft, status_v2 NULL) tampil "overdue" 50.000; summary mengecualikan keduanya → daftar ≠ summary. Filter nominal
memakai `COALESCE(grand_total, amount)`, tampilan memakai `amount`. `mark_paid` membayar sebesar `amount − amount_paid` (cache). Endpoint
v1/v2 kembar; jalur bayar kedua tanpa idempotensi. Kolom hantu: tak ditemukan.

**Ukuran: L ≈ 6 unit.** U0 sumber uang (`compute_ap_rincian` migrasi + `sisa_tagihan` + predikat void/draf; gerbang lawan compute_ap_outstanding
2 tenant) → U1 kontrak daftar → U2 paritas detail (gabung v1/v2) → U3 bundel dokumen; U4 teks+periode, U5 idempotensi paralel sesudah U0.
**8 tagihan kaos ber-jurnal-dibalik = putusan data (bukan kode).**

## A2. Pembayaran Keluar — `routers/bill_payments.py` (+ `bills_service.record_payment`)

| # | butir | status | bukti | celah |
|---|---|---|---|---|
| 1 | `aksi` + `?aksi=` + summary `{k}_count/_value` | TIDAK | list tanpa aksi (BP:588-601); summary hanya by_status/by_method/periode (BP:766-881) | `pembayaran_keluar_daftar.py` kembar penerimaan_daftar (`belum_dialokasikan`) |
| 2 | `alokasi_keadaan/_teks`, `position_text` | TIDAK | ListItem hanya cache `allocated_amount/unapplied_amount` + bill_count | alokasi_fakta + teks sama dgn Penjualan |
| 3 | `with_totals` | TIDAK | — | tambah |
| 4 | `documents[]` + bundel | TIDAK + **RUTE BENTROK** | `GET /bill-payments/{id}/documents` SUDAH ADA = daftar LAMPIRAN (BP:2450-2504) | migrasi pemanggil FE lampiran atau jalur lain; jenis render kwitansi bayar keluar |
| 5 | `?search=` daftar + summary | SEBAGIAN | list ADA (BP:637-653); summary tanpa param | `_syarat_cari` bersama |
| 6 | paginasi + tiebreak | SEBAGIAN | skip/limit≤100 + has_more ADA; ORDER BY tanpa id (BP:725) | tiebreak |
| 7 | paritas detail == baris | TIDAK | list total = Σ debit PAYABLE jurnal, detail = `bp.total_amount`; baris hantu → detail 404; voided/draf tak pernah tampil di daftar tapi bisa dibuka di detail | daftar berbasis wrapper; satu pembentuk baris |
| 8 | **uang alokasi** | TIDAK (cache, dan SALAH) | list/detail membaca `allocated_amount`/`unapplied_amount` cache | turunan Σ alokasi pembayaran posted (pola `dengan_turunan` Penerimaan) |
| 9 | teks Indonesia | SEBAGIAN | ±32/38 detail Inggris ("Payment not found", "Amount exceeds remaining"…); pesan sukses Inggris | teks_galat |
| 10 | idempotensi | ADA (router) / TIDAK (`record_payment`) | BP:1156-1173, 1525; medan body `idempotency_key` disimpan tapi tak dipakai dedup | tambah di record_payment |
| 11 | pagar periode | ADA | post BP:1610; void cek tanggal bayar + hari ini BP:1759/1764; trigger DB | — |
| 12 | response_model | ADA (ketat) | ListItem/Detail tanpa extra → medan baru dibuang | perluas skema |

**Uang.** Jurnal BENAR: posting Dr AP = dialokasikan, Dr AP_PREPAID sisa, Cr bank; void = jurnal cermin. `compute_ap_outstanding` membaca
pembayaran dari `bill_payments_v2.journal_id` dibagi proporsional per alokasi (V245) → sisa tagihan turunan jurnal ✔ (Law 16/29).
**Cache `allocated_amount` SALAH ✔: grapgrap 18/18, kaos 6/8 pembayaran posted menampilkan "dialokasikan Rp 0" padahal penuh** — penulis
kedua `bills_service.record_payment` (bills_service.py:1766) INSERT tanpa allocated/unapplied (default 0). Jurnal AP debit == total di semua 26
baris (buku besar benar; yang salah tampilan). Backfill 24 baris = data uang → izin pemilik.

**Risiko.** (1) Daftar dibangun dari JURNAL (CTE settlements, BP:678-713), bukan wrapper: kaos ✔ 10 baris hantu (9 MANUAL + 1
VENDOR_OPENING_BALANCE, nama "Settlement", tanpa vendor) ikut daftar + summary; detail-nya 404. Nota debit / aplikasi DP kelak ikut muncul.
(2) Pembayaran voided & draf TAK PERNAH tampil di daftar (`?status=voided/draft` selalu kosong). (3) Void TIDAK atomik: update PPh + `status
='voided'` di LUAR transaksi (±BP:1911-1940) → jurnal bisa terbalik sementara wrapper tetap posted. (4) Posting draf tak memeriksa ulang sisa
jurnal tagihan → bisa kelebihan bayar. (5) Dua penulis berbeda perilaku (cache, idempotensi, nomor seri).

**Ukuran: L ≈ 4 unit BE + 1 FE.** U1 uang+identitas baris (daftar berbasis wrapper, turunan alokasi, perbaiki INSERT record_payment, void
atomik, cek ulang sisa saat posting; backfill = izin pemilik) → U2 kontrak daftar (aksi/summary/search/with_totals/tiebreak/skema + harness
paritas) → U3 bundel + jenis render (+ migrasi rute lampiran) ; U4 teks + idempotensi record_payment.

## A3. Nota Debit — `routers/vendor_credits.py` (0 baris di semua tenant: semua dari kode)

| # | butir | status | bukti | celah |
|---|---|---|---|---|
| 1 | `aksi` + `?aksi=` + summary | TIDAK | list hanya status/vendor/search/tanggal (VC:208-330); summary per status cache | `nota_debit_daftar` kembar nota_kredit_daftar |
| 2 | `sisa_keadaan`/`status_text` | TIDAK | status mentah saja | tambah |
| 3 | `with_totals` | TIDAK | — | sisa jurnal |
| 4 | documents + bundel | TIDAK | tak ada jenis/PDF nota debit | jenis + bundel + PDF |
| 5 | search daftar + summary | SEBAGIAN | summary tanpa param | `_syarat_cari` bersama |
| 6 | paginasi + tiebreak | SEBAGIAN | ORDER BY tanpa id (VC:286) | tiebreak |
| 7 | paritas + terpakai/dikembalikan jurnal | SEBAGIAN | sisa = total − amount_applied − amount_received (CACHE, trigger) | turunan jurnal |
| 8 | **uang** | TIDAK (rusak) | lihat bawah | U0 |
| 9 | aturan NK potongan | TIDAK | semua alasan → Cr COGS_PURCHASE_RETURN + KELUARKAN STOK (juga potongan harga); tak ada blok > sisa tagihan, tanpa baris-wajib, tanpa pratinjau | peta alasan→akun per peran (butuh putusan akun diskon pembelian), stok hanya retur/rusak, blok, pratinjau, PKP bruto |
| 10 | teks Indonesia | SEBAGIAN | ±32/45 Inggris | teks_galat + kode `VC_*` |
| 11 | idempotensi | TIDAK | hanya advisory lock; klik ganda refund = 2 refund + 2 jurnal | idem_buat/mulai_aksi |
| 12 | periode + void cermin | SEBAGIAN | void benar (Law 2: asli tetap POSTED + reversed_by_id) tapi TIDAK membalik keluar-stok, jurnal VENDOR_CREDIT_COGS, baris DTL; tanpa unapply/void-refund | lengkapi |
| 13 | response_model | ADA | perluas | — |

**Uang + jurnal.** Posting: Dr AP_TRADE total, Cr COGS_PURCHASE_RETURN subtotal, Cr VAT_INPUT (PKP) — akun per peran (Law 27 ✔). **Terapkan
ke tagihan TIDAK menulis jurnal dan tak mengisi `vendor_credit_applications.journal_id` ✔** (INSERT VC:1380-1383 tanpa journal_id), sedangkan
`compute_ap_outstanding` cabang nota debit JOIN lewat `vca.journal_id` → aplikasi tak pernah mengurangi sisa tagihan; cabang "belum terpakai"
tak mengurangi refund. Sebaliknya `get_bill_remaining_from_journal` (VC:119-168) + 2 salinan di bill_payments.py mengurangkan SELURUH debit AP
nota debit ke setiap tagihan yang ditautkan. Buku besar AP total benar; per-tagihan salah ke DUA arah. source_type terdaftar ✔ (tanpa FK 500).

**Risiko (urut).** (1) **Kolom hantu → 500 saat dipakai ✔**: PATCH menulis `vendor_credit_date` (kolom = `credit_date`); `vendor_credits.tax_invoice_id`
tak ada tapi dibaca (VC:1866, tax_invoices.py:282/568/987) → faktur pajak dari nota debit + e-Faktur massal meledak. (2) Atribusi AP di atas.
(3) Refund hanya `bank_account_id` → `UUID(None)` TypeError 500 (VC:1588/1624); akun kas tak diperiksa tipenya. (4) Void meninggalkan stok/HPP/DTL.
(5) Potongan harga mengeluarkan stok (dicatat 'SALE'). (6) Tanpa filter tenant saat membaca tagihan asal (VC:607-611 — nomor tagihan tenant
lain bocor) dan vendor_id. (7) Terapkan tak memeriksa status tagihan (draf/void lolos). (8) PATCH menolkan diskon/pajak header. (9) e-Faktur massal
memfilter status 'approved' yang tak diizinkan CHECK.

**Ukuran: L ≈ 5 unit BE + 1 nyata.** 0 baris = tanpa migrasi data, tapi SEMUA jalur tulis belum pernah jalan (2 pasti 500) → tiap unit wajib nyata
kaos. U0 kebenaran uang (pilih satu definisi aplikasi→tagihan [cermin cara AR memperlakukan aplikasi NK: menulis original_invoice_id, cabang 2]
atau jurnal saat terapkan; perbaiki compute_ap_outstanding + hapus 3 salinan; refund; void lengkap; unapply; kolom hantu) → U1 aturan potongan
(butuh putusan akun "Diskon Pembelian") → U2 daftar seragam → U3 dokumen → U4 idempotensi + teks.

## A4. Ringkasan + urutan usulan

| modul | ukuran | penghalang pertama | putusan yang dibutuhkan |
|---|---|---|---|
| Faktur Pembelian | L ≈ 6 | U0 sumber uang (sisa = cache − CTE) | 8 tagihan kaos ber-jurnal-dibalik (Rp 6,58 jt): data |
| Pembayaran Keluar | L ≈ 4 BE + FE | U1 daftar dari jurnal + cache alokasi salah 24/26 | backfill cache 24 baris (izin pemilik) |
| Nota Debit | L ≈ 5 + nyata | U0 aplikasi tanpa jurnal + kolom hantu | akun "Diskon Pembelian" (peran baru?) untuk potongan |

Usul urutan (uang dulu, Law 1/16/29): **(1)** `compute_ap_rincian` (migrasi kembar V430) + sumber uang Faktur Pembelian →
**(2)** Pembayaran Keluar daftar berbasis wrapper + turunan alokasi → **(3)** Nota Debit U0 (kebenaran uang + kolom hantu) →
lalu kontrak daftar/paritas/bundel tiap modul (pola Penjualan, bisa dibagi BACKEND/BACKEND2), FE terakhir per modul.
Bundel dokumen Pembelian butuh jenis render baru (tagihan, kwitansi bayar keluar, nota debit) + rute `/bill-payments/{id}/documents` yang kini dipakai lampiran.

---------------------------------------------------------------------------------------------------------------------------------------

# BAGIAN B — BACKEND2

## B0. Data (produksi, 22 Okt 2026)

| Modul | grapgrap | kaos | Catatan |
|---|---|---|---|
| `purchase_orders` (+items) | 0 | 0 | **NOL baris di semua tenant** — seluruh jalur PO tak pernah terpakai di produksi; bug laten tak pernah terlihat |
| `vendor_deposits` (+applications/refunds) | 0 | 0 | idem; FE: **tidak ada pemanggil** `/api/vendor-deposits` |
| `recurring_bills` (+items) | 0 | 0 | FE: tak ada pemanggil |
| `vendors` | 25 | 7 | master hidup (`/api/vendors`); kolom `is_active` (bukan `status`) |
| `suppliers` (tabel) | 0 | 0 | tabel kosong; router `/api/suppliers` hanya **autocomplete/pencarian di atas tabel `vendors`** (dipakai ChatPanel PurchaseForm) — BUKAN master kedua |
| `bills` (referensi) | posted 18 | draft 3 · paid 2 · posted 24 · void 3 · (status_v2 NULL 1) | `bills.purchase_order_id` terisi = **0** |
| `goods_receipts`/`purchase_receipts` | — | — | **TABEL TIDAK ADA** (tak ada GRN) |
| jurnal terkait pembelian | BILL 47 · BILL_PAYMENT 28 · RECLASSIFY_BILL_INVENTORY 1 · VENDOR_OPENING_BALANCE 1 | | tak ada source_type PO/GRN/VENDOR_DEPOSIT (UM vendor belum pernah diposting) |

Konsekuensi: perbaikan di bagian B **tidak mengubah angka tenant mana pun** (aman diperbaiki/dirombak), tetapi juga **tak ada bukti nyata**; setiap
unit wajib dibuktikan lewat data sintetis + uji tulis 'TES E2E' di kaos (pola perlu_difaktur).

## B1. Jawaban pertanyaan khusus MASTER

### (a) PO `/receive`, GRN, GRNI, 3-way match
- `POST /purchase-orders/{id}/receive` hanya **menaikkan `purchase_order_items.quantity_received`** dan menghitung `amount_received` (kolom cache) + status.
  **Tidak menulis stok** (`inventory_ledger`/`warehouse_stock`), **tidak menulis jurnal**, tidak memilih gudang, `receive_date` diterima di badan tetapi **diabaikan**.
- **BUG PASTI**: `new_qty_received = Decimal(...) + recv_item.quantity_received` — skema `ReceiveItemRequest.quantity_received: float` → `Decimal + float` =
  `TypeError` (dibuktikan di kontainer) → ditangkap `except Exception` → **setiap `/receive` menjawab 500 "Failed to receive goods"**. Tersembunyi karena PO = 0 baris.
- Tidak ada dokumen penerimaan barang yang berdiri sendiri (GRN): hanya penghitung kumulatif per baris PO → tak bisa dibatalkan per penerimaan, tak ada nomor,
  tanggal, gudang, riwayat, kwitansi/surat terima.
- Stok masuk **hanya saat Bill diposting** (`bills_service` → `record_inventory_inbound(source_type='BILL')`, Dr `INVENTORY_MERCHANDISE` / Cr `AP_TRADE`). Jadi alur
  sekarang = **2-way (PO ↔ Bill)**, stok & hutang lahir bersamaan di faktur — barang yang sudah tiba tapi faktur belum datang tidak tercatat di buku mana pun.
- **GRNI (Barang Diterima Belum Ditagih): TIDAK ADA** — tak ada `AccountRole` (penjualan punya `GOODS_DELIVERED_NOT_INVOICED` → 1-10660 'Barang Terkirim'; sisi beli tak ada
  padanannya), tak ada akun CoA.
- **3-way match tidak ada**: `/to-bill` boleh dari status `sent` (barang belum diterima), tidak membandingkan qty faktur dengan qty diterima, tidak membandingkan harga
  faktur dengan harga PO setelah bill diedit, dan `bill_items` **tak punya tautan baris PO** (`purchase_order_item_id`) — hanya `bills.purchase_order_id` (header).
- `/to-bill` membuat **Bill DRAFT** (nomor `generate_bill_number`), menaikkan `quantity_billed` **saat draf dibuat**; `bills.py` (hapus draf/void) **tidak menyentuh**
  `purchase_order_items.quantity_billed` → draf dihapus = PO tertahan 'partial_billed/billed' selamanya (qty tertagih tak kembali). Rumus bill memakai `BillCalculator`
  HALF_UP (sudah diperbaiki 'D'), tetapi PO sendiri masih `int()` (lihat G6).

### (b) Uang Muka Pembelian: akun peran & penerapan ke bill
- Peran akun **ADA**: `AP_PREPAID` → **1-10550 'Uang Muka Pembelian'** (ASSET) di semua tenant (kaos terukur). **Kode `vendor_deposits.py` TIDAK memakai peran itu.**
- **BUG AKUN SALAH (laten, uang)**: `post`/`apply`/`refund` memakai `resolve_account_id(conn, tenant, "1-10800")` — di SEMUA tenant 1-10800 = **'PPN Masukan' (`VAT_INPUT`)**,
  bukan uang muka. `seed_vendor_deposit_account` (`INSERT … ON CONFLICT DO NOTHING`, nama 'Uang Muka Vendor') **tak pernah membuat akun** karena kode sudah terpakai. Maka
  setiap UM vendor yang diposting akan **mendebit PPN Masukan** (Law 27 hardcode + akun keliru → SPT/PPN masukan & neraca salah). `apply` juga hardcode `2-10100`
  (kebetulan benar = `AP_TRADE`; tetap pelanggaran Law 27) dan `1-10100` untuk kas bawaan.
- Penerapan ke bill (`/apply`): Dr Hutang / Cr Uang Muka, jurnal `DEPOSIT_APPLICATION` (source_id = id **deposit**, bukan id aplikasi), `vendor_deposit_applications`,
  cache bill lewat `segarkan_cache_hutang_tagihan` (benar). **Semua pemeriksaan (status, `remaining_amount` KOLOM CACHE, sisa bill) dilakukan SEBELUM lock & transaksi
  dan tidak diulang di dalamnya** → dua apply bersamaan lolos pemeriksaan yang sama = **over-apply** (Law 13 salah urutan; Law 1/16: guard membaca cache).
  Hal sama di `post` (status 'draft' dibaca sebelum lock → **dua jurnal untuk satu deposit**).
- Tak ada rute **lepas penerapan (unapply)**; `void` menolak deposit berpenerapan ("Reverse applications first") → deposit yang pernah diterapkan **tak bisa dibatalkan**.
- Bank mirror sudah ada (post/refund/void) — catatan di skill banksync 'footnote ³ belum diperbaiki' **BASI** (diperbaiki 14 Sep); perlu diperbarui.
- `GET /vendor-deposits/{id}`, `/by-vendor/{id}`, `/available/{id}` **diparkir** (`fitur_belum_tersedia` → 409 `FEATURE_NOT_AVAILABLE`); daftar & summary hidup.

## B2. TABEL CELAH (urut keparahan)

Legenda: **P0** = uang/akun salah atau rute rusak pasti; **P1** = integritas/proses (race, tautan, GRN); **P2** = paritas seragam; sumber = file:baris (≈).

| # | Modul | Celah | Bukti | Sev |
|---|---|---|---|---|
| G1 | PO | `/receive` SELALU 500 (`Decimal + float`); `receive_date` diabaikan | purchase_orders.py ≈1060; schemas ≈113 `quantity_received: float` | P0 |
| G2 | UM Pembelian | post/apply/refund ke akun **1-10800 = PPN Masukan**, bukan `AP_PREPAID` 1-10550; seed tak pernah membuat akun | vendor_deposits.py ≈437–450, 662–668; CoA 5 tenant | P0 |
| G3 | Recurring | `generate` dengan `post_immediately/auto_post` mengubah `bills.status='posted'` **tanpa jurnal AP** (komentar `TODO`) → hutang hantu (tak terlihat `compute_ap_outstanding`); `status` vs `status_v2` melayang; tanpa lock/idempotensi → ganda | recurring_bills.py ≈620–635 | P0 |
| G4 | UM Pembelian | `post`/`apply`/`refund`: pemeriksaan status & `remaining_amount` (cache) di LUAR lock/transaksi, tidak diulang → double-post / over-apply | vendor_deposits.py ≈428–470, 621–660 | P0 |
| G5 | PO→Bill | `quantity_billed` naik saat Bill **DRAFT** dibuat; hapus/void bill tak memulihkan → PO macet; PO tak dikunci (`FOR UPDATE`) → tagih ganda | purchase_orders.py ≈1280–1330; bills.py (tak ada rujukan PO) | P0/P1 |
| G6 | PO | uang `int()` (pemotongan, bukan HALF_UP), `unit_price: int` (sen hilang), `quantity_received: float` (Law 9/25). **Trigger DB `update_purchase_order_totals` (AFTER INSERT/UPDATE/DELETE pada `purchase_order_items`) MENIMPA `purchase_orders.subtotal/tax_amount/total_amount` = Σ baris (BIGINT)** → diskon header & pajak header yang dihitung Python di create HILANG dari angka tersimpan (angka respons create ≠ angka tersimpan); setiap `/receive` & `/to-bill` (UPDATE baris) memicunya lagi | purchase_orders.py ≈78–100, 565–590; schemas ≈36; fungsi DB (diukur) | P0/P1 |
| G7 | PO/GRN | Tak ada GRN (dokumen/nomor/gudang/stok/jurnal/void per penerimaan); tak ada GRNI; tak ada 3-way match; `/to-bill` boleh dari 'sent' | B1(a) | P1 |
| G8 | PO | `vendor_id` PO & `purchase_order_id` UM **tak divalidasi tenant** (Law 24); `po_number` via fungsi DB tanpa lock/idempotensi | purchase_orders.py ≈552; vendor_deposits.py ≈276 | P1 |
| G9 | PO/UM | Tanpa idempotensi (`X-Idempotency-Key`) di create/send/receive/to-bill; UM create/post/apply/refund tak bertransaksi tunggal (nomor + insert terpisah) | — | P1 |
| G10 | UM Pembelian | tak ada **unapply**; deposit berpenerapan tak bisa di-void; GET detail/by-vendor/available diparkir (FE tak bisa menampilkan) | vendor_deposits.py ≈203, 1174, 1235 | P1 |
| G11 | UM Pembelian | summary dari fungsi DB atas **kolom cache** (`applied_amount`, `remaining_amount`) bertipe BIGINT (sen terpotong); Law 1/16 | `get_vendor_deposit_summary` | P1 |
| G12 | PO | tanggal bisnis/periode: `po_date`/`receive_date` tak lewat `tanggal_dokumen`/cek periode; `receive` tak ada pagar periode (stok/jurnal nanti wajib) | — | P1 |
| G13 | PO | **Paritas seragam nol**: tanpa `aksi` (pill), `keadaan` (jatuh tempo kirim `expected_date`: telat/hari_ini/akan/selesai/tanpa), `position_text`, `documents[]`/bundel `/purchase-orders/{id}/documents`, `with_totals`, `?search=` di summary, `sum_*` | purchase_orders.py list ≈120–262, summary ≈346 | P2 |
| G14 | PO | tiebreak (`ORDER BY created_at` tanpa `, id`), paginasi hanya `skip/limit` (tak ada `page/per_page/offset`), `has_more` ada | purchase_orders.py ≈210 | P2 |
| G15 | PO/UM/Vendor/Recurring | Teks error **Inggris** ("Purchase order not found", "Vendor deposit not found", "Failed to …", "Cannot …") — FE mencocokkan teks; kontrak kita Indonesia | seluruh router | P2 |
| G16 | UM Pembelian | Paritas seragam nol: tanpa `aksi` (belum_diterapkan), `keadaan`, `position_text`, `documents`, `with_totals`, `?search=`, `aksi` summary; `ORDER BY deposit_date DESC` tanpa tiebreak; status 'partial' vs `sisa` | list ≈115–183 | P2 |
| G17 | PO | Kode order generik (V359) tidak terpasang di PO; nomor `PO-` via fungsi DB (bukan `kode_order`) | — | P2 |
| G18 | Vendor | `ORDER BY {sort_field}` tanpa `, id`; tanpa `aksi/with_totals/documents` (master data: tak wajib); `ap_balance` sudah dari jurnal (OK). **Bukan celah uang** | vendors.py ≈455 | P2 |
| G19 | Suppliers | router `/api/suppliers` = autocomplete di atas `vendors` (tabel `suppliers` kosong). Tak perlu seragam; dokumentasikan 'bukan master' + jadwalkan pensiun setelah ChatPanel pindah ke `/api/vendors/autocomplete` | suppliers.py | info |
| G20 | UM/Bill | Law 27: `2-10100`, `1-10100` hardcode (juga di `get_bill_remaining_from_journal`) → pakai `AP_TRADE`/`CASH_*` role | vendor_deposits.py ≈60–75 | P1 |

Yang SUDAH baik (tak perlu disentuh): `vendors` (master hidup, `search_text`, `ap_balance` dari jurnal, opening-balance beridempotensi + advisory lock, merge), void UM
(lock + cek ulang di dalam + reversal + cermin bank + tolak void bila terekonsiliasi), bank mirror UM, rumus Bill dari PO (`BillCalculator` HALF_UP, sama dengan bill manual).

## B3. Rancangan GRN (Penerimaan Barang) — singkat

Pola rujukan: NetSuite Item Receipt / SAP GR; padanan sisi jual: **SJ-SO R1** (`sj_so.py`, Barang Terkirim 1-10660, flag `pengiriman_stok_sebelum_faktur`). Prinsip sama:
SATU penentu `rencana` (pratinjau == tulis), satu transaksi milik pemanggil, tenant eksplisit, Decimal 2dp HALF_UP, lock per PO + `STOCK:{barang}:{gudang}`, idempotensi header.

**Data (migrasi; nomor diminta ke MASTER):** `goods_receipts` (id, tenant_id, grn_number `GRN-YYMM-NNNN`, po_id NULL-able untuk penerimaan tanpa PO, vendor_id, warehouse_id, receipt_date,
status posted|voided, journal_id, notes, voided_*), `goods_receipt_items` (grn_id, po_item_id, product_id, quantity, unit_cost SNAPSHOT [harga PO ternet diskon], total_cost,
batch/expiry bila dilacak), tautan faktur `grn_bill_alokasi` (INSERT-only, hanya `voided_*`; analog `sj_faktur_alokasi`), `bill_items.purchase_order_item_id` + `goods_receipt_item_id`
(NULL-able). Role baru **`GOODS_RECEIVED_NOT_INVOICED`** (liabilitas lancar, 2-xxxxx 'Barang Diterima Belum Ditagih'), disemai per tenant.

**Alur (flag tenant `penerimaan_sebelum_faktur`, bawaan MATI = perilaku sekarang tidak berubah):**
1. `POST /purchase-orders/{id}/goods-receipts` (+`/preview`): per baris PO 'perlu terima' (stok atau `perlu_kirim`), qty ≤ sisa (qty − diterima aktif), gudang (`warehouse_id` → PO → bawaan).
   - **Non-stok**: dokumen saja (nol jurnal/kartu) — sama dengan SJ non-stok.
   - **Ber-stok** (aturan buku baru, **izin LANGSUNG pemilik**): kartu masuk `record_inventory_inbound(source_type='GRN')` (WAC naik di sini), jurnal `GOODS_RECEIPT`: **Dr Persediaan (INVENTORY_MERCHANDISE) / Cr GRNI** senilai qty × harga PO ternet (snapshot).
2. Bill dari PO / bill manual bertaut baris GRN: baris yang ber-GRN memposting **Dr GRNI / Cr Hutang (AP_TRADE)** senilai snapshot GRN (clearing tepat) dan **tidak lagi** memanggil `record_inventory_inbound` (hindari stok ganda); selisih harga (harga bill − snapshot) → akun **Selisih Harga Pembelian** (PPV; peran baru `PURCHASE_PRICE_VARIANCE`, atau ke Persediaan bila stok masih ada — putusan akuntansi, izin pemilik).
   Baris tanpa GRN (flag mati / tanpa PO) = alur lama (stok & hutang di faktur).
3. **3-way match** di `rencana` bill: qty faktur ≤ qty diterima − tertagih (blok `BILL_MELEBIHI_PENERIMAAN`, bisa dilonggarkan flag toleransi), selisih harga vs PO dilaporkan (peringatan, blok di atas toleransi %), `quantity_billed` PO diturunkan dari faktur TERBIT (bukan draf) — memperbaiki G5.
4. **Void GRN**: hanya bila belum tertagih; pembalik jurnal (`GOODS_RECEIPT_REVERSAL`, `reversed_by_id`, Law 2/26) + `record_inventory_reversal`. Void bill melepas alokasi + membalik reklas (GRN kembali 'belum ditagih').
5. Status PO diturunkan dari GRN aktif & faktur terbit (`diterima_sebagian/diterima`, `ditagih_sebagian/ditagih`), bukan dari penghitung yang ditulis tangan; laporan **"Diterima belum ditagih"** (`/goods-receipts/pending-invoice`) = padanan `delivery-notes/pending-invoice`, saldo GRNI dari jurnal vs Σ snapshot (Check harian).
6. Seragam: `aksi` PO (`perlu_dikirim_ke_vendor`/`menunggu_barang`/`telat_terima`/`perlu_ditagih_vendor`), `keadaan` per `expected_date`, `position_text`, `documents[]` (PO · GRN · Bill · Bayar), bundel `/purchase-orders/{id}/documents`, `with_totals`, pagination/tiebreak, idempotensi, teks Indonesia — pola Penjualan.

**Law yang disentuh:** 1/16/29 (angka dari jurnal; `amount_received/billed` cache → turunan), 2/26 (void = pembalik), 5 (periode), 13 (lock PO+stok), 14 (idempotensi), 19/20 (DRAFT→POSTED), 24 (tenant+validasi vendor), 25 (Decimal), 27 (role, bukan kode), Inventory Rule 1/3/9 (dual ledger, WAC hanya di masuk, helper kanonik).

## B4. Urutan kerja yang diusulkan (setiap unit: ukur → rancangan → izin → bangun)

| Unit | Isi | Sifat | Izin |
|---|---|---|---|
| P-U1 | Perbaikan P0 tanpa aturan buku baru: G1 (/receive Decimal + `receive_date`), G2/G20 (UM → `AP_PREPAID`/`AP_TRADE` via role), G4 (cek ulang di dalam lock; guard sisa dari jurnal), G5 (qty tertagih dari faktur terbit + FOR UPDATE), G3 (generate tidak boleh 'posted' tanpa jurnal: panggil posting bill kanonik atau tolak `post_immediately`) | perbaikan bug, data 0 baris | izin merge+restart |
| P-U2 | PO seragam (G13/G14/G15/G17 + G6 Decimal 2dp + G8 validasi tenant + G9 idempotensi + G12 tanggal/periode) | aditif + Decimal; `unit_price` jadi 2dp (kompat. int) | izin merge+restart; uang → ironlaws |
| P-U3 | UM Pembelian seragam + unapply + unpark GET + summary jurnal (G10/G11/G16) | aditif + rute baru | izin; aturan buku: unapply = pembalik |
| P-U4 | GRN + GRNI + 3-way match (B3) di balik flag `penerimaan_sebelum_faktur` | **aturan buku baru + migrasi** | izin LANGSUNG pemilik per butir (akun GRNI/PPV, WAC di penerimaan) |
| P-U5 | Vendor/Suppliers: tiebreak, dokumentasi 'bukan master', pensiun `/suppliers` | kecil | izin restart |

Catatan koordinasi: `bills.py`/`bills_service.py`/`bill_payments.py` milik BACKEND — P-U1 (G5) dan P-U4 menyentuh bill posting; harus dijadwalkan bersama BACKEND (satu pemilik per berkas per jendela).
