# KONTRAK — Alamat pelanggan terstruktur (triase #4) — 16 Okt 2026

Status: **TAHAP KONTRAK** (BACKEND, atas perintah MASTER). Nol tulis DB, nol kode. Migrasi **V428** (dipesan
MASTER untuk unit ini) dan kode baru hanya sesudah **GO pemilik**. Semua angka di bawah diukur 16 Okt 2026 dari
DB produksi (read-only) dan master `b65747ad`. Isi alamat pribadi TIDAK dicetak; hanya hitungan.

---

## 1. Keadaan sekarang (terukur)

### 1.1 Kolom alamat pelanggan

| Tempat | Kolom | Isi (seluruh DB: 103 pelanggan) | Catatan |
|---|---|---|---|
| `customers` | `alamat` text | **7** terisi (grapgrap 3/81, kaos 3/21, tenant lain 1) | SATU-SATUNYA alamat yang ditulis API (`body.address` → `alamat`) |
| `customers` | `address` text | **0** terisi | kolom warisan; dibaca `quotes.py:2386` sebagai cadangan, tak pernah ditulis |
| `customers` | `city`, `province`, `postal_code` | grapgrap 1/1/0, kaos 1/1/1 | ditulis API (POST/PUT customer) |
| `customers` | `negara` char(3) | grapgrap 6 = `IDN`, sisanya NULL | V420 identitas pajak |
| `customers` | multi-baris (`\n`) | 0 | panjang maks 74 karakter |
| `sales_orders` | `shipping_address` text | grapgrap 0/94, kaos 19/228 (16 = persis alamat pelanggan) | teks bebas per SO; snapshot |
| `tax_invoices` | `alamat_pembeli` text | 0 baris tabel | diisi dari `customers.alamat` saat dibuat |
| `vendor_addresses` | tabel anak terstruktur (billing/shipping) | **0 baris** | preseden di repo: `schemas/vendors.AlamatTerstruktur` + `routers/vendors._tulis_anak_pemasok` |

Tidak ada tabel `customer_addresses`. Satu pelanggan = satu alamat teks + kota/provinsi/kode pos datar.

### 1.2 Konsumen alamat pelanggan di kode BE (master `b65747ad`)

| Konsumen | Membaca | Untuk |
|---|---|---|
| `routers/customers.py` (list, detail, create, update, riwayat label) | `alamat`→`address`, `city`, `province`, `postal_code`, `negara` | API pelanggan |
| `routers/sales_invoices.py:1359, 5865, 6045` | `c.alamat AS pelanggan_alamat` → `customer_address` | detail faktur + PDF (`sales_invoice.html`, `sales_invoice_b.html`) |
| `routers/deliveries.py:175, 270, 472` | `c.alamat AS customer_address` | daftar/detail SJ + PDF `delivery_note.html` |
| `routers/quotes.py:2383` + `services/penawaran_cetak.alamat_pelanggan` | `alamat` ATAU `address`, + `city` bila belum termuat | PDF penawaran "Kepada Yth." |
| `routers/tax_invoices.py:167, 258` | `customer["alamat"]` → `alamat_pembeli` | e-Faktur / Coretax |
| `services/pengiriman_serah.alamat_bawaan` | `sales_orders.shipping_address` → `customers.alamat` | alamat bawaan Pengiriman (serah) |
| `routers/sales_orders.py:619, 842, 865` + `so_riwayat`, `so_ubah_terkonfirmasi` | `shipping_address` (teks) | SO header, riwayat, ubah terkonfirmasi |
| `services/unified_agent/*` (orchestrator, registry, extractor) | `alamat`/`address` | chat agent (ekstraksi/eksekusi) |

Pola cetak sekarang: **satu string**. Tidak ada format terpusat (penawaran menempel kota; faktur/SJ tidak).

### 1.3 FE (repo `milkyhoop`, main) — temuan

