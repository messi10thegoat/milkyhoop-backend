# KONTRAK — Unit Pengiriman generik (serah-terima, resi, lampiran) — DISETUJUI MASTER 11 Okt 2026 (+ koreksi implementasi di §11)

Pemilik: BACKEND2. Status: **DISETUJUI** (K1–K6 mengikuti rekomendasi; syarat K2 & K4 di §11). Kode: cabang `feat/pengiriman-serah`. Pola acuan: NetSuite Item Fulfillment / SAP Delivery.
Semua angka di bagian 1 diukur 11 Okt 2026 pada prod (baca-saja) dan kode master `37d12f32`.

## 1. Hasil ukur

| # | Pertanyaan | Hasil terukur |
|---|---|---|
| U1 | `POST /sales-orders/{id}/fulfill` menulis ke tabel mana? | **`invoice_fulfillments` + `invoice_fulfillment_items`** (satu Surat Jalan per faktur SO), lewat `services/so_pengiriman.py` yang memanggil inti `/sales-invoices/{id}/fulfill`. **Tidak** menulis `sales_order_shipments`. Tidak ada tabel `delivery`: `/api/deliveries` membaca `invoice_fulfillments`. |
| U2 | `carrier / tracking_number / status / delivered_at` dipakai? | Kolom itu **hanya ada di `sales_order_shipments`** (0 baris; jalurnya `POST /{id}/ship` dinonaktifkan 409 oleh keputusan K2, `GET /{id}/shipments` masih hidup). **`invoice_fulfillments` TIDAK punya** carrier/resi/delivered_at; `status` hanya `posted` (9) / `voided` (2). Jadi: tak ada cara serah, resi, atau tanggal diterima di jalur yang hidup. |
| U3 | Lampiran dokumen? | Ada, pola U1/L1: berkas di **MinIO** (`<tenant>/uploads/lampiran/<sha256><ext>`, baris `documents` storage_type='s3', tautan `document_attachments`). Router lengkap hanya `sales_order_lampiran.py` (maks 10 berkas × 10 MB, kuota di transaksi + advisory lock). CHECK `document_attachments.entity_type` **sudah memuat `'delivery'`** (tak perlu ubah constraint); baris `delivery` saat ini 0. |
| U4 | Alamat kirim | `sales_orders.shipping_address` (terisi 19 dari 299 SO) dan `customers.alamat`. PDF Surat Jalan hari ini mencetak **hanya `customers.alamat`** (`deliveries.py:171,260,456`; `delivery_note.html:73`). `sales_orders.shipping_method` (teks bebas; 1 baris = "JNE") tidak sampai ke Surat Jalan. |
| U5 | Lokasi bawaan | `so_pengiriman._gudang`: badan permintaan → `sales_orders.warehouse_id` → satu-satunya gudang aktif / satu `is_default`; selain itu blok `SO_FULFILL_WAREHOUSE_REQUIRED`. **Fase "Tersedia di X" (`sales_orders.fase_lokasi='tersedia'`, `fase_gudang_id`) TIDAK dibaca** oleh pengiriman. |
| U6 | Trigger | `trg_so_shipped_recompute` **AFTER INSERT/UPDATE/DELETE per baris** pada `invoice_fulfillments` & `invoice_fulfillment_items` (menghitung ulang `sales_orders.shipped_qty`). Akibat desain: **jangan menambah kolom di `invoice_fulfillments` lalu meng-UPDATE-nya** — tiap UPDATE metadata memicu hitung ulang SO. Metadata serah ditaruh di tabel terpisah. |
| U7 | Pola "Preferences" | `accounting_settings` (satu baris/tenant; sudah memuat `default_dp_percent`, `default_uang_muka_account_id`, `default_quote_*`) + `services/default_dokumen.py` (server menghitung bawaan, FE menampilkan & mengirim, dokumen menyalin snapshot). |
| U8 | Flag | `tenant_features(tenant_id, feature, enabled)`. Flag `conversational_ship_page_so` sudah ON di kaos & grapgrap (halaman "Kirim barang" CW). |

Kesimpulan ukur: **jalur hidup tidak punya serah-terima apa pun**; semua kolom yang "tampak ada" milik jalur mati. Kontrak ini **tidak menghidupkan `sales_order_shipments`** (K2: dua jalur = pembukuan bercabang).

## 2. Data (semua ADITIF; tak ada ALTER pada `invoice_fulfillments`)

