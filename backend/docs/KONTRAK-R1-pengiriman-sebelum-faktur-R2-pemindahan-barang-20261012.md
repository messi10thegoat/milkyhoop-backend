# KONTRAK RANCANGAN — R1 Pengiriman sebelum Faktur · R2 Pemindahan Barang antargudang

Status: **RANCANGAN, belum ada kode, belum ada data disentuh** · 12 Okt 2026 · BACKEND2 untuk MASTER
Dasar: skill `milkyhoop-ironlaws` (Law 1/2/5/6/8/13/14/16/18/19/20/22/23/24/26/27/31/33/34) dan `milkyhoop-inventory`
(Rule 1 dual-ledger, Rule 3 WAC, Rule 7 stok negatif, Rule 8 append-only, Rule 9 pembalik, Rule 11 cascade audit,
Inv-1/2/3/4/5), `milkyhoop-pengiriman` (state machine SJ, 3-event PSAK 72), putusan pemilik 12 Okt: rujukan pola =
NetSuite / SAP / Accurate, tiap butir ditabelkan.

## 0. Kelas bukti (Law 34)

| Tag | Arti |
|---|---|
| `[UKUR]` | dibaca dari DB prod 12 Okt, transaksi READ ONLY |
| `[KODE]` | dibaca dari kode master `6bbb1e8f` |
| `[WEB]` | dari halaman resmi/arsip yang saya buka hari ini (URL di §9) |
| `[BRIEF]` | dari brief MASTER; BELUM saya verifikasi ke sumber vendor |
| `[UMUM]` | pengetahuan umum ERP; BELUM diverifikasi sumber, jangan dipakai sebagai dasar tanpa cek |

Catatan jujur: pencarian web saya **tidak** menemukan dokumen resmi Accurate untuk jurnal "Barang Terkirim" maupun
"Pemindahan Barang", dan tidak menemukan sumber resmi untuk jurnal Item Fulfillment NetSuite. Kolom Accurate di tabel
padanan karenanya `[BRIEF]` atau `[UMUM]`. Verifikasi final = help center Accurate atau trial akun Accurate milik pemilik.

---

# BAGIAN R1 — PENGIRIMAN SEBELUM FAKTUR

## R1.1 Keadaan sekarang (diukur)

| # | Fakta | Bukti |
|---|---|---|
| 1 | `invoice_fulfillments.invoice_id` **NOT NULL** FK `sales_invoices`; `invoice_fulfillment_items.invoice_item_id` **NOT NULL** | `[UKUR]` information_schema |
| 2 | Satu SJ selalu milik tepat satu faktur. Faktur boleh punya banyak SJ (kiriman parsial); SJ tak bisa mencakup dua faktur | `[UKUR]` skema + `[KODE]` `_execute_fulfillment` |
| 3 | Kirim dari SO = orkestrasi di atas faktur TERBIT: "baris SO → baris faktur terbit yang pengirimannya masih terbuka, tertua-dulu, satu SJ per faktur"; jalur tunggal inti = `sales_invoices._execute_fulfillment` | `[KODE]` docstring `so_pengiriman.py` |
| 4 | Jurnal saat SJ: (a) HPP `INVOICE_FULFILLMENT`: Dr `COGS_SALES` (5-10100 HPP) / Cr `INVENTORY_MERCHANDISE` (1-10600), nilai = qty × WAC (`get_weighted_average_cost(tenant, produk)`, tanpa gudang); (b) pengakuan pendapatan `INVOICE_REVENUE`: Dr `REVENUE_DEFERRED` (2-10750 Pendapatan Diterima Dimuka) / Cr `REVENUE_SALES_GOODS` (4-10100) | `[KODE]` sales_invoices.py:2045-2160, role kaos `[UKUR]` |
| 5 | Jurnal saat faktur terbit (billing): Dr Piutang / Cr 2-10750 / Cr PPN (kontrak liabilitas, bukan pendapatan) — hanya akun "Dimuka" yang menjembatani faktur→kirim; TIDAK ada akun Barang Terkirim | `[KODE]` + skill pengiriman |
| 6 | Void SJ **tidak ada endpoint sendiri**; hanya kaskade dari void faktur (pembalik INVOICE_REVENUE + INVOICE_FULFILLMENT_REVERSAL + `record_inventory_reversal`) | `[KODE]` sales_invoices.py ~4455-4600, skill |
| 7 | Barang non_inventory / `track_inventory=false`: tak ada baris ledger, tak ada HPP; pendapatan tetap diakui lewat `INVOICE_REVENUE` saat SJ bila `perlu_kirim` | `[KODE]` ln 756-769, 2893-2940 |
| 8 | Data: 9 SJ (kaos 7 posted + 2 voided, grapgrap 2 posted), **0 SJ tanpa faktur**. Jurnal: kaos HPP 7 (Rp8.841.921), grapgrap 0 HPP (0 produk ber-stok; 105 produk semua non-stok) tetapi 51 `INVOICE_REVENUE` | `[UKUR]` |
| 9 | Trigger `trg_so_shipped_recompute` (INSERT/UPDATE/DELETE pada `invoice_fulfillments` dan `_items`) menghitung `v_so` dari **`sales_invoices.sales_order_id` via `invoice_id`**; `recompute_so_shipped()` menjumlah SJ non-void lewat join `sales_invoices`. SJ tanpa faktur = `v_so` NULL = **SO tak pernah ter-update** | `[UKUR]` pg_get_functiondef |
| 10 | `compute_so_status`: shipped ≥ ordered → `shipped`; ship>0 → `partial_shipped`; invoiced ≥ ordered → `invoiced`; dst. (shipped menang atas invoiced) | `[UKUR]` |
| 11 | Akun "Barang Terkirim" / "transit" / "terkirim" di CoA: **tidak ada** di tenant mana pun. Peran akun: katalog 44 role, `account_roles(tenant_id, role_key, account_id, is_interim)`; CoA baru lewat `seed_default_coa()`, tenant lama lewat migrasi backfill (pola V151/V152) | `[UKUR]` |
| 12 | 36 berkas kode menyebut `invoice_fulfillments` (terbanyak: sales_invoices 14, deliveries 10, delivery_lampiran 6, so_riwayat 3, so_kirim 3, pengiriman_serah 2, dokumen 2) | `[KODE]` grep |

Kesimpulan ukur: yang diminta **bukan** menambah satu endpoint, melainkan melepas asumsi "SJ ⇒ tepat satu faktur" yang
tertanam di skema, trigger SO, kaskade void, dan ±36 pembaca.

## R1.2 Tabel padanan per butir

| Butir | NetSuite | SAP | Accurate | MilkyHoop sekarang | Usulan |
|---|---|---|---|---|---|
| Dokumen kirim sebelum faktur | Item Fulfillment dari Sales Order; Invoice terpisah (boleh sebelum/sesudah) `[UMUM]` | Outbound Delivery lalu Billing `[UMUM]` | Pengiriman Pesanan lalu Faktur Penjualan `[BRIEF]`; arsip UPJ menggambarkan urutan dokumen quotation→order→packing/delivery→faktur `[WEB]` | SJ hanya setelah faktur terbit | SJ dari SO tanpa faktur (flag) |
| Jurnal saat kirim | Dr COGS / Cr Inventory saat fulfillment (perpetual) `[UMUM]`; Charted guide: fulfillment mem-posting ke GL saat pengiriman dikonfirmasi `[WEB]`; forum "Why Item Fulfilment has no GL impact?" menunjukkan **bergantung preferensi** `[WEB, tak dibuka]` | Post Goods Issue: Dr COGS / Cr Stok `[UMUM]` | Dr Barang Terkirim / Cr Persediaan `[BRIEF]` | Dr HPP / Cr Persediaan saat SJ (setelah faktur) | Dr Barang Terkirim / Cr Persediaan, nilai WAC snapshot |
| Jurnal saat faktur | AR / Revenue (+ COGS bila belum) | Billing: AR / Revenue | Dr HPP / Cr Barang Terkirim + pendapatan `[BRIEF]` | Billing ke "Dimuka"; pendapatan+HPP di SJ | Reklas Dr HPP / Cr Barang Terkirim per alokasi + INVOICE_REVENUE untuk qty terkirim |
| Faktur tanpa SJ | boleh | boleh | boleh `[BRIEF]` | jalur sekarang | tetap, tak diubah |
| Void kirim mandiri | Void/delete Item Fulfillment selama belum diinvoice `[UMUM]` | Reverse Goods Issue (WE?) sebelum billing `[UMUM]` | Hapus/batal pengiriman `[UMUM]` | tak ada | `POST /deliveries/{id}/void`, hanya `belum_difakturkan` |
| Satu faktur dari banyak SJ / satu SJ ke banyak faktur | ya (Bill/Invoice dari fulfillment terpilih) `[UMUM]` | billing due list per delivery `[UMUM]` | ya `[BRIEF]` | tak bisa | tabel alokasi |
| Laporan | "Fulfilled not invoiced" / Shipped-not-billed `[UMUM]` | billing due list `[UMUM]` | Daftar pengiriman belum difakturkan `[UMUM]` | tak ada | laporan + check harian |
| Barang jasa/non-stok | tanpa efek stok | tanpa efek stok | tanpa efek stok | tanpa jurnal HPP | tanpa jurnal sama sekali di SJ |