- `types/customer.ts` sudah punya tipe `Address` dan `shipping_address: Address`.
- `Customer/AddCustomerForm` (mobile) punya lembar **BillingAddressSheet** dan **ShippingAddressSheet**.
- **TEMUAN (kelas "UI menjanjikan lebih dari kenyataan", Iron Law 34):** payload simpan di
  `AddCustomerForm/hooks/useCustomerFormState.ts:133-158` hanya mengirim `address/city/province/postal_code`.
  `shipping_address` yang diisi pengguna di lembar Alamat Pengiriman **TIDAK PERNAH dikirim** — hilang diam-diam,
  dan BE pun tak punya tempat menyimpannya. Dicatat; perbaikan = bagian unit ini (FE mengirim ke API baru).
- CW `form/master/skemaPelanggan.ts`, `SalesOrder/useSalesOrderForm.ts`, `Pengiriman/*` membaca alamat teks.

---

## 2. Acuan: NetSuite & SAP

### 2.1 Pemetaan medan

| Konsep | NetSuite (Customer → `addressbook` sublist, subrecord `addressbookaddress`) | SAP S/4HANA (Business Partner, ADRC) | MilkyHoop usulan (`customer_addresses`) |
|---|---|---|---|
| Banyak alamat per pelanggan | ya (baris addressbook) | ya (BUT020 → ADRC) | ya (baris) |
| Label | `label` | address usage / keterangan | `label` |
| Penerima (Up.) | `attention` | `NAME_CO` (c/o) | `attention` |
| Nama di alamat | `addressee` | `NAME1..4` | `addressee` |
| Telepon alamat | `addrphone` | `TEL_NUMBER` (ADR2) | `phone` |
| Jalan baris 1 | `addr1` | `STREET` + `HOUSE_NUM1` | `addr1` |
| Jalan baris 2 | `addr2` | `STR_SUPPL1` | `addr2` |
| Jalan baris 3 | `addr3` | `STR_SUPPL2/3` | `addr3` (kelurahan/kecamatan bila mau) |
| Kota/kabupaten | `city` | `CITY1` | `city` |
| Kecamatan/distrik | (pakai addr2/3) | `CITY2` (district) | (pakai addr2/3 — generik, lihat §6 P2) |
| Provinsi | `state` | `REGION` | `state` |
| Kode pos | `zip` | `POST_CODE1` | `zip` |
| Negara | `country` | `COUNTRY` | `country` (ISO 3166 alpha-3, sama dengan `customers.negara`) |
| Teks tercetak | `addrtext` (turunan, read-only) + `override` | address format by country | turunan `format_alamat()`; tak disimpan |
| Bawaan tagih | `defaultbilling` | usage BILL_TO, `XXDEFAULT` | `is_default_billing` |
| Bawaan kirim | `defaultshipping` | usage SHIP_TO | `is_default_shipping` |
| Lokasi usaha pajak | — (lokalisasi ID: per subsidiary) | — | `nitku` (Coretax: NPWP + 6 digit tempat usaha) — opsional, §6 P3 |
| Di transaksi | `billaddresslist`/`shipaddresslist` (pilih dari addressbook) + subrecord `billingaddress`/`shippingaddress` = **SALINAN** (boleh alamat custom satu kali) | partner function sold-to (AG) / ship-to (WE) / bill-to (RE); alamat dokumen = salinan, boleh one-time address | `*_address_id` (asal) + teks snapshot yang ADA SEKARANG (`shipping_address`) |

### 2.2 Aturan yang diambil (bawaan ikut NetSuite/SAP, putusan 11 Okt)
1. **Banyak alamat per pelanggan**, tepat satu bawaan tagih dan tepat satu bawaan kirim (boleh baris yang sama).
2. **Dokumen menyimpan SALINAN**. Mengubah alamat pelanggan TIDAK mengubah dokumen lama (Law 19 semangatnya:
   dokumen terbit = beku). Sama dengan NetSuite (subrecord transaksi) dan SAP (alamat dokumen).
3. **Alamat satu kali** (custom/one-time) di dokumen tetap boleh: teks dokumen tanpa `address_id`.
4. **Teks cetak = turunan** satu fungsi format (NetSuite `addrtext`), bukan kolom yang disunting terpisah.

---

## 3. Skema usulan — migrasi V428

