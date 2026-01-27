# MilkyHoop ERP - Field Mapping Audit Report
## 5 Modul: Produk, Vendor, Faktur Pembelian, Faktur Penjualan, Biaya

**Tanggal Audit:** 2026-01-16
**Auditor:** Claude (AI) - Akuntan Senior
**Standar Referensi:** SAK EMKM, PSAK, Regulasi DJP 2025
**Software Referensi:** Zoho Books, Accurate, Jurnal.id

---

## Executive Summary

| Modul | Frontend | Backend | Sinkronisasi | Tax Compliance | Prioritas |
|-------|----------|---------|--------------|----------------|-----------|
| 1. Produk & Jasa | ✅ Complete | ✅ Complete | ⚠️ Minor gaps | ✅ Compliant | LOW |
| 2. Vendor | ⚠️ Partial | ⚠️ Partial | ❌ Major gaps | ⚠️ Review | HIGH |
| 3. Faktur Pembelian | ✅ Complete | ✅ Complete | ✅ Synced | ✅ Compliant | LOW |
| 4. Faktur Penjualan | ✅ Complete | ✅ Complete | ✅ Synced | ⚠️ Review | MEDIUM |
| 5. Biaya & Pengeluaran | ✅ Complete | ❌ Missing | ❌ Not synced | ❌ Incomplete | CRITICAL |

---

# Modul 1: Produk & Jasa (Items)

```yaml
module: produk_jasa
status: COMPLETE

field_review:
  # === BASIC INFO ===
  - field: name / nama_produk
    frontend: "name: string (required, max 100)"
    backend: "nama_produk VARCHAR(100) NOT NULL"
    standar_akuntansi: "Nama barang/jasa harus jelas dan deskriptif"
    status: ✅ MATCH
    catatan: "Nama unik per tenant"

  - field: item_type
    frontend: "'goods' | 'service'"
    backend: "item_type VARCHAR(20) CHECK IN ('goods', 'service')"
    standar_akuntansi: "SAK EMKM membedakan persediaan barang dagang vs jasa"
    status: ✅ MATCH
    catatan: "Menentukan perlakuan akuntansi (inventory vs expense)"

  - field: track_inventory
    frontend: "trackInventory: boolean (default true)"
    backend: "track_inventory BOOLEAN DEFAULT true"
    standar_akuntansi: "Persediaan wajib dicatat untuk barang dagang (SAK EMKM 2105)"
    status: ✅ MATCH
    catatan: "Auto-false untuk service type"

  - field: base_unit / satuan
    frontend: "baseUnit: string (required)"
    backend: "satuan VARCHAR(50) NOT NULL"
    standar_akuntansi: "Satuan dasar wajib untuk pencatatan persediaan"
    status: ✅ MATCH
    catatan: "Default units: Pcs, Box, Karton, Lusin, Pack, Kg, Gram, Liter"

  - field: barcode
    frontend: "barcode?: string (EAN-13 format)"
    backend: "barcode VARCHAR(50) UNIQUE per tenant"
    standar_akuntansi: "Opsional, untuk integrasi POS"
    status: ✅ MATCH
    catatan: "13 digit EAN-13 format"

  - field: kategori / category
    frontend: "kategori?: string"
    backend: "kategori VARCHAR(100)"
    standar_akuntansi: "Opsional, untuk grouping laporan"
    status: ✅ MATCH
    catatan: null

  - field: deskripsi / description
    frontend: "deskripsi?: string"
    backend: "deskripsi TEXT"
    standar_akuntansi: "Opsional"
    status: ✅ MATCH
    catatan: null

  - field: is_returnable
    frontend: "isReturnable: boolean (default true)"
    backend: "is_returnable BOOLEAN DEFAULT true"
    standar_akuntansi: "Retur pembelian/penjualan (SAK EMKM)"
    status: ✅ MATCH
    catatan: "Auto-false untuk service"

  # === PRICING ===
  - field: sales_price / harga_jual
    frontend: "salesPrice?: number (>= 0)"
    backend: "harga_jual DECIMAL(15,2), sales_price FLOAT"
    standar_akuntansi: "Harga jual dalam Rupiah (tanpa desimal untuk IDR)"
    status: ⚠️ REVIEW
    catatan: "Backend pakai DECIMAL dan FLOAT - standardize ke BIGINT untuk Rupiah"

  - field: purchase_price
    frontend: "purchasePrice?: number (>= 0)"
    backend: "purchase_price FLOAT"
    standar_akuntansi: "Harga beli untuk kalkulasi HPP"
    status: ⚠️ REVIEW
    catatan: "Sebaiknya BIGINT untuk konsistensi dengan modul lain"

  # === ACCOUNT MAPPING ===
  - field: sales_account
    frontend: "salesAccount: string (default 'Sales')"
    backend: "sales_account VARCHAR(100) DEFAULT 'Sales'"
    standar_akuntansi: |
      SAK EMKM: Pendapatan (4-xxxxx)
      - Penjualan Barang Dagang (4-10100)
      - Pendapatan Jasa (4-10200)
    status: ⚠️ REVIEW
    catatan: "Sebaiknya link ke CoA ID, bukan string"

  - field: purchase_account
    frontend: "purchaseAccount: string (default 'Cost of Goods Sold')"
    backend: "purchase_account VARCHAR(100) DEFAULT 'Cost of Goods Sold'"
    standar_akuntansi: |
      SAK EMKM:
      - HPP Barang Dagang (5-10100) untuk goods
      - Beban Langsung (5-xxxxx) untuk service
    status: ⚠️ REVIEW
    catatan: "Sebaiknya link ke CoA ID, bukan string"

  # === TAX SETTINGS ===
  - field: sales_tax
    frontend: |
      options: ['', 'PPN_11', 'PPN_12']
      service adds: ['PPH_23_2', 'PPH_23_15']
    backend: "sales_tax VARCHAR(50)"
    standar_akuntansi: |
      PPN 2025: 12% (umum), 11% (tertentu), 0% (ekspor)
      PPh 23: 2% (jasa), 15% (dividen/royalti)
    status: ✅ MATCH
    catatan: "Tax options sudah sesuai regulasi 2025"

  - field: purchase_tax
    frontend: "Same options as sales_tax"
    backend: "purchase_tax VARCHAR(50)"
    standar_akuntansi: |
      PPN Masukan: 11-12%
      PPh 23 dipotong dari vendor: 2%
    status: ✅ MATCH
    catatan: null

  # === UNIT CONVERSIONS ===
  - field: conversions / unit_conversions
    frontend: |
      conversions[]: {
        conversion_unit: string,
        conversion_factor: int (1-10000),
        purchase_price?: number,
        sales_price?: number
      }
    backend: |
      unit_conversions table: {
        base_unit, conversion_unit, conversion_factor,
        purchase_price, sales_price
      }
    standar_akuntansi: "Konversi satuan untuk pembelian grosir vs penjualan eceran"
    status: ✅ MATCH
    catatan: "Hanya untuk goods, tidak untuk service"

missing_fields:
  - field: inventory_account_id
    reason: "SAK EMKM: Persediaan Barang Dagang (1-10400) harus di-link"
    priority: MEDIUM

  - field: reorder_level
    reason: "Best practice: titik pemesanan ulang untuk inventory management"
    priority: LOW

  - field: preferred_vendor_id
    reason: "Best practice: vendor utama untuk auto-PO"
    priority: LOW

  - field: sku
    reason: "Internal stock keeping unit, berbeda dari barcode"
    priority: LOW

  - field: weight / dimensions
    reason: "Untuk integrasi pengiriman"
    priority: LOW

extra_fields:
  - field: content_unit, wholesale_unit, units_per_wholesale
    reason: "Duplikasi dengan unit_conversions table - bisa dihapus"

tax_compliance:
  - item: "PPN Rate Options"
    status: ✅ COMPLIANT
    catatan: "11% dan 12% sudah tersedia sesuai PMK 2025"

  - item: "PPh 23 untuk Jasa"
    status: ✅ COMPLIANT
    catatan: "2% (jasa umum) dan 15% (dividen/royalti) tersedia"

  - item: "Tax-inclusive pricing"
    status: ⚠️ REVIEW
    catatan: "Belum ada flag tax_inclusive di level produk"

recommendations:
  - priority: MEDIUM
    description: "Standardize harga ke BIGINT (Rupiah tanpa desimal)"
    affected: backend

  - priority: MEDIUM
    description: "Link sales_account dan purchase_account ke CoA ID, bukan string"
    affected: both

  - priority: LOW
    description: "Tambah inventory_account_id untuk aset persediaan"
    affected: both

  - priority: LOW
    description: "Hapus fields duplikat: content_unit, wholesale_unit, units_per_wholesale"
    affected: backend
```