**`metode_serah`** — daftar kecil per tenant (= NetSuite *Ship Method items*; koreksi MASTER 11 Okt: bukan enum tetap)

| kolom | tipe | catatan |
|---|---|---|
| id | uuid PK | |
| tenant_id | text NOT NULL | Law 24 |
| nama | varchar(100) NOT NULL | "JNE", "J&T", "Ambil di Toko Manado", "Antar sendiri" |
| jenis | varchar(20) NOT NULL CHECK IN (`kurir`,`antar_sendiri`,`ambil_sendiri`) | jenis dasar — satu-satunya yang diketahui kode |
| aktif | boolean NOT NULL DEFAULT true | tak ada hapus-keras bila terpakai (FK RESTRICT); nonaktifkan |
| urutan | integer NOT NULL DEFAULT 0 | |
| created_at/updated_at/created_by | | |
| UNIQUE (tenant_id, lower(nama)) | | |

**`pengiriman_serah`** — 1:1 dengan Surat Jalan (PK = fulfillment_id)

| kolom | tipe | catatan |
|---|---|---|
| fulfillment_id | uuid PK, FK `invoice_fulfillments(id)` | |
| tenant_id | text NOT NULL | |
| metode_serah_id | uuid NULL FK `metode_serah` (RESTRICT) | |
| metode_nama | text NULL | **snapshot** nama saat dibuat (setelan diubah tak menyentuh dokumen lama — pola default_dokumen) |
| jenis_serah | varchar(20) NULL | **snapshot** jenis |
| kirim_ke | text NULL | alamat kirim snapshot; NULL bila `ambil_sendiri` |
| kirim_ke_sumber | varchar(10) NULL CHECK IN (`pesanan`,`pelanggan`,`manual`) | |
| diterima_pada | date NULL | **USUL opsional (§9-K3)**, stempel saja |
| diubah_at / diubah_oleh | | |

**`pengiriman_resi`** — BANYAK nomor per Surat Jalan (= NetSuite *Packages* / SAP handling unit)

| kolom | tipe | catatan |
|---|---|---|
| id | uuid PK | |
| tenant_id | text NOT NULL | |
| fulfillment_id | uuid NOT NULL FK | |
| nomor_resi | varchar(100) NOT NULL | dipangkas; kosong ditolak |
| kurir_nama | varchar(100) NULL | bila resi dari kurir berbeda dari metode |
| urutan | smallint NOT NULL | |
| UNIQUE (fulfillment_id, lower(nomor_resi)) | | maks 20 resi/SJ (422) |

**Preferensi** — dua kolom NULL di `accounting_settings`: `default_lokasi_kirim_id` (FK warehouses), `default_metode_serah_id` (FK metode_serah). Nama diubah/dinonaktifkan → bawaan dilewati diam di server dan FE menampilkan "belum diatur" (bukan galat).

**Lampiran** — tanpa tabel baru: `documents` + `document_attachments(entity_type='delivery', entity_id=<fulfillment id>)`.

## 3. API

Semua di belakang flag `pengiriman_serah` (§7). Tenant tanpa flag: field baru di badan permintaan → **409 `SERAH_NONAKTIF`** (bukan diabaikan diam — Law 34); respons GET tanpa kunci `serah` (grapgrap byte-identik).

1. `GET/POST /api/metode-serah`, `PATCH /api/metode-serah/{id}` (nama, jenis, aktif, urutan). Jenis boleh diubah hanya selama belum dipakai SJ mana pun (409 bila sudah; SJ lama menyimpan snapshot jadi tetap benar, tapi daftar jadi menyesatkan).
2. `POST /api/sales-orders/{id}/fulfill` dan `/fulfill/preview` — badan menerima objek opsional:
   ```json
   "serah": { "metode_serah_id": "uuid|null", "kirim_ke": "teks|null", "resi": [{"nomor": "JNE123", "kurir": null}] }
   ```
   Aturan: `ambil_sendiri` → `kirim_ke` dan `resi` harus kosong (422); `antar_sendiri` → `resi` kosong; `kurir` → resi boleh banyak. Berlaku untuk **setiap SJ yang dibuat oleh satu panggilan** (satu SO bisa jadi banyak faktur → banyak SJ); resi per-SJ diubah lewat butir 3. Isi `serah` ikut hash idempotensi (`hash_payload(body)`) → replay sah, isi beda 409 (Law 14, sudah ada).
   `data.serah` ikut di respons dan di pratinjau (termasuk lokasi & alamat yang DIPUTUSKAN server + sumbernya).