**Selisih penting yang harus dicatat (Law 34):** NetSuite dan SAP, sejauh pengetahuan umum saya, **mengakui HPP saat
kirim** tanpa akun penampung; hanya pola Accurate (sesuai brief) memakai Barang Terkirim. Dua-duanya sah. Opsi
alternatif ada di R1.9 (HPP saat kirim). Rekomendasi tetap Barang Terkirim karena (1) putusan pemilik menyebut Accurate
sebagai rujukan, (2) HPP dan pendapatan jatuh di periode yang sama saat faktur terbit bulan berikutnya, (3) void SJ
sebelum faktur tidak menyentuh akun beban.

## R1.3 Model data

Pilihan A (**direkomendasikan**): perluas `invoice_fulfillments` — SJ tetap SATU konsep (nomor SJ-YYMM-seq, serah,
lampiran, PDF, daftar pengiriman semua menempel di sini).

| Perubahan | Isi | Alasan / jebakan |
|---|---|---|
| `invoice_fulfillments.invoice_id` → NULLABLE | `+ sales_order_id uuid`, `+ asal text CHECK IN ('faktur','so')` | CHECK `(asal='faktur' AND invoice_id IS NOT NULL) OR (asal='so' AND invoice_id IS NULL AND sales_order_id IS NOT NULL)`. SJ lama otomatis `asal='faktur'` (DEFAULT + backfill). |
| `invoice_fulfillment_items.invoice_item_id` → NULLABLE | `+ sales_order_item_id uuid`, CHECK serupa | Item SJ-so tanpa baris faktur. |
| Tabel baru `sj_faktur_alokasi` | `(id, tenant_id, fulfillment_item_id, invoice_item_id, quantity, unit_cost, total_cost, cogs_journal_id, revenue_journal_id, created_at, voided_at)` | Satu baris kirim bisa ditagih ke banyak faktur dan sebaliknya; reklas HPP dihitung dari SNAPSHOT `unit_cost` SJ, bukan WAC hari faktur. |
| Predikat DB | trigger/CHECK: Σ `alokasi.quantity` per `fulfillment_item_id` ≤ `quantity` baris SJ; Σ per `invoice_item_id` ≤ qty faktur | Invarian atas baris wajib di DB (Law 13 catatan "di mana menegakkan invariant"). |
| Kolom turunan | `invoice_fulfillments.status`: tambah nilai logis `faktur_status` ('belum','sebagian','penuh') bila perlu dihitung, **bukan** disimpan sebagai fakta buku (Law 16) | Cukup view/hitung dari alokasi. |

Pilihan B (tidak disarankan): tabel baru `surat_jalan_so`. Blast radius baca lebih kecil, tetapi menggandakan SJ
(daftar pengiriman, PDF, serah, lampiran `delivery_lampiran` yang induknya `invoice_fulfillments`, nomor, kaskade) dan
membelah konsep yang di tiga ERP rujukan satu rekaman. Kelemahan A = ±36 pembaca; mitigasi = tes inventaris
"setiap SELECT yang JOIN `sales_invoices` dari `invoice_fulfillments` harus LEFT JOIN atau menyaring asal".

## R1.4 Aturan buku (INI aturan buku baru — izin LANGSUNG pemilik)

Akun baru: peran **`GOODS_DELIVERED_NOT_INVOICED`** ("Barang Terkirim", ASSET, leaf). Kode akun diusulkan **1-10660**
(`[UKUR]` kaos: 1-10600, 1-10650, 1-10700 terpakai; 1-10660 kosong di kaos, **belum diukur di 5 tenant lain**).
Resolusi lewat `resolve_account_id_by_role` (Law 27), tak ada hardcode.

| Peristiwa | Jurnal | source_type | Ledger |
|---|---|---|---|
| SJ-so diposting (barang ber-stok) | Dr `GOODS_DELIVERED_NOT_INVOICED` / Cr `INVENTORY_MERCHANDISE`, nilai = Σ qty × WAC snapshot | `DELIVERY_NOTE` (baru) | `inventory_ledger` keluar, source_type `DELIVERY_NOTE`, `journal_id` terisi |
| Faktur diterbitkan memuat baris yang terkirim | (i) billing seperti sekarang: Dr Piutang / Cr 2-10750 / Cr PPN; (ii) per alokasi: Dr `COGS_SALES` / Cr `GOODS_DELIVERED_NOT_INVOICED` pada `unit_cost` snapshot; (iii) `INVOICE_REVENUE` Dr 2-10750 / Cr `REVENUE_SALES_GOODS` untuk qty teralokasi | `DELIVERY_INVOICED` (baru), `INVOICE_REVENUE` (ada) | tidak ada ledger baru (stok sudah keluar saat SJ) |
| Void SJ-so (belum difakturkan sama sekali) | pembalik penuh jurnal SJ (DRAFT→POSTED, `reversed_by_id`, Law 2/26) | `DELIVERY_NOTE_REVERSAL` (baru) | `record_inventory_reversal(source_type='DELIVERY_NOTE')` → `DELIVERY_NOTE_VOID` (Rule 9) |
| Void faktur yang punya alokasi | pembalik alokasi: Dr `GOODS_DELIVERED_NOT_INVOICED` / Cr `COGS_SALES` + pembalik INVOICE_REVENUE; **SJ tidak ikut void**, kembali "belum difakturkan" | `DELIVERY_INVOICED_REVERSAL` (baru) | tak ada |
| Barang non_inventory | **tanpa jurnal dan tanpa ledger** di SJ; pendapatan diakui di faktur untuk qty yang tercatat terkirim (tak ada nilai di Barang Terkirim) | — | — |

Konsekuensi yang wajib diputuskan pemilik (bukan teknis):
1. **Saat pengakuan pendapatan untuk alur SJ→faktur = saat faktur terbit** (qty yang sudah terkirim langsung diakui),
   bukan saat kirim. Alasan: sebelum faktur tidak ada piutang. Sejalan NetSuite/SAP (pendapatan di billing) dan
   Accurate `[BRIEF]`; berbeda dari tafsir PSAK 72 "kendali berpindah saat kirim" (akan butuh aset kontrak/piutang
   belum ditagih). Ini kebijakan, bukan teknis.
2. HPP dicocokkan dengan pendapatan di periode faktur (bukan periode kirim). Saldo Barang Terkirim muncul di neraca
   akhir bulan (aset lancar). Laporan laba rugi periode kirim tidak memuat HPP SJ-so.
3. SJ-so yang bulannya sudah ditutup: void SJ ditolak (Law 5); koreksi lewat faktur/NK.

Law yang dipenuhi: 1/16 (semua saldo dari jurnal, Barang Terkirim diturunkan), 2/26 (pembalik, bukan edit), 4 (jurnal
seimbang; verifikasi Σ baris = header sendiri, bukan hanya CHECK header — celah terukur 6 Agt), 5 (periode), 6 (4
source_type jurnal baru — DELIVERY_NOTE, DELIVERY_NOTE_REVERSAL, DELIVERY_INVOICED, DELIVERY_INVOICED_REVERSAL — WAJIB didaftarkan di `journal_source_types`, FK-enforced; lupa = 500 senyap), 13 (lock
`DELIVERY_NOTE:{tenant}:{so}` + `INVOICE_FULFILL:{invoice_id}` yang sama dengan jalur lama + lock `(item,warehouse)`
`STOCK:{product}:{warehouse}` untuk anti-oversell lintas dokumen, ironlaws v3.8), 14 (idempotency key SJ-so + hash,
`MANUAL_FULFILL:` prefix), 18 (akun baru = struktur CoA, tak boleh ubah tipe setelah ada jurnal), 19 (nilai SJ
dibekukan setelah POSTED, hanya serah/lampiran boleh diubah), 20 (DRAFT→POSTED), 23, 24, 27, 31 (7/7), 33/34.

## R1.5 Komponen yang berubah