```sql
-- V428__customer_addresses.sql  (BELUM DIBUAT; sesudah GO pemilik)
CREATE TABLE customer_addresses (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           text NOT NULL REFERENCES "Tenant"(id) ON DELETE CASCADE,
    customer_id         uuid NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    label               varchar(100),
    attention           varchar(255),
    addressee           varchar(255),
    phone               varchar(50),
    addr1               text,
    addr2               text,
    addr3               text,
    city                varchar(100),
    state               varchar(100),
    zip                 varchar(20),
    country             char(3),               -- ISO alpha-3 seperti customers.negara; NULL = tak diketahui (tanpa tebakan)
    is_default_billing  boolean NOT NULL DEFAULT false,
    is_default_shipping boolean NOT NULL DEFAULT false,
    is_active           boolean NOT NULL DEFAULT true,
    sumber              varchar(20) NOT NULL DEFAULT 'pengguna',  -- 'pengguna' | 'migrasi_v428'
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    created_by          text,
    updated_by          text,
    CONSTRAINT ca_isi CHECK (COALESCE(btrim(addr1),'') <> '' OR COALESCE(btrim(city),'') <> ''
                             OR COALESCE(btrim(state),'') <> '' OR COALESCE(btrim(zip),'') <> ''),
    CONSTRAINT ca_negara CHECK (country IS NULL OR country ~ '^[A-Z]{3}$'),
    CONSTRAINT ca_bawaan_aktif CHECK (is_active OR NOT (is_default_billing OR is_default_shipping))
);
CREATE INDEX idx_ca_pelanggan ON customer_addresses (tenant_id, customer_id);
-- tepat satu bawaan per jenis per pelanggan (ditegakkan DB, bukan konvensi kode — pola Iron Law "invarian di DB")
CREATE UNIQUE INDEX uq_ca_bawaan_tagih ON customer_addresses (customer_id) WHERE is_default_billing;
CREATE UNIQUE INDEX uq_ca_bawaan_kirim ON customer_addresses (customer_id) WHERE is_default_shipping;

-- asal alamat di dokumen (NULL = alamat satu kali / dokumen lama). Teks snapshot TETAP kolom yang ada.
ALTER TABLE sales_orders ADD COLUMN shipping_address_id uuid REFERENCES customer_addresses(id) ON DELETE SET NULL;
```

Catatan desain:
- Tenant dicek eksplisit di setiap kueri (BYPASSRLS; `set_config` bukan pagar). FK ke `customers` saja tak
  menjamin tenant sama → penulis memverifikasi `customers.tenant_id = tenant_id` dalam transaksi yang sama;
  usul tambahan: trigger `trg_ca_tenant_sama` (BEFORE INSERT/UPDATE) yang menolak tenant beda — predikat atas
  baris DB ditegakkan di DB.
- Tak ada `kecamatan`/`kelurahan` khusus: solusi generik semua usaha (putusan 14 Okt); Indonesia memakai
  `addr2`/`addr3`. Lihat §6 P2 bila pemilik ingin medan eksplisit.
- Hapus = `is_active=false` (alamat yang sudah dirujuk dokumen tetap ada). Bawaan tak boleh nonaktif (CHECK).
- `vendor_addresses` (0 baris) TIDAK digabung di unit ini; bentuk medannya beda (`street`/`province`/
  `postal_code`). Usul penyelarasan pemasok = tiket terpisah sesudah ini (§6 P4).

### 3.1 Migrasi data lama (di V428, transaksi yang sama)
Aturan: **teks bebas → `addr1` APA ADANYA, tanpa tebakan** (tak memecah kota/kode pos dari teks).