---

# Modul 2: Vendor (Pemasok)

```yaml
module: vendor
status: PARTIAL - MAJOR GAPS

field_review:
  # === BASIC INFO ===
  - field: name / nama
    frontend: "nama: string (required)"
    backend: "name VARCHAR(255) NOT NULL"
    standar_akuntansi: "Nama vendor wajib untuk dokumen transaksi"
    status: ✅ MATCH
    catatan: null

  - field: company / perusahaan
    frontend: "perusahaan: string"
    backend: "NOT IN SCHEMA"
    standar_akuntansi: "Nama perusahaan untuk PT/CV"
    status: ❌ MISMATCH
    catatan: "Frontend collect, backend tidak simpan"

  - field: display_name
    frontend: "displayName: string"
    backend: "NOT IN SCHEMA"
    standar_akuntansi: "Nama tampilan di faktur/dokumen"
    status: ❌ MISMATCH
    catatan: "Frontend collect, backend tidak simpan"

  - field: code
    frontend: "NOT IN FORM"
    backend: "code VARCHAR(50)"
    standar_akuntansi: "Kode vendor untuk referensi internal"
    status: ⚠️ REVIEW
    catatan: "Backend ada, frontend tidak ada input"

  # === CONTACT INFO ===
  - field: email
    frontend: "email: string"
    backend: "email VARCHAR(255)"
    standar_akuntansi: "Opsional"
    status: ✅ MATCH
    catatan: null

  - field: phone / telepon
    frontend: "telepon: string (landline)"
    backend: "phone VARCHAR(50)"
    standar_akuntansi: "Opsional"
    status: ✅ MATCH
    catatan: null

  - field: handphone / mobile
    frontend: "handphone: string"
    backend: "NOT IN SCHEMA"
    standar_akuntansi: "Opsional"
    status: ❌ MISMATCH
    catatan: "Frontend collect, backend tidak simpan"

  - field: website
    frontend: "website: string"
    backend: "NOT IN SCHEMA"
    standar_akuntansi: "Opsional"
    status: ❌ MISMATCH
    catatan: "Frontend collect, backend tidak simpan"

  # === TAX INFO (CRITICAL) ===
  - field: vendor_type / jenis_vendor
    frontend: |
      jenisVendor: 'BADAN' | 'ORANG_PRIBADI'
      (Badan Usaha vs Pribadi)
    backend: "NOT IN SCHEMA"
    standar_akuntansi: |
      KRITIS untuk PPh 23:
      - BADAN: PPh 23 = 2% dari DPP
      - ORANG_PRIBADI tanpa NPWP: PPh 23 = 4% (200% tarif normal)
      - ORANG_PRIBADI dengan NPWP: PPh 23 = 2%
    status: ❌ MISMATCH
    catatan: "CRITICAL GAP - Wajib untuk withholding tax"

  - field: npwp
    frontend: "npwp: string (15/16 digit)"
    backend: "tax_id VARCHAR(50)"
    standar_akuntansi: |
      NPWP Format 2025:
      - Badan: 15 digit (XX.XXX.XXX.X-XXX.XXX)
      - Pribadi: 16 digit NIK atau NPWP 15 digit
    status: ⚠️ REVIEW
    catatan: "Field name berbeda (npwp vs tax_id)"

  - field: nik
    frontend: "nik: string (for ORANG_PRIBADI)"
    backend: "NOT IN SCHEMA"
    standar_akuntansi: |
      NIK 16 digit untuk vendor ORANG_PRIBADI
      Mulai 2024: NIK = NPWP untuk orang pribadi
    status: ❌ MISMATCH
    catatan: "Frontend collect untuk pribadi, backend tidak simpan"

  - field: is_pkp / isPkp
    frontend: "isPkp: boolean"
    backend: "NOT IN SCHEMA"
    standar_akuntansi: |
      PKP (Pengusaha Kena Pajak):
      - Wajib untuk vendor dengan omzet > 4.8M/tahun
      - Menentukan apakah PPN bisa dikreditkan
      - e-Faktur hanya dari PKP
    status: ❌ MISMATCH
    catatan: "CRITICAL GAP - Wajib untuk PPN krediting"

  - field: tarif_pajak / tax_rate
    frontend: |
      tarifPajak: 'NONE' | 'PPN_11' | 'PPN_12' |
                  'PPH_21' | 'PPH_23' | 'PPH_4_2'
    backend: "NOT IN SCHEMA"
    standar_akuntansi: |
      Tarif potong default vendor:
      - PPh 23: 2% (jasa), 4% (tanpa NPWP)
      - PPh 4(2): Final (sewa tanah/bangunan)
      - PPh 21: Untuk vendor tenaga kerja
    status: ❌ MISMATCH
    catatan: "Frontend collect, backend tidak simpan"

  # === FINANCIAL INFO ===
  - field: payment_terms
    frontend: |
      syaratPembayaran: 'DUE_ON_RECEIPT' | 'NET_15' |
                        'NET_30' | 'NET_45' | 'NET_60' | 'NET_90'
    backend: "payment_terms_days INTEGER DEFAULT 30"
    standar_akuntansi: "Standar Indonesia: COD, Net 7, Net 14, Net 30, Net 60"
    status: ⚠️ REVIEW
    catatan: "Frontend pakai enum, backend pakai integer days"

  - field: currency / mata_uang
    frontend: "mataUang: 'IDR' | 'USD' | 'SGD' | 'EUR' | 'MYR'"
    backend: "NOT IN SCHEMA"
    standar_akuntansi: "Multi-currency untuk vendor impor"
    status: ❌ MISMATCH
    catatan: "Frontend collect, backend tidak simpan"

  - field: opening_balance / saldo_awal
    frontend: "saldoAwal: number"
    backend: "NOT IN SCHEMA"
    standar_akuntansi: |
      Saldo awal hutang vendor saat migrasi
      SAK EMKM: Hutang Usaha (2-10100)
    status: ❌ MISMATCH
    catatan: "Frontend collect, backend tidak simpan"

  - field: credit_limit
    frontend: "NOT IN FORM"
    backend: "credit_limit BIGINT"
    standar_akuntansi: "Batas kredit vendor (opsional)"
    status: ⚠️ REVIEW
    catatan: "Backend ada, frontend tidak ada input"

  # === ADDRESS ===
  - field: address
    frontend: |
      alamatPenagihan: { street, city, province, postcode }
      alamatPengiriman: { street, city, province, postcode }
    backend: |
      address TEXT (single field)
      city VARCHAR(100)
      province VARCHAR(100)
      postal_code VARCHAR(20)
    standar_akuntansi: "Alamat untuk dokumen tagihan"
    status: ⚠️ REVIEW
    catatan: "Frontend punya 2 alamat, backend hanya 1"

  # === CONTACT PERSONS ===
  - field: contacts
    frontend: |
      contacts[]: {
        name, position, email, phone
      }
    backend: "contact_person VARCHAR(255) - SINGLE FIELD"
    standar_akuntansi: "Multiple kontak untuk perusahaan besar"
    status: ❌ MISMATCH
    catatan: "Frontend support multiple, backend hanya 1"

missing_fields:
  - field: vendor_type (BADAN/ORANG_PRIBADI)
    reason: |
      CRITICAL untuk PPh 23 withholding:
      - Badan: 2% PPh 23
      - Orang Pribadi tanpa NPWP: 4% PPh 23
      - Orang Pribadi dengan NPWP: 2% PPh 23
    priority: HIGH

  - field: is_pkp (PKP status)
    reason: |
      CRITICAL untuk PPN:
      - Hanya PPN dari PKP yang bisa dikreditkan
      - e-Faktur wajib dari vendor PKP
    priority: HIGH

  - field: default_expense_account_id
    reason: "Auto-fill akun beban saat buat tagihan"
    priority: MEDIUM

  - field: bank_account_info
    reason: "Nomor rekening untuk pembayaran"
    priority: MEDIUM

extra_fields: []

tax_compliance:
  - item: "NPWP Format"
    status: ⚠️ REVIEW
    catatan: |
      Validasi format NPWP:
      - 15 digit: XX.XXX.XXX.X-XXX.XXX (badan)
      - 16 digit: NIK (orang pribadi sejak 2024)

  - item: "PPh 23 Withholding"
    status: ❌ NON-COMPLIANT
    catatan: |
      TIDAK BISA dihitung otomatis karena:
      - vendor_type tidak tersimpan
      - tarif_pajak tidak tersimpan
      Backend HARUS menambah field ini

  - item: "PKP Status untuk PPN"
    status: ❌ NON-COMPLIANT
    catatan: |
      PPN Masukan hanya bisa dikreditkan dari vendor PKP.
      Tanpa is_pkp field, tidak bisa validasi.

  - item: "e-Faktur Integration"
    status: ❌ NON-COMPLIANT
    catatan: |
      Untuk e-Faktur Masukan perlu:
      - NPWP vendor
      - Status PKP
      - Nomor Seri Faktur Pajak

recommendations:
  - priority: HIGH
    description: |
      URGENT: Tambah kolom ke vendors table:
      - vendor_type VARCHAR(20) CHECK IN ('BADAN', 'ORANG_PRIBADI', 'LUAR_NEGERI')
      - nik VARCHAR(20)
      - is_pkp BOOLEAN DEFAULT false
      - default_tax_rate VARCHAR(20)
    affected: backend

  - priority: HIGH
    description: |
      Tambah tabel vendor_addresses untuk multiple address:
      - vendor_id, address_type (billing/shipping),
        street, city, province, postal_code
    affected: backend

  - priority: HIGH
    description: |
      Tambah tabel vendor_contacts untuk multiple contacts:
      - vendor_id, name, position, email, phone, is_primary
    affected: backend

  - priority: MEDIUM
    description: "Tambah currency dan opening_balance ke vendors table"
    affected: backend

  - priority: MEDIUM
    description: "Tambah company_name dan display_name ke vendors table"
    affected: backend

  - priority: LOW
    description: "Tambah credit_limit field ke frontend form"
    affected: frontend
```