| Komponen | Perubahan | Risiko |
|---|---|---|
| `trg_so_shipped_from_fulfillment` + `_item` | `v_so = COALESCE(f.sales_order_id, si.sales_order_id)` | Mengubah trigger yang menyentuh SO setiap kirim. Uji: hitung-ulang semua SO prod sebelum/sesudah (harus identik), 300 SO. |
| `recompute_so_shipped()` | jumlah SJ non-void dengan `f.sales_order_id = so` **atau** `si.sales_order_id = so`, dihitung per `f.id` sekali (hindari hitung ganda) | salah join = shipped_qty ganda → status SO salah. |
| `compute_so_status` / `terapkan_selesai_so` (V315) | belum diukur isi `terapkan_selesai_so`; harus dipastikan "selesai" menuntut shipped≥ordered DAN invoiced≥ordered DAN lunas, tak menganggap shipped-sebelum-invoiced selesai | `[BELUM-UKUR]` wajib diukur sebelum kode |
| Sisa kirim / sisa tagih SO | sisa kirim = ordered − Σ SJ (so+faktur) ; sisa tagih = ordered − `quantity_invoiced`; **qty SJ-so yang belum difakturkan = terkirim-belum-ditagih** (angka baru di kartu SO) | UI/FE: kartu SO harus menampilkan tiga angka |
| Faktur dari SO (draft→terbit) | bila ada SJ-so belum difakturkan untuk SO itu: **alokasi otomatis tertua-dulu** per `sales_order_item_id` (mirip `so_kirim.SQL_FAKTUR_TERTUNDA_PER_SO`); faktur boleh meminta subset SJ | jalur auto-fulfill saat posting faktur harus MELEWATI baris yang sudah terkirim (jangan kirim dua kali): `fulfilled_qty` faktur dinaikkan lewat alokasi |
| `pengiriman_serah` (V407/V408) | **tak berubah**: tabel `pengiriman_serah/resi` berkunci `fulfillment_id` (PK `invoice_fulfillments`); lampiran `delivery_lampiran` sama. Satu-satunya kait: `PUT /deliveries/{id}/serah` tak boleh mengasumsikan faktur (cek pembaca `si.*`) | rendah |
| Kaskade void faktur | cabang baru: SJ `asal='so'` yang punya alokasi → lepas alokasi (pembalik DELIVERY_INVOICED), bukan void SJ | jalur void = area rawan (Rule 11 audit) — tes tiga-lapis |
| `_execute_fulfillment` | tetap untuk `asal='faktur'`; inti baru `_execute_delivery_so` memakai helper `inventory_helpers` yang sama | jangan menyalin 400 baris; ekstrak pemakaian bersama |
| Check/invarian lama | **Inv-2/Inv-4 menyaring daftar source_type literal** (pelajaran 12 Sep: nama berpindah, invarian tak ikut → hijau hampa). `DELIVERY_NOTE`/`DELIVERY_INVOICED` WAJIB ditambahkan ke Inv-2, Inv-9, Check 10, Check 21 (`STOK_WAJIB_JURNAL`), `kosakata_ledger.py` | tertinggal = penjaga bisu (Law 33) |
| Check 15 / Inv-3 (GL persediaan vs Σ on_hand×WAC) | tetap konsisten: ledger keluar dan Cr Persediaan terjadi sama-sama di SJ | — |

## R1.6 Penjaga & laporan (wajib)

1. **Check harian baru (HIGH):** saldo akun `GOODS_DELIVERED_NOT_INVOICED` (diturunkan dari `journal_lines` +
   `is_effective_journal`, role-resolved) == Σ `unit_cost × (qty_SJ − Σ qty_alokasi aktif)` atas SJ-so POSTED non-void.
   Fail-CLOSED bila role tak terpetakan atau tenant context hilang (Rule 11 banksync / V188), toleransi berprinsip
   2dp seperti Check 15. Dibuktikan DUA sisi (Law 15/33): sisipan SJ tanpa jurnal → MERAH; data sehat → HIJAU.
2. **Check 21:** `DELIVERY_NOTE` masuk `STOK_WAJIB_JURNAL` (kartu keluar tanpa jurnal = merah).
3. **Invarian predikat DB** alokasi ≤ qty (lihat R1.3).
4. **Laporan "Terkirim belum difakturkan"**: per SJ/SO/pelanggan, qty, nilai (snapshot), umur hari, total = saldo akun
   (sub-ledger = GL). Endpoint baca saja; format ekspor mengikuti `bulk_export`.
5. **Penanda ramp**: tenant tanpa flag tidak punya baris `asal='so'` → semua penjaga baru 0 baris = hijau hampa;
   uji kontrol merah wajib di kaos sebelum flag dinyalakan.

## R1.7 Kompatibilitas data lama

| Hal | Perlakuan |
|---|---|
| 9 SJ yang ada | tetap sah, `asal='faktur'` lewat DEFAULT; nol jurnal diubah; nol baris ledger diubah |
| Trigger SO | hasil recompute SO prod identik sebelum/sesudah (diverifikasi massal di scratch DB hasil restore dump terbaru, pola harness nyata) |
| Faktur lama tanpa SJ | tetap |
| `trg_so_shipped_recompute` dilarang UPDATE `invoice_fulfillments` | SJ-so **tak pernah** di-UPDATE untuk "menautkan faktur" — tautan hidup di `sj_faktur_alokasi` (INSERT only). Ini juga alasan Pilihan A aman. |
| pengiriman_serah, lampiran SJ | tak berubah |
| grapgrap | 0 produk ber-stok → SJ-so hanya dokumen (qty/serah), nol jurnal, nol ledger |

## R1.8 Migrasi, flag, urutan rilis

Flag: `pengiriman_sebelum_faktur` (tabel `tenant_features(tenant_id, feature, enabled)`), **kaos saja** dulu.

| Langkah | Isi | Izin pemilik LANGSUNG? |
|---|---|---|
| 0 | Ukur lanjut: isi `terapkan_selesai_so`, kode akun 1-10660 kosong di 6 tenant, daftar pembaca `invoice_fulfillments` yang JOIN faktur (36 berkas), `journal_source_types` kosakata | tidak (baca saja) |
| 1 | Migrasi A (tanpa perubahan perilaku): akun + peran `GOODS_DELIVERED_NOT_INVOICED` (patch `seed_default_coa()` + backfill 6 tenant + `account_roles`), 4 source_type jurnal terdaftar (+ kosakata ledger DELIVERY_NOTE/_VOID), kolom nullable + CHECK, tabel alokasi, trigger SO baru, penjaga predikat | **YA** (akun CoA baru di semua tenant; migrasi prod; merge+restart) |
| 2 | Kode jalur SJ-so + void SJ + alokasi di faktur, di balik flag, `/api/deliveries` tetap | **YA** (merge+restart; aturan buku baru) |
| 3 | Check harian + laporan + Check 21/Inv-2/Inv-9 diperluas, uji merah/hijau | YA (merge) |
| 4 | E2E nyata kaos (SO → SJ → faktur parsial → faktur sisa → void faktur → void SJ), buku dibandingkan ke ground truth | YA (tulis uji ke kaos: izin tetap "TES E2E") |
| 5 | Nyalakan flag kaos | **YA** (bukan flag CW; flag buku) |
| 6 | grapgrap | **YA, terpisah**; nilai buku nol tapi alur kerja berubah |

## R1.9 Opsi yang ditolak / alternatif

| Opsi | Kelebihan | Alasan tidak dipilih |
|---|---|---|
| HPP saat kirim, tanpa Barang Terkirim (pola NetSuite/SAP `[UMUM]`) | satu jurnal, tak ada akun baru, tak ada alokasi nilai | HPP lebih awal dari pendapatan (beda periode), void SJ menyentuh beban; bila MASTER/pemilik memilih ini, R1 mengecil: cukup kolom nullable + trigger SO + jurnal HPP di SJ-so |
| Memperlakukan SJ-so sebagai "faktur draft otomatis" | tak ubah skema | faktur ≠ SJ; piutang/pajak/PPN timbul sebelum waktunya |
| UPDATE `invoice_id` pada SJ saat difakturkan | model sederhana | memicu `trg_so_shipped_recompute`, tak mendukung 1 SJ → N faktur |

---

# BAGIAN R2 — PEMINDAHAN BARANG ANTARGUDANG

## R2.1 Keadaan sekarang (diukur)

| # | Fakta | Bukti |
|---|---|---|
| 1 | Tabel `stock_transfers` (+ `stock_transfer_items`) ada, **kosong** di semua tenant; bentuk memadai: from/to gudang, status, shipped/received_date, `quantity_requested/shipped/received/variance`, `unit_cost`, `total_value`, `batch_id`, `serial_ids`, `cancel_*` | `[UKUR]` |
| 2 | Semua rute tulis ditahan 409 `STOCK_TRANSFER_NOT_AVAILABLE` (`_TAHAN`, d873b4a4, 23 Sep); baca terbuka | `[KODE]` |
| 3 | `ship_stock_transfer()`/`receive_stock_transfer()` adalah fungsi DB; semuanya, termasuk cancel di router (`~ln 614`), menulis `inventory_ledger(item_id, quantity_change, total_value, transaction_date)` — **kolom hantu**, tak ada di tabel (nyata: product_id, quantity_in/out, total_cost, movement_date) | `[KODE]` + `[UKUR]` kolom ledger |
| 4 | Satu-satunya relokasi nyata = 6 baris kaos 11 Sep (perbaikan warehouse_id NULL → WH-MAIN), source_type `RELOKASI_GUDANG`, movement `RELOKASI_KELUAR/MASUK`, neto nol, tanpa jurnal, dijaga lengan-2 Check 21 | `[UKUR]` laporan 12 Okt |
| 5 | Akun persediaan per **produk** (`products.inventory_account_id`; kaos 73/79 override, selainnya `INVENTORY_MERCHANDISE`), `warehouses` tak punya kolom akun; WAC tanpa gudang | `[UKUR]` |
| 6 | Gudang: kaos 1, grapgrap 2, tenant lain 1; grapgrap 0 produk ber-stok | `[UKUR]` |
| 7 | Fase lokasi SO (`fase_lokasi` ∈ {NULL, `dikirim_ke_lokasi`, `tersedia`} + `fase_gudang_id` + `fase_at`, constraint `chk_so_fase_lengkap`): label manual; **300 SO semuanya NULL** (belum dipakai). Kirim SO membaca fase `tersedia` sebagai pilihan gudang (urutan: body → fase → gudang SO → preferensi → default) | `[UKUR]` + `[KODE]` so_pengiriman `_gudang` |