3. `PUT /api/deliveries/{id}/serah` — ganti-seluruhnya (metode, kirim_ke, resi[], diterima_pada). 409 bila SJ `voided`. Metadata non-keuangan → tidak dikunci periode; tiap perubahan masuk log audit (Law 8/12). Lock `DELIVERY_SERAH:{tenant}:{id}`.
4. `POST/GET/DELETE /api/deliveries/{id}/attachments[/{aid}[/download]]` — cermin `sales_order_lampiran.py` (kuota 10 × 10 MB di dalam transaksi + advisory lock, MinIO, unggah sebagian-sukses). DELETE hanya melepas tautan (berkas `documents` tetap; log audit). Izin modul `delivery` R/U.
5. `GET /api/deliveries/{id}` dan daftar: tambah `serah{metode, jenis, kirim_ke, sumber, diterima_pada}`, `resi[]`, `lampiran_jumlah`. PDF Surat Jalan: blok "Serah-terima" (metode, kirim ke, resi) — **perubahan tepi pada `delivery_note.html`** (baca versi sekarang dulu, skill PDF); `ambil_sendiri` mencetak "Diambil sendiri di {lokasi}".
6. Bawaan: `GET /api/default-dokumen/pengiriman?sales_order_id=` → `{lokasi:{id,nama,sumber}, metode:{id,nama,jenis}|null, kirim_ke:{teks,sumber}}`; set via `PUT /api/accounting-settings` (dua kolom baru; modul izin sama dengan setelan akuntansi yang ada).

## 4. Penentuan bawaan (urutan = tahan banting, selalu dapat ditimpa)

**Lokasi**: (1) `warehouse_id` badan → (2) **bila `fase_lokasi='tersedia'`: `fase_gudang_id`** → (3) `sales_orders.warehouse_id` → (4) `default_lokasi_kirim_id` → (5) satu-satunya gudang aktif / `is_default` (perilaku sekarang) → blok `SO_FULFILL_WAREHOUSE_REQUIRED`.
Catatan jujur: butir 2 & 4 mengubah **gudang mana yang dikurangi stoknya** bila FE tidak mengirim `warehouse_id` — bukan logika posting. Pagar: berlaku hanya bila flag ON (kaos), pratinjau menampilkan lokasi + sumber sebelum tulis, dan stok kurang di lokasi itu = 409 jelas (bukan jatuh diam ke gudang lain).
**Cara serah**: badan → `default_metode_serah_id` → kosong (belum diatur; SJ tetap sah tanpa serah).
**Kirim ke**: badan → `sales_orders.shipping_address` → `customers.alamat`; metode berjenis `ambil_sendiri` → tidak dipakai.

## 5. Tabel pemetaan ke NetSuite / SAP

| MilkyHoop | NetSuite (Item Fulfillment) | SAP (Outbound Delivery) |
|---|---|---|
| Surat Jalan = `invoice_fulfillments` | Item Fulfillment | Outbound Delivery |
| Dari lokasi (`warehouse_id`) | Location (inventory location) | Shipping Point / Storage Location |
| Lokasi bawaan tenant | Preferences (Setup ▸ Accounting) / default location | Shipping point determination |
| Fase "Tersedia di X" → lokasi bawaan X | (tak ada setara; padanan: Location di baris SO) | Plant/StLoc di item SO |
| Cara serah = `metode_serah` (daftar per tenant) | **Ship Method** (shipping item list) | Shipping Condition / Shipping Type |
| `jenis`: kurir / antar_sendiri / ambil_sendiri | jenis item pengiriman (Pick up / Delivery) | Shipping condition "Customer pick-up" |
| Nama kurir bebas (`kurir_nama`) | Carrier / Ship Method name | Forwarding agent (partner SP) |
| Kirim ke (`kirim_ke`) | Ship To Address (Ship To Select) | Ship-to party (WE) address |
| Resi banyak (`pengiriman_resi`) | Packages sublist (Tracking Number) | Handling Unit (external ID / tracking) |
| Lampiran (`document_attachments 'delivery'`) | Files / Attachments subtab | GOS attachment (Generic Object Services) |
| Status: `posted` = barang keluar & jurnal | Shipped (Ship Status) | Goods Issue posted |
| `voided` | Item Fulfillment dihapus/di-reverse | GI reversal |
| Picked / Packed | Ship Status Picked/Packed | Picking / Packing status |
| Delivered / diterima | (NetSuite tak punya status native; via integrasi kurir) | Proof of Delivery (POD) |
| Transfer antar-lokasi barang ber-stok | Transfer Order | Stock Transport Order |