---

# Modul 3: Faktur Pembelian (Bills)

```yaml
module: faktur_pembelian
status: COMPLETE - WELL SYNCED

field_review:
  # === HEADER ===
  - field: vendor_id
    frontend: "vendorId: UUID (from VendorSheet)"
    backend: "vendor_id UUID FK vendors"
    standar_akuntansi: "Link ke master vendor"
    status: ✅ MATCH
    catatan: null

  - field: vendor_name
    frontend: "vendorName: string (auto-fill or manual)"
    backend: "vendor_name VARCHAR(255) NOT NULL"
    standar_akuntansi: "Nama vendor tersimpan untuk audit trail"
    status: ✅ MATCH
    catatan: "Support auto-create vendor jika vendor_id null"

  - field: invoice_number
    frontend: "invoiceNumber?: string (nomor faktur vendor)"
    backend: "invoice_number VARCHAR(50) NOT NULL"
    standar_akuntansi: "Nomor faktur dari vendor untuk referensi"
    status: ✅ MATCH
    catatan: "Auto-generate jika kosong"

  - field: ref_no
    frontend: "referenceNumber?: string"
    backend: "ref_no VARCHAR(100)"
    standar_akuntansi: "Nomor referensi tambahan (PO number, dll)"
    status: ✅ MATCH
    catatan: null

  - field: issue_date / bill_date
    frontend: "transactionDate: string (YYYY-MM-DD)"
    backend: "issue_date DATE NOT NULL"
    standar_akuntansi: "Tanggal faktur untuk pengakuan hutang"
    status: ✅ MATCH
    catatan: null

  - field: due_date
    frontend: "dueDate: string (YYYY-MM-DD)"
    backend: "due_date DATE NOT NULL"
    standar_akuntansi: "Tanggal jatuh tempo untuk AP aging"
    status: ✅ MATCH
    catatan: "Default: issue_date + 30 days"

  - field: tax_inclusive
    frontend: "taxInclusive: boolean"
    backend: "tax_inclusive BOOLEAN DEFAULT false"
    standar_akuntansi: |
      Harga sudah termasuk PPN atau belum.
      DPP harus dihitung mundur jika inclusive.
    status: ✅ MATCH
    catatan: null

  - field: notes
    frontend: "notes?: string"
    backend: "notes TEXT"
    standar_akuntansi: "Catatan internal"
    status: ✅ MATCH
    catatan: null

  # === LINE ITEMS ===
  - field: product_id
    frontend: "productId?: UUID"
    backend: "product_id UUID FK products"
    standar_akuntansi: "Link ke master produk"
    status: ✅ MATCH
    catatan: null

  - field: product_name
    frontend: "productName: string (required)"
    backend: "product_name VARCHAR(255)"
    standar_akuntansi: "Nama produk untuk display"
    status: ✅ MATCH
    catatan: null

  - field: qty / quantity
    frontend: "quantity: number (> 0)"
    backend: "quantity DECIMAL(10,2) NOT NULL"
    standar_akuntansi: "Jumlah item"
    status: ✅ MATCH
    catatan: null

  - field: unit
    frontend: "unit: string"
    backend: "unit VARCHAR(20)"
    standar_akuntansi: "Satuan ukur"
    status: ✅ MATCH
    catatan: null

  - field: price / unit_price
    frontend: "pricePerUnit: number (integer Rupiah)"
    backend: "unit_price BIGINT NOT NULL"
    standar_akuntansi: "Harga per unit dalam Rupiah"
    status: ✅ MATCH
    catatan: "Konsisten BIGINT"

  - field: discount_percent (item level)
    frontend: "discountPercent: number (0-100)"
    backend: "discount_percent DECIMAL(5,2) DEFAULT 0"
    standar_akuntansi: "Diskon per item dalam persen"
    status: ✅ MATCH
    catatan: null

  # === PHARMACY EXTENSION ===
  - field: batch_no
    frontend: "batch?: string"
    backend: "batch_no VARCHAR(100)"
    standar_akuntansi: "Nomor batch untuk traceability (BPOM)"
    status: ✅ MATCH
    catatan: "Pharmacy/FMCG specific"

  - field: exp_date
    frontend: "expiryDate?: string (YYYY-MM)"
    backend: "exp_date DATE"
    standar_akuntansi: "Tanggal kadaluarsa (BPOM compliance)"
    status: ✅ MATCH
    catatan: "Stored as first of month"

  - field: bonus_qty
    frontend: "bonusQty?: number"
    backend: "bonus_qty INTEGER DEFAULT 0"
    standar_akuntansi: "Barang gratis, tidak masuk kalkulasi harga"
    status: ✅ MATCH
    catatan: "Pharmacy promotion handling"

  # === DISCOUNTS (MULTI-LEVEL) ===
  - field: invoice_discount_percent
    frontend: "invoiceDiscountPercent: number"
    backend: "invoice_discount_percent DECIMAL(5,2) DEFAULT 0"
    standar_akuntansi: |
      Diskon faktur sebelum pajak.
      SAK: Mengurangi nilai pembelian
    status: ✅ MATCH
    catatan: "Percent OR amount, tidak keduanya"

  - field: invoice_discount_amount
    frontend: "invoiceDiscountAmount: number"
    backend: "invoice_discount_amount BIGINT DEFAULT 0"
    standar_akuntansi: "Diskon faktur dalam nominal Rupiah"
    status: ✅ MATCH
    catatan: null

  - field: cash_discount_percent
    frontend: "cashDiscountPercent: number"
    backend: "cash_discount_percent DECIMAL(5,2) DEFAULT 0"
    standar_akuntansi: |
      Potongan tunai / early payment discount.
      SAK EMKM: Dicatat terpisah dari diskon pembelian
    status: ✅ MATCH
    catatan: "Diskon untuk pembayaran cepat"

  - field: cash_discount_amount
    frontend: "cashDiscountAmount: number"
    backend: "cash_discount_amount BIGINT DEFAULT 0"
    standar_akuntansi: "Potongan tunai dalam nominal"
    status: ✅ MATCH
    catatan: null

  # === TAX ===
  - field: tax_rate
    frontend: "taxRate: 0 | 11 | 12"
    backend: "tax_rate INTEGER DEFAULT 11"
    standar_akuntansi: |
      PPN 2025:
      - 0%: Non-taxable, ekspor
      - 11%: Tarif tertentu (transisi)
      - 12%: Tarif umum (barang biasa)
    status: ✅ MATCH
    catatan: "Sesuai regulasi 2025"

  - field: dpp_manual
    frontend: "dppManual?: number (null = auto)"
    backend: "dpp_manual BIGINT"
    standar_akuntansi: |
      DPP manual override untuk kasus khusus:
      - Faktur dari non-PKP
      - Koreksi DPP
    status: ✅ MATCH
    catatan: "Jika null, DPP dihitung otomatis"

  # === CALCULATED FIELDS ===
  - field: subtotal
    frontend: "Calculated: Σ(qty × price)"
    backend: "subtotal BIGINT DEFAULT 0"
    standar_akuntansi: "Total sebelum diskon"
    status: ✅ MATCH
    catatan: "Stored for performance"

  - field: item_discount_total
    frontend: "Calculated: Σ(item discounts)"
    backend: "item_discount_total BIGINT DEFAULT 0"
    standar_akuntansi: "Total diskon per item"
    status: ✅ MATCH
    catatan: null

  - field: invoice_discount_total
    frontend: "Calculated: after_item × percent / 100"
    backend: "invoice_discount_total BIGINT DEFAULT 0"
    standar_akuntansi: "Diskon level faktur yang diterapkan"
    status: ✅ MATCH
    catatan: null

  - field: cash_discount_total
    frontend: "Calculated"
    backend: "cash_discount_total BIGINT DEFAULT 0"
    standar_akuntansi: "Potongan tunai yang diterapkan"
    status: ✅ MATCH
    catatan: null

  - field: dpp
    frontend: "Calculated: after_all_discounts OR dpp_manual"
    backend: "dpp BIGINT DEFAULT 0"
    standar_akuntansi: |
      Dasar Pengenaan Pajak.
      DPP = Subtotal - All Discounts
    status: ✅ MATCH
    catatan: "Tax base untuk PPN"

  - field: tax_amount
    frontend: "Calculated: dpp × tax_rate / 100"
    backend: "tax_amount BIGINT DEFAULT 0"
    standar_akuntansi: "PPN = DPP × 11% atau 12%"
    status: ✅ MATCH
    catatan: null

  - field: grand_total
    frontend: "Calculated: dpp + tax_amount"
    backend: "grand_total BIGINT DEFAULT 0"
    standar_akuntansi: "Total faktur termasuk pajak"
    status: ✅ MATCH
    catatan: null

  # === STATUS & PAYMENT ===
  - field: status_v2
    frontend: "'draft' initially"
    backend: "status_v2 VARCHAR(20): draft|posted|paid|void"
    standar_akuntansi: |
      - draft: Belum posting, bisa edit
      - posted: Sudah masuk akuntansi (AP + Journal)
      - paid: Sudah lunas
      - void: Dibatalkan
    status: ✅ MATCH
    catatan: null

  - field: amount_paid
    frontend: "Display only"
    backend: "amount_paid BIGINT DEFAULT 0"
    standar_akuntansi: "Total pembayaran yang sudah dilakukan"
    status: ✅ MATCH
    catatan: "Updated via payment transactions"

missing_fields:
  - field: pph_withheld
    reason: |
      PPh 23/4(2) yang dipotong dari vendor:
      - Jasa: 2% × DPP (PPh 23)
      - Sewa tanah/bangunan: 10% × DPP (PPh 4(2))
      Harus ada tracking untuk bukti potong
    priority: HIGH

  - field: efaktur_number
    reason: |
      Nomor Seri Faktur Pajak dari vendor PKP.
      Format: XXX-XXX.XX.XXXXXXXX
      Wajib untuk PPN Masukan
    priority: HIGH

  - field: warehouse_id
    reason: "Gudang penerima untuk multi-warehouse"
    priority: MEDIUM

  - field: attachments
    reason: "Lampiran file faktur vendor"
    priority: MEDIUM

extra_fields: []

tax_compliance:
  - item: "PPN Rate 2025"
    status: ✅ COMPLIANT
    catatan: "11% dan 12% tersedia"

  - item: "DPP Calculation"
    status: ✅ COMPLIANT
    catatan: |
      Formula: DPP = Subtotal - Item Discounts - Invoice Discount - Cash Discount
      Sesuai dengan aturan DJP

  - item: "Cash Discount Treatment"
    status: ✅ COMPLIANT
    catatan: |
      Potongan tunai dikurangkan SEBELUM pajak (mengurangi DPP).
      Ini sesuai dengan perlakuan pajak Indonesia.

  - item: "PPh Withholding"
    status: ❌ NON-COMPLIANT
    catatan: |
      TIDAK ADA field untuk:
      - PPh 23 (2% jasa, 15% dividen)
      - PPh 4(2) (10% sewa tanah/bangunan)
      - Bukti potong

  - item: "e-Faktur Integration"
    status: ❌ NON-COMPLIANT
    catatan: |
      TIDAK ADA field untuk:
      - Nomor Seri Faktur Pajak
      - Tanggal Faktur Pajak
      - NPWP PKP vendor

recommendations:
  - priority: HIGH
    description: |
      Tambah kolom PPh withholding ke bills table:
      - pph_rate DECIMAL(5,2)
      - pph_amount BIGINT
      - pph_type VARCHAR(20) -- 'PPH_23', 'PPH_4_2', 'PPH_21'
    affected: backend

  - priority: HIGH
    description: |
      Tambah kolom e-Faktur ke bills table:
      - efaktur_number VARCHAR(30)
      - efaktur_date DATE
      - vendor_npwp VARCHAR(20)
    affected: backend

  - priority: MEDIUM
    description: "Tambah warehouse_id untuk multi-warehouse receiving"
    affected: both

  - priority: LOW
    description: "Tambah attachments support (tabel terpisah)"
    affected: both
```