## R2.2 Tabel padanan per butir

| Butir | NetSuite | SAP | Accurate | Usulan |
|---|---|---|---|---|
| Dokumen | Transfer Order (Inventory Transfer untuk satu-langkah) `[WEB]` | Stock Transport Order / transfer posting (MIGO 301/311 satu-langkah; 351+101 / 641 dua-langkah `[WEB]`) | Pemindahan Barang `[BRIEF]` | Dokumen `Pemindahan Barang` PB-YYMM-seq |
| Jurnal | Transfer Order **tidak** posting ke COGS; memindah nilai dari akun aset lokasi asal ke **Inventory in Transit** `[WEB: Oracle help]`; bila akun persediaan sama per lokasi, tak ada selisih bersih | Posting transfer plant→plant: stok transit tercatat di plant penerima sampai GR (MB5T) `[WEB]` | tidak ada jurnal bila satu akun persediaan `[BRIEF]`, **belum diverifikasi** | **Tanpa jurnal** (akun per produk, WAC tanpa gudang) |
| Langkah | dua langkah: Fulfillment lalu Receipt (in transit) `[WEB]` | 351 (keluar ke transit) + 101 (terima) atau 311 satu langkah `[WEB]`/`[UMUM]` | belum diketahui ada status perjalanan; Accurate punya artikel "persediaan barang dalam perjalanan" tapi untuk pembelian `[WEB]` | **Satu langkah dulu** (R2.4) |
| Nilai | ongkos item (opsi "Use Item Cost as Transfer Cost") `[WEB]` | nilai valuasi/harga standar | WAC `[UMUM]` | WAC snapshot, kedua kaki, WAC tak berubah |
| Selisih terima | variance → penyesuaian | selisih lewat dokumen terpisah | — | satu langkah: tak ada selisih; dua langkah (nanti): selisih = penyesuaian stok bernilai (jurnal Biaya Penyesuaian) |
| Batal | void/close | reverse movement (351→352) | hapus/batal | pembalik ledger kedua kaki (Rule 9) |

## R2.3 Model

Pakai ulang `stock_transfers` + `stock_transfer_items` (skema memadai; kosong → nol migrasi data). Yang dibangun ulang =
**logika tulis**: Python service di atas `inventory_helpers`, bukan fungsi DB.

| Aspek | Desain |
|---|---|
| Status (satu langkah) | `draft` → `completed` · `draft` → `cancelled` · `completed` → `voided` |
| Status (dua langkah, fase 2) | `draft` → `in_transit` → `received` · variance → penyesuaian |
| Ledger | 2 baris per item di transaksi yang SAMA: `RELOKASI_KELUAR` (gudang asal, `quantity_out`) dan `RELOKASI_MASUK` (gudang tujuan, `quantity_in`); `source_type='RELOKASI_GUDANG'` (pakai ulang agar lengan-2 Check 21 tetap berlaku), `source_id = stock_transfers.id`, `unit_cost`/`average_cost` = WAC snapshot, `journal_id` NULL **sengaja** |
| WAC | **tidak dihitung ulang** pada kaki masuk: ledger ditulis langsung dengan `average_cost` = WAC saat itu (Rule 3 invarian 1 "WAC hanya berubah pada INBOUND pembelian/adj+/produksi" diperluas: relokasi = bukan inbound ekonomis). Memanggil `record_inventory_inbound` akan menghitung ulang dengan pembulatan 2dp → drift sub-rupiah. **Perlu putusan: ini ketentuan Rule 3 baru.** |
| Stok | `warehouse_stock` hanya lewat trigger ledger (Rule 1, larangan UPDATE langsung); stok asal tak cukup → 409 (constraint `chk_ws_quantity` akan menolak; jangan andalkan, cek eksplisit lalu pesan jujur) |
| Lock | `STOCK_TRANSFER:{tenant}:{id}` + `STOCK:{product}:{warehouse}` untuk kedua gudang, urut deterministik (anti-deadlock), Law 13 + ironlaws v3.8 |
| Idempotency | key + hash (Law 14), nomor PB dari `journal_number_sequences`-style, bukan `MAX+1` |
| Barang | hanya `track_inventory=true`; non-stok ditolak 409 (tak ada stok untuk dipindah) |
| Batch/serial | belum diukur untuk kaos; tahap 1 tolak barang ber-batch/serial dengan 409 jelas, tahap 2 setelah diukur (`batch_id`/`serial_ids` ada di item) |
| Bertanggal | tanggal dokumen = tanggal bisnis tenant (`tanggal_dokumen`, t10); periode tertutup tak relevan (tanpa jurnal) tetapi `movement_date` ikut tanggal |
| Void | pembalik kedua kaki via `record_inventory_reversal` (`RELOKASI_GUDANG_VOID`); ditolak 409 bila stok tujuan sudah terpakai (qty tak cukup untuk dikembalikan) |
| Kolom hantu | `ship_stock_transfer`, `receive_stock_transfer` (dan fungsi cancel) **DROP** lewat migrasi, bukan dibiarkan (pelajaran Law 33 instance 7: jaring/penjaga rusak yang dibiarkan menyesatkan); cancel di router ditulis ulang. Tes baru: kolom yang disebut tiap INSERT ke `inventory_ledger` dicocokkan ke `information_schema` (pagar "kolom hantu" yang selama ini tak ada). |

## R2.4 Satu langkah atau dua langkah — rekomendasi

**Rekomendasi: satu langkah (kirim+terima atomik) sebagai versi pertama; dua langkah ditunda sampai ada kebutuhan nyata.**

Alasan, urut bobot:
1. **Jebakan penjaga (DINALAR dari definisi Check 9/15/Inv-3, belum diukur dengan data transit):** dua langkah *tanpa jurnal* menurunkan
   Σ on_hand di ledger (kaki keluar) selama transit sementara saldo GL `INVENTORY_MERCHANDISE` tetap → Check 15
   ("GL persediaan = Σ on_hand × WAC") dan Inv-3 menyala MERAH setiap ada barang di jalan. Menyelesaikannya butuh
   akun/peran baru "Persediaan dalam Perjalanan" + jurnal Dr/Cr per kirim dan terima (pola NetSuite
   Inventory in Transit `[WEB]`) — yaitu aturan buku baru yang tak perlu bila tak ada transit.
2. Selisih terima (rusak/hilang di jalan) adalah **kejadian bernilai** (beban/penyesuaian) → jurnal + izin pemilik.
3. Skala nyata: kaos 1 gudang, grapgrap 2 gudang tanpa produk ber-stok, 0 transfer pernah dibuat, tak ada UI.
4. Accurate (rujukan pemilik) tampak memakai satu dokumen, `[BRIEF]`, belum diverifikasi.

Bila nanti dua langkah: kaki masuk ditunda ke terima; transit = baris ledger keluar + peran
`INVENTORY_IN_TRANSIT` + jurnal Dr InTransit / Cr Persediaan saat kirim dan Dr Persediaan / Cr InTransit saat terima;
Check harian "saldo InTransit == Σ transfer in_transit" (analog R1.6). **Aturan buku baru → izin langsung pemilik.**

## R2.5 Penjaga

| Penjaga | Isi |
|---|---|
| Lengan-2 Check 21 (ada) | sekarang berpasangan per produk per hari; **diperkuat** jadi per `source_id` (transfer): Σ qty in = Σ qty out dan Σ nilai in = Σ nilai out per transfer. 6 baris 11 Sep (source_id belum diukur terisi atau tidak) tetap lewat jalur per-hari |
| Inv-1 | `warehouse_stock` = GREATEST(0, Σ ledger) tetap berlaku per gudang (kini kedua gudang bergerak) |
| Check baru ringan | transfer `completed` tanpa tepat 2n baris ledger = MERAH; ledger RELOKASI tanpa `source_id` transfer (selain 6 baris 11 Sep) = MERAH |
| Uji kolom | tes statis kolom INSERT ledger vs `information_schema` |