## 6. Ironlaws (skill `milkyhoop-ironlaws` dibaca)

| Law | Kaitan | Penegakan |
|---|---|---|
| 1/16, 4, 20 (buku) | **Tidak disentuh**: nol jurnal, nol `inventory_ledger`, nol `warehouse_stock`, nol HPP baru. `_execute_fulfillment` tak diubah. | Tes bukti: SJ dengan & tanpa `serah` menghasilkan baris jurnal/kartu stok **identik** (harness ROLLBACK, bandingkan SUM & hitungan; kontrol merah = sabotase yang menulis jurnal). **Bila saat implementasi terbukti menyentuh buku → STOP.** |
| 2/3/19 | Nominal SJ beku; kolom baru non-keuangan, tabel terpisah (tak ada UPDATE pada `invoice_fulfillments`, jadi `trg_so_shipped_recompute` tak terpancing). | Tes: tidak ada UPDATE/INSERT ke `invoice_fulfillments` dari kode serah. |
| 5 | Periode: edit serah bukan transaksi keuangan → tak dikunci; pembuatan SJ tetap melewati cek periode yang ada. | Putusan §9-K4. |
| 6 | Traceability: `pengiriman_serah.fulfillment_id` = jejak ke SJ ke jurnal. | |
| 8/12 | Tak ada mutasi senyap: tiap PUT serah / lepas lampiran → log audit. | |
| 13 | Advisory lock `DELIVERY_SERAH:{tenant}:{id}`; buat SJ tetap `INVOICE_FULFILL:{id}`. | |
| 14 | Idempotensi lewat `X-Idempotency-Key` yang ada; `serah` masuk hash. | |
| 23 | SJ + serah + resi dalam SATU transaksi (satu gagal = nol tulisan). | |
| 24 | `tenant_id` eksplisit di tiap SQL tabel baru (gateway BYPASSRLS, bukan pagar). | |
| 33/34 | Kontrol merah di tiap pagar; klaim "aditif/nol buku" dibuktikan ukur, bukan janji. | |

## 7. Flag & peluncuran

- Flag baru `pengiriman_serah` (tenant_features), migrasi **KAOS SAJA** (pola V405/V406: INSERT guard `WHERE EXISTS Tenant`, `ROLLBACK` pendamping, DO-block penjaga "tenant lain NOL"). grapgrap = 0 sampai putusan pemilik.
- Migrasi skema: tiga tabel baru + dua kolom di `accounting_settings` (ADD COLUMN NULL, tanpa default → tak menulis ulang tabel). Rollback = DROP tabel baru + DROP kolom (aman: tak ada FK masuk dari tabel lama).
- Urutan: (1) migrasi skema (apply_mig, owner langsung) → (2) kode BE + restart (jendela MASTER, owner langsung) → (3) flag kaos → (4) FE WORKSPACE (setelah Tahap 2 sub-mode). Tanpa FE, flag ON = tanpa efek.

## 8. Rencana uji (bukan kode)

Tabel rute app (rute literal sebelum `/{id}`), kontrol merah tiap pagar; 422 kombinasi jenis×resi×kirim_ke; replay idempoten; banyak-SJ per SO; bukti nol-buku (§6); void SJ → serah/resi tetap terbaca; flag OFF = respons byte-identik; PDF render kaos (teks/piksel dibanding sebelum); lampiran kuota paralel (cermin uji L1); lokasi bawaan 5 jalur + stok kurang = 409.

## 9. Keputusan terbuka (butuh jawaban MASTER/pemilik)