---

# Modul 4: Faktur Penjualan (Sales Invoice)

```yaml
module: faktur_penjualan
status: COMPLETE - MOSTLY SYNCED

field_review:
  # === HEADER ===
  - field: customer_id
    frontend: "customerId?: string (from CustomerSheet)"
    backend: "customer_id UUID FK customers"
    standar_akuntansi: "Link ke master pelanggan"
    status: ✅ MATCH
    catatan: null

  - field: customer_name
    frontend: "customerName: string (required)"
    backend: "customer_name VARCHAR(255)"
    standar_akuntansi: "Nama pelanggan tersimpan untuk audit"
    status: ✅ MATCH
    catatan: "Support auto-create customer"

  - field: invoice_number
    frontend: "invoiceNo?: string (optional, auto-gen)"
    backend: "invoice_number VARCHAR(50) UNIQUE per tenant"
    standar_akuntansi: |
      Format nomor faktur:
      - Internal: INV-YYMM-0001
      - e-Faktur: XXX-XXX.XX.XXXXXXXX
    status: ✅ MATCH
    catatan: "Auto-generate: INV-YYMM-####"

  - field: ref_no / order_no
    frontend: "orderNo?: string (Customer PO)"
    backend: "ref_no VARCHAR(100)"
    standar_akuntansi: "Nomor PO pelanggan untuk referensi"
    status: ✅ MATCH
    catatan: null

  - field: invoice_date
    frontend: "invoiceDate: string (YYYY-MM-DD)"
    backend: "invoice_date DATE NOT NULL"
    standar_akuntansi: "Tanggal pengakuan pendapatan"
    status: ✅ MATCH
    catatan: "Default: today"

  - field: due_date
    frontend: "dueDate: string (YYYY-MM-DD)"
    backend: "due_date DATE NOT NULL"
    standar_akuntansi: "Tanggal jatuh tempo untuk AR aging"
    status: ✅ MATCH
    catatan: "Default: invoice_date + 30 days"

  - field: sales
    frontend: "sales?: string (salesperson name)"
    backend: "NOT IN SCHEMA"
    standar_akuntansi: "Tracking komisi sales"
    status: ⚠️ REVIEW
    catatan: "Frontend collect, backend tidak simpan"

  - field: tax_rate
    frontend: "taxRate: 0 | 11"
    backend: "tax_rate DECIMAL(5,2)"
    standar_akuntansi: "PPN Keluaran: 11% atau 12%"
    status: ⚠️ REVIEW
    catatan: "Frontend hanya 0/11, backend support decimal"

  - field: tax_inclusive
    frontend: "taxInclusive: boolean"
    backend: "Implied in calculation"
    standar_akuntansi: "Harga termasuk PPN atau tidak"
    status: ⚠️ REVIEW
    catatan: "Backend handling perlu verifikasi"

  - field: notes
    frontend: "notes?: string"
    backend: "notes TEXT"
    standar_akuntansi: "Catatan untuk pelanggan"
    status: ✅ MATCH
    catatan: null

  # === LINE ITEMS ===
  - field: item_id / product_id
    frontend: "productId?: string"
    backend: "item_id UUID FK products"
    standar_akuntansi: "Link ke master produk"
    status: ✅ MATCH
    catatan: null

  - field: description
    frontend: "productName: string"
    backend: "description VARCHAR(255)"
    standar_akuntansi: "Deskripsi item"
    status: ✅ MATCH
    catatan: null

  - field: quantity
    frontend: "quantity: number"
    backend: "quantity DECIMAL(10,2)"
    standar_akuntansi: "Jumlah item terjual"
    status: ✅ MATCH
    catatan: null

  - field: unit
    frontend: "unit: string"
    backend: "unit VARCHAR(20)"
    standar_akuntansi: "Satuan ukur"
    status: ✅ MATCH
    catatan: null

  - field: unit_price
    frontend: "pricePerUnit: number"
    backend: "unit_price BIGINT"
    standar_akuntansi: "Harga jual per unit"
    status: ✅ MATCH
    catatan: null

  - field: discount_percent
    frontend: "discountPercent: number (0-100)"
    backend: "discount_percent DECIMAL(5,2)"
    standar_akuntansi: "Diskon per item"
    status: ✅ MATCH
    catatan: null

  - field: discount_amount
    frontend: "discountAmount?: number"
    backend: "discount_amount BIGINT"
    standar_akuntansi: "Diskon nominal per item"
    status: ✅ MATCH
    catatan: null

  # === INVOICE LEVEL DISCOUNT ===
  - field: discount_percent (invoice)
    frontend: "discountPercent: number"
    backend: "discount_percent DECIMAL(5,2)"
    standar_akuntansi: "Diskon level faktur"
    status: ✅ MATCH
    catatan: null

  - field: discount_amount (invoice)
    frontend: "discountAmount: number"
    backend: "discount_amount BIGINT"
    standar_akuntansi: "Diskon nominal level faktur"
    status: ✅ MATCH
    catatan: null

  # === COGS / HPP ===
  - field: unit_cost
    frontend: "NOT IN FORM (auto-calculated)"
    backend: "unit_cost BIGINT DEFAULT 0"
    standar_akuntansi: |
      Harga Pokok Penjualan per unit.
      Dihitung saat posting menggunakan:
      - Weighted Average Cost (prioritas)
      - Purchase Price (fallback)
    status: ✅ MATCH
    catatan: "Auto-calculated from inventory ledger"

  - field: total_cost
    frontend: "NOT IN FORM"
    backend: "total_cost BIGINT DEFAULT 0"
    standar_akuntansi: "HPP total = qty × unit_cost"
    status: ✅ MATCH
    catatan: null

  - field: total_cogs
    frontend: "NOT IN FORM"
    backend: "total_cogs BIGINT DEFAULT 0"
    standar_akuntansi: "Total HPP seluruh invoice"
    status: ✅ MATCH
    catatan: null

  - field: cost_source
    frontend: "NOT IN FORM"
    backend: "cost_source VARCHAR(30)"
    standar_akuntansi: "Sumber HPP: WEIGHTED_AVG atau PURCHASE_PRICE"
    status: ✅ MATCH
    catatan: "Audit trail untuk metode costing"

  # === CALCULATED TOTALS ===
  - field: subtotal
    frontend: "Calculated"
    backend: "subtotal BIGINT"
    standar_akuntansi: "Total sebelum diskon dan pajak"
    status: ✅ MATCH
    catatan: null

  - field: tax_amount
    frontend: "Calculated: taxableAmount × taxRate / 100"
    backend: "tax_amount BIGINT"
    standar_akuntansi: "PPN Keluaran"
    status: ✅ MATCH
    catatan: null

  - field: total_amount
    frontend: "grandTotal = taxableAmount + taxAmount"
    backend: "total_amount BIGINT"
    standar_akuntansi: "Grand total faktur"
    status: ✅ MATCH
    catatan: null

  # === STATUS & PAYMENT ===
  - field: status
    frontend: "'draft' initially"
    backend: "status VARCHAR(20)"
    standar_akuntansi: |
      Lifecycle:
      - draft: Belum posting
      - posted: Masuk AR dan journal
      - partial: Sebagian dibayar
      - paid: Lunas
      - overdue: Lewat jatuh tempo
      - void: Dibatalkan
    status: ✅ MATCH
    catatan: null

  - field: amount_paid
    frontend: "Display only"
    backend: "amount_paid BIGINT"
    standar_akuntansi: "Total pembayaran diterima"
    status: ✅ MATCH
    catatan: null

  # === ACCOUNTING LINKS ===
  - field: ar_id
    frontend: "NOT IN FORM"
    backend: "ar_id UUID FK accounts_receivable"
    standar_akuntansi: "Link ke piutang usaha"
    status: ✅ MATCH
    catatan: "Created on posting"

  - field: journal_id
    frontend: "NOT IN FORM"
    backend: "journal_id UUID FK journal_entries"
    standar_akuntansi: "Link ke jurnal penjualan"
    status: ✅ MATCH
    catatan: "Dr Piutang, Cr Penjualan"

  - field: cogs_journal_id
    frontend: "NOT IN FORM"
    backend: "cogs_journal_id UUID FK journal_entries"
    standar_akuntansi: "Link ke jurnal HPP"
    status: ✅ MATCH
    catatan: "Dr HPP, Cr Persediaan"

missing_fields:
  - field: salesperson_id
    reason: "Link ke master sales untuk tracking komisi"
    priority: MEDIUM

  - field: efaktur_number
    reason: |
      Nomor Seri Faktur Pajak untuk e-Faktur Keluaran.
      WAJIB untuk PKP dengan omzet > 4.8M/tahun.
      Format: XXX-XXX.XX.XXXXXXXX
    priority: HIGH

  - field: efaktur_date
    reason: "Tanggal Faktur Pajak (bisa beda dari invoice_date)"
    priority: HIGH

  - field: customer_npwp
    reason: "NPWP pelanggan untuk e-Faktur"
    priority: HIGH

  - field: tax_rate 12%
    reason: "Frontend hanya support 0/11%, perlu tambah 12%"
    priority: MEDIUM

  - field: shipping_address
    reason: "Alamat pengiriman untuk invoice"
    priority: LOW

  - field: terms_conditions
    reason: "Syarat dan ketentuan di invoice"
    priority: LOW

extra_fields: []

tax_compliance:
  - item: "PPN Keluaran"
    status: ⚠️ REVIEW
    catatan: |
      Frontend hanya support 0% dan 11%.
      Perlu tambah 12% untuk tarif umum 2025.

  - item: "e-Faktur Keluaran"
    status: ❌ NON-COMPLIANT
    catatan: |
      TIDAK ADA field untuk:
      - Nomor Seri Faktur Pajak
      - Kode dan Nomor Seri (16 digit)
      - Tanggal Faktur Pajak
      - NPWP Pembeli
      PKP wajib terbitkan e-Faktur.

  - item: "HPP/COGS Calculation"
    status: ✅ COMPLIANT
    catatan: |
      Weighted Average Cost method sudah diimplementasi.
      Sesuai dengan SAK EMKM untuk UMKM.

  - item: "Journal Entry"
    status: ✅ COMPLIANT
    catatan: |
      Double entry posting:
      - Dr Piutang Usaha (1-10300)
      - Cr Penjualan (4-10100)
      - Dr HPP (5-10100)
      - Cr Persediaan (1-10400)

recommendations:
  - priority: HIGH
    description: |
      Tambah support e-Faktur:
      - efaktur_number VARCHAR(30)
      - efaktur_date DATE
      - customer_npwp VARCHAR(20)
      - efaktur_status VARCHAR(20) -- draft, created, approved, cancelled
    affected: both

  - priority: MEDIUM
    description: "Tambah tax_rate 12% di frontend"
    affected: frontend

  - priority: MEDIUM
    description: "Tambah salesperson_id dan link ke tabel sales reps"
    affected: both

  - priority: LOW
    description: "Tambah shipping_address dan terms_conditions"
    affected: both
```