## R2.6 Pertemuan dengan fase lokasi SO

Dua konsep yang ada: fase lokasi SO = **label** manual ("Dikirim ke lokasi X" / "Tersedia di X"); stok fisik hanya
bergerak lewat dokumen stok. Usul pertemuan, minimal dan reversibel:

| Peristiwa | Efek ke SO |
|---|---|
| `stock_transfers.sales_order_id` (opsional, informasi) | PB boleh ditautkan ke satu SO |
| PB satu langkah `completed` ber-`sales_order_id` | set fase `tersedia` di gudang tujuan lewat `so_fase_lokasi.atur` (audit; aktor = pembuat PB), hanya bila SO belum tertutup dan fase belum diisi manual atau berbeda → fase lama dicatat di audit |
| (fase 2) PB `in_transit` | fase `dikirim_ke_lokasi` + gudang tujuan; `received` → `tersedia` |
| PB di-void | fase dikembalikan ke nilai sebelumnya dari audit, atau dikosongkan bila tak ada |
| Kirim SO 409 stok kurang di gudang fase | pesan menyebut "buat Pemindahan Barang" (catatan lama di KONTRAK serah §10) |

Aturan tegas: fase **tidak pernah** menggerakkan stok; hanya PB menggerakkan stok. Fase tetap boleh diisi manual
(label, perilaku sekarang) untuk kasus barang dikirim tanpa PB. Pelanggaran fase≠stok = peringatan UI, bukan blok.

## R2.7 Dampak, migrasi, flag, urutan

| Hal | Isi |
|---|---|
| Dampak buku | **NOL jurnal**, nilai persediaan GL tak berubah (akun per produk); ledger bertambah 2 baris per item; warehouse_stock bergeser antargudang |
| Migrasi | (1) DROP fungsi `ship_/receive_stock_transfer`; (2) kolom `sales_order_id` (+FK komposit tenant), `completed_at`, `voided_*` bila perlu; (3) daftar `kosakata_ledger` / source_type tak perlu baru (`RELOKASI_GUDANG` ada; `RELOKASI_GUDANG_VOID` perlu masuk kosakata ledger); tak ada akun CoA baru |
| Flag | `pemindahan_barang`, kaos saja (grapgrap tak punya stok untuk dipindah; tenant lain 1 gudang) |
| Risiko | rendah-sedang: kaki ganda di satu transaksi, kunci urut; trigger `update_warehouse_stock_from_ledger` + `chk_ws_quantity` menolak stok negatif → 409 jujur; WAC tak bergeser bila penulisan langsung; jangan lewat `record_inventory_inbound` |
| Urutan | 0 ukur (batch/serial kaos, sumber_id 6 baris relokasi, Check 21 per-transfer) → 1 migrasi (DROP fungsi, kolom) → 2 service + rute + peta izin modul `warehouse` + inventaris rute + lantai gerbang → 3 Check/tes kolom hantu → 4 E2E kaos TES E2E (dua gudang: perlu gudang kedua TES di kaos, **tulis ke kaos = izin langsung**) → 5 flag |
| Izin LANGSUNG pemilik | YA: ketentuan Rule 3 baru (relokasi tak menghitung ulang WAC), migrasi + merge + restart, gudang kedua uji di kaos, flag. Dua langkah + akun transit = YA terpisah |

---

# BAGIAN 8 — URUTAN ANTAR R

R2 lebih kecil dan tak menyentuh buku → bisa lebih dulu dan menjadi uji harness untuk kebiasaan baru (kolom hantu,
lengan Check). R1 menyentuh CoA semua tenant, trigger SO, kaskade void, ±36 pembaca → butuh tahap 0 (ukur lanjut) dulu.
Keduanya independen di kode; satu-satunya tautan = saran pesan blok SO R2.6 dan lengan Check 21.

# BAGIAN 9 — Sumber

- NetSuite, Inventory Transfer Orders (Oracle help): https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_N2308766.html
- NetSuite Professionals archive "Why Item Fulfilment has no GL Impact?": https://archive.netsuiteprofessionals.com/t/26929031/why-item-fulfilment-has-no-gl-impact (hanya judul dilihat, tak dibuka)
- Charted, NetSuite transaction GL impact: https://charted.com/white-paper/what-is-a-netsuite-transaction-gl-impact-posting-rules-and-the-full-transaction-lifecycle/ (ringkasan hasil cari)
- SAP Help (movement 351/641/643, stock in transit MB5T): https://help.sap.com/doc/saphelp_470/4.7/en-US/4d/2b90dc43ad11d189410000e829fbbd/content.htm
- Accurate, persediaan barang dalam perjalanan (pembelian): https://accurate.id/marketing-manajemen/persediaan-barang-dalam-perjalanan/
- Accurate release notes (hanya pengiriman parsial SO): https://accurate.id/release-notes-aol/1013570/
- Tidak ditemukan: dokumen resmi Accurate untuk akun "Barang Terkirim"/jurnal Pengiriman Pesanan dan "Pemindahan Barang".

# BAGIAN 10 — Putusan yang dibutuhkan (ringkas)

| # | Putusan | Pemutus |
|---|---|---|
| P1 | R1: Barang Terkirim (Accurate) atau HPP saat kirim (NetSuite/SAP) | pemilik langsung (aturan buku) |
| P2 | R1: pendapatan diakui saat faktur untuk qty yang sudah terkirim | pemilik langsung |
| P3 | R1: pilihan model data A (perluas) vs B (tabel baru) | MASTER + BE2 |
| P4 | R1: kode akun 1-10660 (setelah diukur kosong di 6 tenant) | MASTER |
| P5 | R2: satu langkah dulu; dua langkah + akun transit nanti | MASTER (rekomendasi) |
| P6 | R2: Rule 3 diperluas — relokasi tak menghitung ulang WAC | pemilik langsung (aturan buku) |
| P7 | R2: tautan PB→SO mengisi fase otomatis | MASTER |
| P8 | Urutan: R2 dulu lalu R1 tahap 0 | MASTER |


---

# BAGIAN 11 — R2: KOREKSI IMPLEMENTASI + BUKTI (12 Okt 2026, BACKEND2)

Keputusan MASTER yang berlaku: P3 model A, P4 1-10660, P5 satu langkah, P7 PB ber-sales_order_id mengisi fase "tersedia" + audit,
P8 R2 dulu. P6 (relokasi tak menghitung ulang WAC) = izin langsung pemilik, diajukan sebelum merge.

| # | Temuan saat implementasi | Tindakan |
|---|---|---|
| K1 | **Ada JALUR TRANSFER KEDUA yang HIDUP**: `POST /api/items/{id}/stock-transfer` (items.py). Kolomnya valid, tetapi `unit_cost = purchase_price` dan `average_cost = purchase_price` pada kedua kaki. `get_weighted_average_cost()` membaca `average_cost` BARIS TERBARU, jadi satu transfer akan MENGGANTI WAC penjualan berikutnya dengan harga beli (mencemari HPP + Check 15). Memakai `source_type='STOCK_TRANSFER'`, di luar lengan Check 21. 0 baris pernah dibuat (tabel kosong, ledger tak ada TRANSFER_*), jadi belum ada kerusakan. | Diganti mendelegasikan ke inti PB (flag-gated; tanpa flag 409). Dicatat sebagai perubahan perilaku endpoint hidup. |
| K2 | `stock_transfer_items.quantity_variance` = kolom GENERATED (shipped - received); UPDATE langsung ditolak PG. Ditemukan harness data nyata, lolos tes stub. | Tak ditulis. |
| K3 | Trigger `prevent_st_modification` hanya membekukan `in_transit`/`received`; `completed` dan `voided` perlu ikut beku. Ditemukan harness (UPDATE pada `voided` lolos). | V412 memperluas trigger (completed -> hanya voided; voided beku). |
| K4 | `stock_transfers.total_value` bertipe integer-ish di skema Pydantic lama; nilai WAC desimal (Rp125.000,00 x qty). | Respons float (Law 25 nuansa); skema DB numeric. |
| K5 | Kode lama menerima `unit_cost` int dari klien (`int(qty*cost)`): pemotongan + nilai tak tepercaya. | Nilai dari WAC server, Decimal 2dp HALF_UP; field klien diabaikan. |
| K6 | Fase SO saat void: tak ada "nilai sebelumnya" yang tersimpan. | Fase hanya DIKOSONGKAN bila masih persis `tersedia @ gudang tujuan` hasil PB itu; selain itu tak disentuh (menyimpang dari §R2.6 "dikembalikan dari audit"). |
| K7 | Lengan 2 Check 21 (per produk-hari) tak melihat void yang jatuh di hari lain. | Lengan 3: berpasangan per DOKUMEN (`source_id`) per barang, mencakup RELOKASI_GUDANG_VOID. 6 baris 11 Sep lolos (satu source_id). |
| K8 | `ship_/receive_stock_transfer` (fungsi DB, kolom hantu) | DROP di V412. Rute /ship /receive: 409 `STOCK_TRANSFER_DUA_LANGKAH_BELUM_TERSEDIA`. |