```sql
INSERT INTO customer_addresses (tenant_id, customer_id, addr1, city, state, zip, country,
                                is_default_billing, is_default_shipping, sumber)
SELECT c.tenant_id, c.id,
       NULLIF(c.alamat, ''),                 -- apa adanya (tanpa trim isi; baris kosong -> NULL)
       NULLIF(c.city, ''), NULLIF(c.province, ''), NULLIF(c.postal_code, ''),
       c.negara,                              -- NULL tetap NULL (tanpa menebak IDN)
       true, true, 'migrasi_v428'
FROM customers c
WHERE COALESCE(btrim(c.alamat),'') <> '' OR COALESCE(btrim(c.city),'') <> ''
   OR COALESCE(btrim(c.province),'') <> '' OR COALESCE(btrim(c.postal_code),'') <> '';
```
- Perkiraan baris: ≤ 10 untuk seluruh DB (7 beralamat + beberapa yang hanya kota/provinsi). Termasuk pelanggan
  `deleted_at` (riwayat tetap bisa dicetak).
- `customers.address` (0 terisi) tidak dimigrasi; kolom dibiarkan (hapus = tiket terpisah, pembacanya di
  `quotes.py` dialihkan dulu).
- **SO lama**: `shipping_address_id` diisi HANYA bila `btrim(shipping_address) = btrim(alamat)` pelanggannya
  (terukur kaos 16 SO, pembanding sama) → tautan ke baris migrasi; 3 SO berbeda + semua lainnya tetap NULL
  (alamat satu kali). Teks snapshot tidak diubah.
- ROLLBACK: `V428__customer_addresses_ROLLBACK.sql` = DROP kolom SO + DROP tabel (data migrasi berasal dari
  kolom yang TIDAK diubah, jadi rollback tanpa kehilangan data).
- Uji pasangan migrasi: untuk setiap pelanggan, `format_alamat(bawaan_tagih)` == teks lama yang dicetak
  sebelum V428 (faktur/SJ: `alamat`; penawaran: `alamat_pelanggan(alamat, address, city)`) — byte-identik.

---

## 4. Kontrak API (kompatibel ke belakang)

### 4.1 Kolom datar lama = CERMIN alamat bawaan tagih
`customers.alamat/city/province/postal_code` **tetap ada dan tetap ditulis**, sebagai cermin baris
`is_default_billing`. Satu fungsi penulis (`services/alamat_pelanggan.simpan_bawaan_tagih`) menulis keduanya dalam
transaksi yang sama. Akibatnya **semua pembaca lama di §1.2 tak berubah** pada tahap 1 — tak ada respons lama yang
bergeser. Pembaca dialihkan ke fungsi format satu per satu di tahap 2 (masing-masing uji lama == baru).

### 4.2 Respons
- `GET /api/customers/{id}`: medan lama (`address`, `city`, `province`, `postal_code`, `negara`) **tetap** +
  BARU: `addresses: [{id, label, attention, addressee, phone, addr1, addr2, addr3, city, state, zip, country,
  is_default_billing, is_default_shipping, text}]` (aktif saja; `text` = `format_alamat`),
  `default_billing_address_id`, `default_shipping_address_id`.
- `GET /api/customers` (daftar): tidak berubah (alamat tunggal sudah ada lewat `address`).
- `GET /api/sales-orders/{id}`: `shipping_address` (teks) tetap + BARU `shipping_address_id`.

### 4.3 Tulis (rute baru → `inventaris_rute_tulis.txt` + keputusan izin modul pelanggan)
| Rute | Perilaku |
|---|---|
| `POST /api/customers/{id}/addresses` | tambah satu; `is_default_*=true` memindahkan bawaan (satu tx) |
| `PATCH /api/customers/{id}/addresses/{address_id}` | ubah medan yang DIKIRIM saja (bukan ganti-seluruhnya) |
| `POST /api/customers/{id}/addresses/{address_id}/default` | `{billing?: bool, shipping?: bool}` |
| `DELETE /api/customers/{id}/addresses/{address_id}` | nonaktif; tolak 409 `ALAMAT_BAWAAN` bila masih bawaan |
| `POST/PUT /api/customers` (lama) | `address/city/province/postal_code` → upsert baris bawaan tagih (cermin) |
| `POST /api/customers` (baru, opsional) | `shipping_address: {..}` → baris bawaan kirim (memperbaiki temuan FE §1.3) |