---

# Modul 5: Biaya & Pengeluaran (Expenses)

```yaml
module: biaya_pengeluaran
status: CRITICAL - BACKEND MISSING

field_review:
  # === HEADER ===
  - field: date / expense_date
    frontend: "date: string (YYYY-MM-DD, required)"
    backend: "ENDPOINT NOT FOUND"
    standar_akuntansi: "Tanggal pengeluaran untuk pengakuan beban"
    status: ❌ MISMATCH
    catatan: "Backend /api/expenses belum dibuat"

  - field: paid_through_id
    frontend: "paidThroughId: string (required)"
    backend: "ENDPOINT NOT FOUND"
    standar_akuntansi: |
      Akun pembayaran (Kas/Bank):
      - Kas (1-10100)
      - Bank BCA (1-10201)
      - Kas Kecil (1-10102)
    status: ❌ MISMATCH
    catatan: "Frontend ready, backend belum"

  - field: account_id (single mode)
    frontend: "accountId: string (required for non-itemized)"
    backend: "ENDPOINT NOT FOUND"
    standar_akuntansi: |
      Akun beban SAK EMKM:
      - Beban Gaji (5-20100)
      - Beban Sewa (5-20200)
      - Beban Utilitas (5-20300)
      - dll
    status: ❌ MISMATCH
    catatan: "Frontend ready dengan akun SAK EMKM"

  - field: amount (single mode)
    frontend: "amount: number (required for non-itemized)"
    backend: "ENDPOINT NOT FOUND"
    standar_akuntansi: "Nominal pengeluaran dalam Rupiah"
    status: ❌ MISMATCH
    catatan: null

  - field: is_itemized
    frontend: "isItemized: boolean"
    backend: "ENDPOINT NOT FOUND"
    standar_akuntansi: "Mode single expense vs multiple line items"
    status: ❌ MISMATCH
    catatan: null

  - field: line_items
    frontend: |
      lineItems[]: {
        id, accountId, accountName, amount, notes
      }
    backend: "ENDPOINT NOT FOUND"
    standar_akuntansi: "Multiple beban dalam satu transaksi"
    status: ❌ MISMATCH
    catatan: "Frontend support itemized expense"

  - field: vendor_id
    frontend: "vendorId?: string (optional)"
    backend: "ENDPOINT NOT FOUND"
    standar_akuntansi: "Link ke vendor (opsional)"
    status: ❌ MISMATCH
    catatan: null

  - field: tax_id / tax_rate
    frontend: |
      taxId?: string
      taxRate?: number
      Options: PPN 11%, PPN 12%, PPh 21/5%, PPh 23/2%
    backend: "ENDPOINT NOT FOUND"
    standar_akuntansi: |
      Pajak yang berlaku:
      - PPN Masukan (jika dari PKP)
      - PPh 21 (gaji)
      - PPh 23 (jasa dari badan)
    status: ❌ MISMATCH
    catatan: "Frontend ready dengan opsi pajak"

  - field: currency
    frontend: "currency: string (default 'IDR')"
    backend: "ENDPOINT NOT FOUND"
    standar_akuntansi: "Multi-currency support"
    status: ❌ MISMATCH
    catatan: null

  - field: reference
    frontend: "reference?: string"
    backend: "ENDPOINT NOT FOUND"
    standar_akuntansi: "Nomor referensi (kwitansi, nota)"
    status: ❌ MISMATCH
    catatan: null

  - field: notes
    frontend: "notes?: string (max 500 chars)"
    backend: "ENDPOINT NOT FOUND"
    standar_akuntansi: "Catatan pengeluaran"
    status: ❌ MISMATCH
    catatan: null

  # === STATUS ===
  - field: status
    frontend: |
      ExpenseStatus: 'non-billable' | 'billable' |
                     'invoiced' | 'reimbursed'
    backend: "ENDPOINT NOT FOUND"
    standar_akuntansi: |
      - non-billable: Beban perusahaan
      - billable: Bisa ditagihkan ke customer
      - invoiced: Sudah ditagihkan
      - reimbursed: Penggantian biaya
    status: ❌ MISMATCH
    catatan: "Frontend support billable expense tracking"

  - field: has_receipt
    frontend: "hasReceipt: boolean"
    backend: "ENDPOINT NOT FOUND"
    standar_akuntansi: "Bukti transaksi untuk audit"
    status: ❌ MISMATCH
    catatan: null

missing_fields:
  - field: ENTIRE BACKEND MODULE
    reason: |
      CRITICAL: /api/expenses endpoint TIDAK ADA.
      Perlu dibuat:
      - POST /api/expenses (create)
      - GET /api/expenses (list with filters)
      - GET /api/expenses/{id} (detail)
      - PATCH /api/expenses/{id} (update)
      - DELETE /api/expenses/{id} (delete)
      - GET /api/expenses/summary (stats)
    priority: HIGH

  - field: journal_entry_creation
    reason: |
      Saat expense dibuat, jurnal otomatis:
      - Dr Beban XXX (akun expense)
      - Cr Kas/Bank (akun paid_through)
    priority: HIGH

  - field: pph_withholding
    reason: |
      PPh yang dipotong:
      - PPh 21: Gaji karyawan
      - PPh 23: Jasa dari badan usaha
      - PPh 4(2): Sewa tanah/bangunan
    priority: MEDIUM

  - field: attachments
    reason: "Upload bukti transaksi (kwitansi, nota)"
    priority: MEDIUM

  - field: approval_workflow
    reason: "Persetujuan untuk expense di atas limit"
    priority: LOW

extra_fields: []

tax_compliance:
  - item: "PPN Masukan"
    status: ❌ NON-COMPLIANT
    catatan: |
      Frontend punya opsi PPN, tapi:
      - Backend belum ada
      - Tidak ada validasi PKP vendor
      - Tidak ada link ke e-Faktur Masukan

  - item: "PPh 21 (Gaji)"
    status: ❌ NON-COMPLIANT
    catatan: |
      Frontend punya opsi PPh 21, tapi:
      - Backend belum ada
      - Tidak ada kalkulasi TER (Tarif Efektif Rata-rata)
      - Tidak ada link ke SPT PPh 21

  - item: "PPh 23 (Jasa)"
    status: ❌ NON-COMPLIANT
    catatan: |
      Frontend punya opsi PPh 23, tapi:
      - Backend belum ada
      - Tidak ada bukti potong
      - Tidak ada link ke SPT PPh 23

  - item: "Journal Entry"
    status: ❌ NON-COMPLIANT
    catatan: |
      Expense TIDAK membuat jurnal karena backend tidak ada.
      Seharusnya:
      - Dr Beban (5-xxxxx)
      - Cr Kas/Bank (1-10xxx)

recommendations:
  - priority: HIGH
    description: |
      URGENT: Buat /api/expenses router dengan:

      Database schema:
      CREATE TABLE expenses (
        id UUID PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        expense_number VARCHAR(50),
        expense_date DATE NOT NULL,
        paid_through_id UUID NOT NULL, -- FK bank_accounts
        vendor_id UUID,
        currency VARCHAR(3) DEFAULT 'IDR',
        subtotal BIGINT NOT NULL,
        tax_rate DECIMAL(5,2),
        tax_amount BIGINT DEFAULT 0,
        total_amount BIGINT NOT NULL,
        status VARCHAR(20) DEFAULT 'posted',
        is_itemized BOOLEAN DEFAULT false,
        reference VARCHAR(100),
        notes TEXT,
        journal_id UUID,
        created_by UUID,
        created_at TIMESTAMPTZ DEFAULT NOW()
      );

      CREATE TABLE expense_items (
        id UUID PRIMARY KEY,
        expense_id UUID NOT NULL,
        account_id UUID NOT NULL, -- FK chart_of_accounts
        amount BIGINT NOT NULL,
        notes TEXT,
        line_number INT
      );
    affected: backend

  - priority: HIGH
    description: |
      Implement auto journal posting:

      async def post_expense(expense):
        # Single expense
        if not expense.is_itemized:
          journal = create_journal(
            date=expense.expense_date,
            lines=[
              {"account_id": expense.account_id, "debit": expense.amount},
              {"account_id": expense.paid_through_id, "credit": expense.amount}
            ]
          )
        # Itemized
        else:
          lines = [{"account_id": item.account_id, "debit": item.amount} for item in expense.items]
          lines.append({"account_id": expense.paid_through_id, "credit": expense.total_amount})
          journal = create_journal(date=expense.expense_date, lines=lines)
    affected: backend

  - priority: MEDIUM
    description: |
      Tambah PPh withholding tracking:
      - pph_type: PPH_21, PPH_23, PPH_4_2
      - pph_rate: DECIMAL
      - pph_amount: BIGINT
      - bukti_potong_number: VARCHAR
    affected: both

  - priority: MEDIUM
    description: "Tambah attachment/receipt upload support"
    affected: both

  - priority: LOW
    description: "Tambah approval workflow untuk expense > limit"
    affected: both
```