Bukti (semua terhadap salinan nyata dump prod 11 Okt di DB scratch, transaksi ROLLBACK, nol jejak diverifikasi):
`scripts/ops/uji-nyata-pemindahan-barang.sh` -> 30 pemeriksaan OK, 0 GAGAL: kartu +2, jurnal NOL berubah (je/jl/total debit), stok
asal -qty dan tujuan +qty, WAC tidak berubah, nilai kartu produk neto nol (Inv-3 per barang), fase SO terisi lalu dikosongkan saat void,
stok kurang 409 tanpa baris, idempoten, `voided` beku, lengan 3 MERAH pada void sepihak, hitungan tabel kembali ke awal.
Tes unit `tests/unit/test_pemindahan_barang.py` 26 tes + 5 sabotase masing-masing memerahkan tes penjaganya.


---

# BAGIAN 12 — R2 v2: KEBUTUHAN FE (12 Okt 2026, permintaan WORKSPACE via MASTER; standar CW: Tinjau = angka server)

Prinsip: SATU penentu. `pemindahan_barang._rencana` / `_rencana_void` menghitung baris, nilai (WAC), stok sebelum/sesudah per gudang
dan SEMUA blok. Pratinjau memakainya tanpa menulis; jalur tulis memakai fungsi yang SAMA (kunci stok FOR UPDATE) dan menulis dari
hasilnya. Angka pratinjau == angka tulis dibuktikan harness data nyata (bagian 6).

| Rute | Izin (modul item) | Isi |
|---|---|---|
| `POST /api/stock-transfers/preview` | C | badan = badan POST draf (longgar: kolom kosong/cacat jadi BLOK, bukan 422). 200 walau diblok. |
| `POST /api/stock-transfers/create-and-complete` | P | ATOMIK buat + selesai satu transaksi (pola Accurate: Simpan = Selesai). Respons = bentuk /complete + `ulang`. |
| `POST /api/stock-transfers` | C | draf; sekarang menerima `X-Idempotency-Key`. `transfer_date` opsional (bawaan tanggal bisnis tenant). |
| `POST /{id}/complete/preview` | P | tinjau menyelesaikan draf tersimpan. |
| `POST /{id}/void/preview` `{reason}` | V | tinjau void: stok sebelum/sesudah per gudang (tujuan berkurang, asal kembali). |
| `POST /{id}/cancel/preview` `{reason}` | V | tinjau batal draf. |
| `GET /{id}/history` | R | riwayat bentuk SO: `{transfer_id, transfer_number, sales_order, events[{at,jenis,ringkas,aktor,dokumen,sumber}], total, omitted}`. |
| `GET /summary` | R | `{bulan, draft, selesai_bulan_ini, void_bulan_ini, nilai_selesai_bulan_ini}` (bulan bisnis tenant). |
| `GET /api/stock-transfers?search=` | R | cari nomor PB, referensi, nomor SO, nama/kode barang (tak peka huruf; `%`/`_` literal). |

Bentuk pratinjau (`data`): `{ok, blocks[{code,message,item_id?}], lines[{item_id,name,unit,item_code,quantity,available_from,available_to,unit_cost,value,from_before,from_after,to_before,to_after,notes}], from{id,name}, to{id,name}, total_quantity, total_value, sales_order{id,number,fase_sesudah{fase_lokasi,fase_gudang_id,teks,berubah}}|null, catatan_fase, transfer_number?, ulang, payload}`.
`payload` = badan ternormalisasi yang dapat dikirim apa adanya ke POST draf / create-and-complete.

Keputusan bentuk atomik: JALUR TERPISAH (`/create-and-complete`), bukan `?selesaikan=true`. Alasan: middleware izin mencocokkan
metode + path; parameter query tak terlihat, sehingga `?selesaikan=true` pada POST "" (izin C) akan MENYELESAIKAN (aksi P) dengan
izin buat saja. Path terpisah memberi izin P sendiri, respons sendiri, dan tak mengubah bentuk jawaban endpoint yang sudah ada.

Idempotensi (Law 14): `X-Idempotency-Key` (alias `Idempotency-Key`) pada POST "" dan `/create-and-complete`: kunci sama + isi
sama = respons asli + `X-Idempotent-Replay: true`; isi beda = 409 `IDEMPOTENCY_KEY_REUSED`; replay dicek SEBELUM nomor terbit;
respons tersimpan di transaksi yang sama dengan dokumennya. complete/void/cancel idempoten lewat status (`ulang`).

Blok baru pratinjau: `PB_GUDANG_ASAL_WAJIB`, `PB_GUDANG_TUJUAN_WAJIB`, `PB_GUDANG_SAMA`, `PB_TANPA_BARANG`, `PB_QTY_TIDAK_SAH`,
`PB_ALASAN_WAJIB` (di samping kode lama bagian R2).

Tak ada perubahan skema/migrasi di v2. Perubahan izin baca: GET stock-transfers dipetakan modul `item` R (sebelumnya tak
terpetakan = tertutup).


---

# BAGIAN 13 — R1 TAHAP 0: HASIL UKUR LANJUT (12 Okt 2026, BACKEND2; baca saja, tanpa kode, tanpa tulis)

Keputusan yang berlaku: P1 Barang Terkirim (pola Accurate), P2 pendapatan saat faktur, P3 model A (perluas + alokasi INSERT-only),
P4 kode 1-10660. Izin pemilik P1+P2 diminta langsung sebelum MERGE kode R1.

## 13.1 Koreksi/penambahan terhadap bagian 1-10

| # | Temuan | Dampak pada rancangan |
|---|---|---|
| T1 | Kartu stok SJ-faktur memakai `source_id = invoice_id` (BUKAN fulfillment_id); jurnal HPP `INVOICE_FULFILLMENT` juga `source_id = invoice_id`. Penautan jurnal ke kartu: `UPDATE inventory_ledger SET journal_id ... WHERE source_type='INVOICE_FULFILLMENT' AND source_id=<invoice> AND journal_id IS NULL`. | SJ-so (belum ada faktur) harus memakai `source_id = fulfillment_id`, `source_type = DELIVERY_NOTE`. Pembaca yang mencari "keluar dari faktur X" lewat `source_id = invoice_id` TIDAK akan melihatnya (T2). |
| T2 | `credit_notes._keluar_faktur_untuk_retur` (HPP asli NK retur) membaca `inventory_ledger WHERE source_id = original_invoice_id AND source_type IN (SALES_INVOICE, INVOICE_FULFILLMENT)`. `profitability_query` dan `kosakata_ledger.KELUAR_JUAL(_FAKTUR)` juga bergantung pada dua source_type itu. | Retur atas faktur yang barangnya dikirim lewat SJ-so akan gagal "barang tak pernah keluar" (400). Wajib: lewat tabel alokasi (invoice_item -> fulfillment_item -> kartu) dan source_type baru masuk KELUAR_JUAL. Tanpa ini = kelas cacat "stok hantu"/"retur ditolak salah". |
| T3 | `recompute_so_shipped`, `trg_so_shipped_from_fulfillment(_item)`, `so_memenuhi_selesai`, `terapkan_selesai_so`, `update_sales_order_status`, dan penjaga harian `verify_so_shipped_reconciliation` semuanya menghubungkan SJ ke SO LEWAT `sales_invoices`. | Semua harus mengenal `invoice_fulfillments.sales_order_id`. `verify_so_shipped_reconciliation` akan MERAH palsu begitu ada SJ-so bila tak diubah bersama (penjaga basi = Law 33). |
| T4 | `so_memenuhi_selesai` menghitung `kirim` lewat `invoice_fulfillment_items.invoice_item_id = sales_invoice_items.id` dan `tagih` lewat faktur; selesai = tagih>=qty AND (stok -> kirim>=qty) AND allocated-recognized<=0.005. | SJ-so menghitung `kirim` lewat `sales_order_item_id`; SO yang terkirim penuh sebelum difakturkan TIDAK selesai (benar: tagih<qty). Setelah faktur, `allocated - recognized` harus 0 karena pendapatan diakui saat faktur (P2). |
| T5 | `is_effective_journal` memuat daftar hitam source_type pembalik literal (`INVOICE_REVERSAL`, `BILL_REVERSAL`, `INVOICE_FULFILLMENT_REVERSAL`, `SALES_INVOICE_COGS_REVERSAL`). | Tambahkan `DELIVERY_NOTE_REVERSAL`, `DELIVERY_INVOICED_REVERSAL` (fungsi DB: CREATE OR REPLACE di migrasi). |
| T6 | `account_roles.role_key` dipagari CHECK enumerasi (`account_roles_role_key_check`, 44+ nilai). | Migrasi harus DROP+ADD CHECK dengan daftar yang dibangun dari definisi sekarang + `GOODS_DELIVERED_NOT_INVOICED` (jangan menyalin literal: ambil `pg_get_constraintdef` di DO block agar tak ada nilai terbuang). Juga `AccountRole` di `role_resolver.py` (+ tes katalog). |
| T7 | `seed_default_coa(p_tenant_id)` (8,5 KB) membangun CoA tenant baru; pola V151/V152: patch fungsi + backfill tenant lama + `account_roles`. | Satu migrasi: patch seed + backfill 6 tenant (kode 1-10660 diukur KOSONG di 6 tenant) + peran. |
| T8 | `journal_source_types` (FK-enforced): belum ada `DELIVERY_*`. Tabel punya kolom `is_active`. | Daftarkan 4 tipe jurnal (Law 6) SEBELUM kode menulisnya. |
| T9 | `tenant_config.revenue_recognition_policy`: hanya KAOS punya baris = `delivery` (billing saja saat posting; HPP+pendapatan saat /fulfill). Tak ada baris untuk 5 tenant lain (bawaan global `invoice`). 149 faktur, `recognize_at` semuanya NULL. | Alur SJ-so tak memerlukan kebijakan: pendapatan diakui di faktur untuk qty yang sudah terkirim (P2). Faktur tanpa SJ tetap mengikuti kebijakan tenant. Tidak ada konflik dengan 'delivery' kaos. |
| T10 | `journals.py:579` (daftar blok jurnal manual untuk dokumen turunan) memuat `INVOICE_FULFILLMENT`. `kosakata_ledger.DIKENAL`, `_SUMBER_*` di credit_notes. | Source_type baru wajib masuk daftar klasifikasi (tes `test_penulis_sql_mentah_dikenal` akan memaksa). |
| T11 | Pembaca `invoice_fulfillments` di kode: sales_invoices (14), deliveries (10), delivery_lampiran (6), so_riwayat (3), so_kirim (3), pengiriman_serah (2), dokumen (2), so_terkait, so_posisi, so_pengiriman, kode_order, cn_tertunda, bulk_export, bulk_dokumen, sales_orders. Yang JOIN `sales_invoices` lewat `f.invoice_id` (INNER) akan DIAM-DIAM menjatuhkan SJ-so: so_posisi:110, so_riwayat:193/799, kode_order:460, deliveries (list/detail/PDF/serah), dokumen:309, so_terkait:143, cn_tertunda:129, bulk_export:83. | Satu tes inventaris: setiap SELECT dari `invoice_fulfillments` yang JOIN `sales_invoices` harus LEFT JOIN atau menyaring `asal='faktur'` (daftar pinned; tambahan baru = merah). |
| T13 | FK ke `invoice_fulfillments`: `invoice_fulfillment_items`, `pengiriman_serah`, `pengiriman_resi`. `document_attachments` (entity_type 'delivery') tanpa FK. Tak ada view/matview yang menyebutnya. | Serah/resi/lampiran tak berubah (berkunci `fulfillment_id`). |