Tidak ada endpoint "ganti seluruh daftar alamat" — kelas kehilangan data "penulis menganggap yang tampil =
segalanya" (memori 22 Sep) dihindari dengan operasi per baris. Audit: riwayat pelanggan mencatat tambah/ubah/
nonaktif/pindah bawaan (label "Alamat").

### 4.4 Format teks (satu sumber)
`services/alamat_pelanggan.format_alamat(a) -> str`: `addr1, addr2, addr3, city state zip` (+ nama negara bila
`country` bukan negara tenant), bagian kosong dilewati, tanpa menggandakan kota yang sudah termuat di `addr1`
(aturan `penawaran_cetak.alamat_pelanggan` sekarang). Dipakai PDF, e-Faktur `alamat_pembeli`, `alamat_bawaan`
pengiriman, dan medan `text`.

### 4.5 Dokumen
- SO: form memilih `shipping_address_id` (bawaan kirim pelanggan sebagai nilai awal) → server menyalin
  `format_alamat` ke `shipping_address` (snapshot). Teks yang diketik bebas = alamat satu kali (`_id` NULL).
  SO terkonfirmasi: aturan ubah tetap `so_ubah_terkonfirmasi.HEADER_BEBAS`.
- Pengiriman serah `alamat_bawaan`: SO `shipping_address` → **bawaan kirim pelanggan** → bawaan tagih → None.
- Faktur/SJ/penawaran PDF: tetap teks pelanggan (bawaan tagih) di tahap 1; SJ beralih ke snapshot SO di tahap 2.
- e-Faktur `alamat_pembeli` = `format_alamat(bawaan tagih)` (sama dengan tahap 1 karena cermin).

---

## 5. Dampak FE

1. Form pelanggan (mobile `AddCustomerForm`, desktop, CW `skemaPelanggan`): kirim `shipping_address` (perbaiki
   temuan §1.3); medan datar lama tetap jalan.
2. Detail pelanggan: daftar alamat + tandai bawaan tagih/kirim + tambah/ubah/nonaktif (keyboard-first untuk CW).
3. Form SO (CW + tradisional): pemilih alamat kirim dari buku alamat, nilai awal bawaan kirim, opsi "alamat
   lain" (teks bebas = satu kali).
4. Tipe: `types/customer.ts` `Address` diselaraskan ke medan kontrak (`addr1..3/city/state/zip/country`);
   OpenAPI ditulis tangan → diperbarui manual.
5. Tak ada perubahan wajib FE di tahap 1 selain (1): respons lama identik.

---

## 6. Pertanyaan untuk pemilik (putusan sebelum V428)

- **P1** GO V428 + tahap 1 (tabel + migrasi + API alamat + cermin kolom datar + `format_alamat`)?
- **P2** Medan Indonesia eksplisit `kecamatan`/`kelurahan`? Usul: TIDAK (generik, `addr2/addr3`; sama dengan NetSuite).
- **P3** `nitku` per alamat (Coretax tempat usaha pembeli bercabang) masuk V428 sebagai kolom opsional, atau
  menunggu unit e-Faktur #3(a)? Usul: tunda ke #3(a) (identitas pajak V420 sudah punya `nitku` per pelanggan).
- **P4** Selaraskan `vendor_addresses` (0 baris) ke bentuk yang sama? Usul: tiket terpisah sesudah tahap 1.

## 7. Tahapan & gerbang (sesudah GO)
1. **Tahap 1 (BE)**: V428 + `services/alamat_pelanggan` + rute §4.3 + cermin; uji pasangan: semua respons lama
   byte-identik (harness lama == baru seperti LISTPAGE-DATA), migrasi `format_alamat == teks lama`, indeks unik
   bawaan menolak dua bawaan (kontrol merah), tenant beda ditolak. Rute → inventaris + izin.
2. **Tahap 2 (BE)**: pembaca §1.2 dialihkan ke `format_alamat`/bawaan kirim satu per satu, tiap pembaca uji lama == baru.
3. **FE**: §5 (serah ke FRONTEND/WORKSPACE).
Rilis BE = izin pemilik langsung + `window_item.sh`; nyata kaos (tulis 'TES E2E' dalam savepoint); grapgrap baca saja.