---

# Sync Plan: Frontend ↔ Backend

## CRITICAL (Harus segera)

| Field/Module | Action | Priority |
|--------------|--------|----------|
| Expenses Backend | CREATE entire /api/expenses module | P0 |
| Vendor: vendor_type | ADD column to vendors table | P0 |
| Vendor: is_pkp | ADD column to vendors table | P0 |
| Bills: pph_withholding | ADD columns for PPh tracking | P1 |
| Sales Invoice: efaktur fields | ADD columns for e-Faktur | P1 |

## HIGH (Dalam 1-2 sprint)

| Field/Module | Action | Priority |
|--------------|--------|----------|
| Vendor: nik, default_tax_rate | ADD columns | P1 |
| Vendor: currency, opening_balance | ADD columns | P1 |
| Vendor: multiple addresses | CREATE vendor_addresses table | P1 |
| Vendor: multiple contacts | CREATE vendor_contacts table | P1 |
| Bills: efaktur_number | ADD column | P1 |
| Sales Invoice: tax_rate 12% | ADD to frontend options | P1 |

## MEDIUM (Dalam 1-2 bulan)

| Field/Module | Action | Priority |
|--------------|--------|----------|
| Products: price type standardization | MIGRATE to BIGINT | P2 |
| Products: account linking | CHANGE to UUID FK | P2 |
| Sales Invoice: salesperson_id | ADD column and FK | P2 |
| Expenses: attachments | ADD expense_attachments table | P2 |