## 13.2 Revisi kompleksitas R1 (lebih besar dari perkiraan awal)

Daftar perubahan kode/DB minimum: (1) skema: kolom nullable + CHECK + tabel alokasi + trigger predikat; (2) CoA: peran + CHECK + seed
+ backfill + katalog Python; (3) 4 source_type jurnal + `is_effective_journal`; (4) SO: 5 fungsi DB (`recompute_so_shipped`,
2 trigger, `so_memenuhi_selesai`, `verify_so_shipped_reconciliation`, mungkin `update_sales_order_status`); (5) jalur baru
SJ-so + void SJ; (6) alokasi saat faktur (otomatis tertua-dulu) + kaskade void faktur; (7) NK retur lewat alokasi (T2);
(8) ~10 pembaca (T11) + tes inventaris; (9) laporan + Check harian + Check 21/Inv-2/Inv-9; (10) FE (kartu SO: tiga angka).
Usul pemecahan rilis (tiap rilis hijau sendiri, flag mati):
 R1-a skema+CoA+source_type+fungsi SO (tanpa perubahan perilaku; harness: hitung-ulang semua SO prod identik),
 R1-b pembaca (LEFT JOIN + tes inventaris) + NK lewat alokasi,
 R1-c jalur SJ-so + void + alokasi + kaskade void faktur di balik flag `pengiriman_sebelum_faktur`,
 R1-d laporan + Check harian,
 lalu E2E kaos (izin tulis uji), flag kaos, grapgrap terpisah.

## 13.3 Yang BELUM diukur (tahap 0 lanjut bila diminta)
- `update_sales_order_status` (isi), `bulk_export`/`dokumen` dampak PDF/ekspor, daftar lengkap pembaca per fungsi (baris).
- Perilaku auto-fulfill saat POSTING faktur dari SO yang sebagian sudah terkirim (jalur `AUTO_FULFILL:{id}`): harus melompati qty yang sudah teralokasi.
- Dampak laporan laba-rugi/neraca (reports.py) atas akun baru Barang Terkirim (akun ASSET baru muncul di neraca otomatis? periksa pengelompokan `psak_sub_category`).


---

# BAGIAN 14 — R1 IRISAN 1: SURAT JALAN DARI PESANAN TANPA FAKTUR, BARANG NON-STOK (12 Okt 2026, BACKEND2)

Putusan MASTER: irisan 1 = tanpa aturan buku baru (SJ non-stok = dokumen: nol jurnal, nol kartu stok, nol HPP). Irisan 2
(barang ber-stok + akun Barang Terkirim + NK retur lewat alokasi T1/T2 + Check saldo) menunggu P1+P2 pemilik.

## 14.1 Yang dibangun

| Komponen | Isi |
|---|---|
| V415 (skema, tanpa perubahan perilaku) | `invoice_fulfillments`: `invoice_id` NULLABLE, `+ sales_order_id`, `+ asal` ('faktur' DEFAULT / 'so'), CHECK kolom sesuai asal. `invoice_fulfillment_items`: `invoice_item_id` NULLABLE, `+ sales_order_item_id`, CHECK min. satu tautan. Tabel `sj_faktur_alokasi` (INSERT-only; hanya `voided_*`) + trigger predikat DB (alokasi aktif <= qty baris SJ dan <= qty baris faktur). Fungsi SO diperbarui: `trg_so_shipped_from_fulfillment(_item)`, `recompute_so_shipped`, `so_memenuhi_selesai`, `verify_so_shipped_reconciliation` (T3/T4). |
| V416 | flag `pengiriman_sebelum_faktur` kaos saja. |
| `services/sj_so.py` | satu penentu `rencana` (pratinjau == tulis; SEMUA blok), `tulis`, `rencana_void`/`void`, `alokasikan_saat_faktur`, `lepas_alokasi_faktur`. |
| `routers/sj_so.py` | `POST /api/sales-orders/{id}/delivery-notes[/preview]`, `POST /api/deliveries/{id}/void[/preview]`. |
| Hook faktur | posting: `alokasikan_saat_faktur` SEBELUM blok V318 (status pengiriman dihitung dari `fulfilled_qty` baru); void faktur: `lepas_alokasi_faktur` (SJ-so kembali "belum ditagih", TIDAK ikut void). |
| Pembaca (T11) | deliveries (list/detail/PDF/serah), riwayat SO + riwayat SJ, so_posisi, so_terkait, kode_order, dokumen, bulk_export, so_kirim (`terkirim_per_baris` + SJ-so) -> LEFT JOIN / COALESCE(`f.sales_order_id`, `si.sales_order_id`); tes inventaris T11 mengunci daftar INNER JOIN yang disengaja. |

## 14.2 Aturan (tanpa buku baru)

- Hanya baris SO `perlu_kirim` yang BUKAN stok (`track_inventory=false`). Baris ber-stok: diminta eksplisit -> 409 `SJ_SO_STOK_BELUM_DIDUKUNG`; default (tanpa `items`) -> dilewati dan dicantumkan di `dilewati`.
- Sisa kirim per baris = qty SO - terkirim (SJ faktur + SJ-so aktif). Melebihi -> 409 `SJ_SO_QTY_MELEBIHI_SISA`.
- SO draf/batal/selesai -> 409 `SJ_SO_STATUS_TIDAK_BOLEH`. Tanpa flag -> 409 `SJ_SO_NONAKTIF`.
- Pendapatan non-stok TETAP diakui saat faktur diposting (jalur lama; tak berubah). Alokasi hanya menaikkan `fulfilled_qty` baris faktur dan status pengiriman.
- Void SJ mandiri hanya `asal='so'` dan hanya bila tak ada alokasi aktif (409 `SJ_SUDAH_DITAGIH` menyebut nomor faktur); SJ asal faktur tetap lewat void faktur (409 `SJ_VOID_ASAL_FAKTUR`).
- Idempotensi (Law 14): `X-Idempotency-Key` pada pembuatan; void idempoten lewat status.
- Kunci (Law 13): `SJ_SO:{tenant}:{so}` + `INVOICE_FULFILL` tiap faktur SO (urut) = kunci yang sama dengan jalur faktur: tak ada kirim ganda lintas jalur.