- **K1** Seed `metode_serah` untuk kaos (mis. "Kurir", "Antar sendiri", "Ambil sendiri") atau kosong dulu & pemilik mengisi? Rekomendasi: seed tiga baris generik.
- **K2** Fase "Tersedia di X" menang atas `sales_orders.warehouse_id`? (rekomendasi: ya — fase = posisi barang SEKARANG).
- **K3** "Delivered/diterima": usul stempel `diterima_pada` (tanpa mesin status, tanpa jurnal) — atau tunda. Rekomendasi: sertakan sebagai stempel opsional.
- **K4** Edit serah pada SJ di periode tertutup: izinkan (rekomendasi, non-keuangan) atau blok?
- **K5** Resi juga lewat `/sales-invoices/{id}/fulfill` (jalur faktur)? Rekomendasi: fase 1 hanya jalur SO (CW), helper dibuat bersama agar jalur faktur bisa menyusul tanpa salinan.
- **K6** Lampiran SJ: lepas-tautan oleh siapa (pengunggah / OWNER / modul `delivery` U)? Rekomendasi: modul `delivery` U + log audit.

## 10. Celah dicatat (di luar cakupan)

- **Transfer Order**: `RELOKASI_GUDANG` sudah ada. Fase "Tersedia di X" hanyalah label manual; tidak ada tautan ke Transfer Order sehingga barang bisa berfase X padahal stok masih di gudang lain → pengiriman ke X akan 409 stok kurang (fail-loud, benar), tetapi tak ada petunjuk "buat Transfer Order dulu". Usul FE: pesan blok menyebut Transfer Order.
- `sales_orders.shipping_method` (teks bebas) dan `sales_order_shipments` tetap sebagai peninggalan; tidak dimigrasi.
- `PATCH /items/{id}/status` tidak memfilter `deleted_at` (lihat butir A di laporan).

## 11. Koreksi implementasi & hasil uji (11 Okt 2026, sesudah persetujuan)

**Perubahan terhadap draf (diukur, bukan selera):**
1. **Preferensi = tabel `pengiriman_preferensi`**, BUKAN dua kolom `accounting_settings` (§2/§3-6). Alasan terukur: 5 dari 6 tenant
   tak punya baris `accounting_settings`, GET-nya sengaja baca-saja (#38), dan `response_model` bersama akan memunculkan kunci
   baru di SEMUA tenant. API: `GET/PUT /api/pengiriman/preferensi`.
2. **Izin**: `/api/deliveries/*` memakai modul `sales_invoice` di PermissionMiddleware (bukan `delivery`) → serah U, lampiran U/R/D
   di modul itu. `/api/metode-serah*` + `/api/pengiriman/preferensi` = modul `warehouse`; `/api/default-dokumen/pengiriman` = `sales_order` R.
3. **Cara serah = daftar per tenant + jenis dasar** (koreksi MASTER 11 Okt), bukan enum tetap — sudah di §2.
4. Pengiriman memuat `pengiriman_serah` 1:1 + `pengiriman_resi` N; `invoice_fulfillments` TIDAK pernah di-UPDATE.
5. Migrasi: **V407** (empat tabel) lalu **V408** (flag `pengiriman_serah` + seed 3 cara serah, KAOS saja). V408 hanya sesudah kode hidup.

**Syarat K2 (MASTER):** fase menang atas `sales_orders.warehouse_id` HANYA bila flag ON; pratinjau menampilkan lokasi + sumber
("dari status pesanan: Tersedia di X" — `warehouse.source_label`); pilihan gudang tak boleh menggeser HPP.
Bukti: WAC = `get_weighted_average_cost(tenant, produk)` (tanpa gudang) di rencana DAN inti penulis; harness data nyata (salinan
backup 11 Okt, SO kaos SO-2609-0008, transaksi ROLLBACK): jalur lama vs flag+serah → jurnal (4 baris), kartu stok, stok per
gudang, Surat Jalan, status SO **identik**; gudang W1 vs W2 → jurnal & unit_cost/total_cost **identik**, stok berkurang di gudang
yang dipilih; fase "Tersedia di W2" → lokasi W2 bersumber `fase`; flag OFF → fase diabaikan, gudang lama. Kontrol merah harness:
angka jurnal diubah → pembanding melihat beda; pembanding kartu stok vakum (0 baris) sempat ditangkap dan diperbaiki.
**Syarat K4:** edit serah di periode tertutup boleh; jejak `audit_logs` (`SJ_SERAH_CHANGED`, `SJ_LAMPIRAN_DILEPAS`) di transaksi yang sama.

**Kompatibilitas:** tenant tanpa flag → field `serah` di badan = 409 `SERAH_NONAKTIF`; respons GET/pratinjau tanpa kunci `serah`;
cetakan SJ tak berubah; hash idempotensi klien lama tak berubah (`serah` ikut hash hanya bila dikirim).