## LOW (Nice to have)

| Field/Module | Action | Priority |
|--------------|--------|----------|
| Products: reorder_level | ADD column | P3 |
| Products: sku | ADD column | P3 |
| Vendor: credit_limit to frontend | ADD to form | P3 |
| Sales Invoice: shipping_address | ADD column | P3 |

---

# Ringkasan Kepatuhan Pajak Indonesia

## PPN (Pajak Pertambahan Nilai)

| Aspek | Status | Catatan |
|-------|--------|---------|
| Tarif 11% | ✅ | Tersedia di semua modul |
| Tarif 12% | ⚠️ | Ada di Bills, kurang di Sales Invoice frontend |
| Tarif 0% | ✅ | Tersedia |
| DPP Calculation | ✅ | Formula sudah benar |
| Tax Inclusive | ✅ | Supported |
| e-Faktur Masukan | ❌ | Field belum ada |
| e-Faktur Keluaran | ❌ | Field belum ada |
| PKP Validation | ❌ | is_pkp field belum ada di vendor/customer |

## PPh (Pajak Penghasilan)

| Aspek | Status | Catatan |
|-------|--------|---------|
| PPh 23 - 2% (Jasa) | ⚠️ | Opsi ada, tracking belum |
| PPh 23 - 15% (Dividen) | ⚠️ | Opsi ada, tracking belum |
| PPh 4(2) - 10% (Sewa) | ❌ | Belum ada |
| PPh 21 (Gaji) | ❌ | Belum ada TER calculation |
| Bukti Potong | ❌ | Belum ada |
| Vendor Type (BADAN/PRIBADI) | ❌ | Field belum ada |

## Rekomendasi Prioritas untuk Tax Compliance

1. **P0**: Tambah vendor_type dan is_pkp ke vendors table
2. **P0**: Buat expenses module dengan PPh tracking
3. **P1**: Tambah e-Faktur fields ke bills dan sales_invoices
4. **P1**: Implementasi PPh withholding di bills
5. **P2**: Buat laporan bukti potong PPh
6. **P2**: Integrasi dengan DJP (e-Faktur, e-Bupot)

---

*Report generated by Claude AI - Akuntan Senior*
*Standar: SAK EMKM, PSAK, DJP 2025*