## 14.3 URUTAN RILIS (kebalikan dari V407/V410: SKEMA DULU)

Kode membaca kolom `asal`/`sales_order_id`, jadi V415 WAJIB terpasang SEBELUM kode berjalan. V415 sendiri kompatibel dengan kode
lama (kolom baru ber-DEFAULT, fungsi diperluas). Urutan: (1) merge-only (tanpa restart), (2) apply V415 (backup otomatis),
(3) restart + verifikasi penanda, (4) apply V416 (flag kaos).

## 14.4 Bukti

- `scripts/ops/uji-nyata-sj-so.sh` (dump prod di scratch): 302 SO (shipped/invoiced/status/`so_memenuhi_selesai` + rekonsiliasi per tenant) IDENTIK sebelum == sesudah V415, dan sesudah `recompute_so_shipped()` atas semua SO; lalu 24 pemeriksaan alur (pratinjau nol tulis, tulis, idempotensi, qty melebihi, alokasi tanpa hitung ganda, predikat DB, void ditolak/diizinkan, nol jejak).
- Tes unit `test_sj_so.py` (20) + penyesuaian empat tes pin-string yang sengaja berubah.

## 14.5 Kontrak FE

`POST /api/sales-orders/{id}/delivery-notes/preview` badan `{items?:[{sales_order_item_id,quantity}], fulfillment_date?, notes?, serah?}` ->
`{ok, blocks[{code,message,sales_order_item_id}], lines[{sales_order_item_id,item_id,name,unit,ordered,delivered_before,quantity,remaining_after}], dilewati[], sales_order{id,number,status}, fulfillment_date, notes, serah?, serah_aktif, payload}`.
`POST .../delivery-notes` (header `X-Idempotency-Key`) -> `{success,data:{id,delivery_number,asal:'so',status,sales_order_id,fulfillment_date,lines,dilewati}}`.
`POST /api/deliveries/{id}/void/preview {reason}` -> `{ok,ulang,delivery_number,blocks[],payload}`; `POST .../void {reason}` -> `{success,ulang}`.
`GET /api/deliveries[/{id}]`: `asal` ('faktur'|'so'), `invoice_id`/`invoice_number` bisa null.


---

# BAGIAN 15 — R1 IRISAN 2: BARANG BER-STOK, BARANG TERKIRIM (12 Okt 2026, BACKEND2) — ATURAN BUKU BARU

Putusan pemilik LANGSUNG (12 Okt): **P1 = Barang Terkirim** (pola Accurate; HPP sepadan dengan pendapatan), **P2 = pendapatan diakui
saat faktur**. Pola: SAP/NetSuite/Accurate untuk "kirim sebelum faktur". Flag: `pengiriman_stok_sebelum_faktur` (kaos dulu), di atas
`pengiriman_sebelum_faktur` (irisan 1).

## 15.1 Jurnal

| Peristiwa | Jurnal | source_type | Kartu stok |
|---|---|---|---|
| SJ dari pesanan, barang ber-stok | Dr `GOODS_DELIVERED_NOT_INVOICED` (1-10660) / Cr `INVENTORY_MERCHANDISE` (1-10600), qty x WAC snapshot | `DELIVERY_NOTE` | keluar, `source_id` = id SJ, `journal_id` terisi |
| Faktur diposting (alokasi tertua-dulu) | (i) Dr `COGS_SALES` / Cr Barang Terkirim, nilai = snapshot SJ (alokasi yang menghabiskan sisa baris SJ mengambil sisa BIAYA: clearing tepat); (ii) Dr Dimuka / Cr Penjualan untuk qty teralokasi (menghabiskan qty baris = sisa pendapatan; selain itu proporsional) | `DELIVERY_INVOICED` + `INVOICE_REVENUE` | — (stok sudah keluar saat SJ) |
| Void SJ mandiri (belum ditagih) | pembalik penuh jurnal SJ | `DELIVERY_NOTE_REVERSAL` | pembalik `DELIVERY_NOTE_VOID` |
| Void faktur | pembalik reklas HPP; INVOICE_REVENUE alokasi ikut dibalik; SJ KEMBALI "belum ditagih" (TIDAK ikut void) | `DELIVERY_INVOICED_REVERSAL` + `INVOICE_REVENUE_REVERSAL` | — |
| Barang non-stok | tanpa jurnal, tanpa kartu (irisan 1) | — | — |

Bagian faktur yang BELUM terkirim mengikuti kebijakan tenant seperti biasa (kirim-otomatis `invoice`, atau tertunda `delivery` di kaos).
Barang yang sudah terkirim penuh lewat SJ TIDAK memicu keluar stok / HPP kedua (`inventory_items_for_fulfill` memakai sisa).

## 15.2 Koreksi dan temuan saat implementasi

| # | Temuan | Tindakan |
|---|---|---|
| I1 | Blok "orphan revenue" void faktur membalik SATU `INVOICE_REVENUE` per faktur (`fetchrow`). Alokasi menambah jurnal INVOICE_REVENUE kedua -> satu akan tertinggal. | Diubah mengulang SEMUA jurnal yang belum dibalik (urut `created_at`); perilaku faktur lama (satu jurnal) identik. |
| I2 | Profitabilitas (`profitability_query` 2x CTE, `profitability_reconciliation`) menghitung HPP dari kartu `source_id = faktur`; kartu `DELIVERY_NOTE` ber-`source_id = SJ`. Tanpa perubahan laba kotor faktur dari SJ-so TERLAMPAU TINGGI. | Ditambah cabang `UNION ALL` dari `sj_faktur_alokasi.total_cost` (aktif, faktur POSTED, rentang tanggal); `DELIVERY_NOTE` TIDAK masuk `KELUAR_JUAL_FAKTUR` (tak ada hitung ganda). |
| I3 | `credit_notes._keluar_faktur_untuk_retur` (T2) tak melihat barang yang dikirim lewat SJ-so. | Ditambah baris sintetis dari alokasi aktif (qty, biaya snapshot, gudang SJ). Harness: retur menemukan sisa=Q, biaya=WAC snapshot, gudang SJ. |
| I4 | `account_roles_role_key_check` = enumerasi; `seed_default_coa`/`seed_default_account_roles` perlu ditambal untuk tenant baru. | V417 membangun CHECK dari definisi yang ada + nilai baru (tak ada nilai terbuang), menambal fungsi lewat anchor yang GAGAL-KERAS bila hilang, backfill akun+peran 6 tenant. |
| I5 | `is_effective_journal` daftar hitam pembalik literal. | V417 menambah `DELIVERY_NOTE_REVERSAL`, `DELIVERY_INVOICED_REVERSAL` (anchor gagal-keras). |
| I6 | Pratinjau jurnal faktur (`/preview-journal`) menghitung dari badan permintaan, bukan alokasi per baris. | BELUM menirunya: bila pesanan induk punya SJ tanpa faktur, pratinjau memberi PERINGATAN eksplisit (bukan angka palsu). Celah dicatat; angka posting = server. |
| I7 | `kosakata_ledger.KELUAR_JUAL` memuat `DELIVERY_NOTE`; `BATAL_JUAL` memuat `DELIVERY_NOTE_VOID`. | tes `test_kosakata_keluar_jual` diperbarui + `sj_so.tulis` terdaftar sebagai penulis SQL mentah. |

## 15.3 Penjaga

- **Check 23 (HIGH, harian):** saldo Barang Terkirim (`is_effective_journal`, role-resolved) == Σ (`total_cost` SJ-so aktif - alokasi aktif). Fail-CLOSED (peran tak terpetakan / keluaran tak terduga = `__GAGAL__`). Dibuktikan merah: jurnal `DELIVERY_NOTE` tanpa SJ (+1000) dan `DELIVERY_INVOICED` tanpa alokasi (-500).
- **Check 21:** `DELIVERY_NOTE` masuk `STOK_WAJIB_JURNAL` (kartu keluar tanpa jurnal = merah; dibuktikan).
- **Check 15/16:** tidak bergeser oleh SJ-so, alokasi, void faktur, void SJ (drift sebelum == sesudah, dump nyata); bergeser bila kartu tanpa jurnal (kontrol merah).
- **Laporan:** `GET /api/delivery-notes/pending-invoice`: baris per SJ (qty/ditagih/belum, nilai snapshot, umur) + saldo buku + selisih.

## 15.4 Urutan rilis dan izin

Migrasi-saja dulu (V417 skema/peran/akun/source_type: kompatibel dengan kode lama; BELUM ada kode yang menulis DELIVERY_*), lalu kode + restart,
lalu V418 flag kaos. Seluruhnya aturan buku baru -> izin LANGSUNG pemilik per langkah; grapgrap terpisah.
