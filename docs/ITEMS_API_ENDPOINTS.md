# Items Module - API Endpoint Documentation

Base URL: `https://milkyhoop.com/api`

## Authentication
All endpoints require `Authorization: Bearer <token>` header.

---

## 1. LIST ITEMS

```
GET /api/items
```

**Query Parameters:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| item_type | string | - | Filter: `goods` or `service` |
| track_inventory | bool | - | Filter tracked items |
| search | string | - | Search by name/barcode |
| kategori | string | - | Filter by category |
| limit | int | 50 | Max 200 |
| offset | int | 0 | Pagination offset |

**Response:**
```json
{
  "success": true,
  "items": [{
    "id": "uuid",
    "name": "Air Mineral Aqua 600ml",
    "item_type": "goods",
    "track_inventory": true,
    "base_unit": "Pcs",
    "barcode": "8992755000105",
    "kategori": "Makanan & Minuman",
    "deskripsi": "Air mineral kemasan",
    "is_returnable": true,
    "sales_price": 4500.0,
    "purchase_price": 3200.0,
    "image_url": null,
    "reorder_level": 10.0,
    "vendor_name": "PT Aqua Golden",
    "sales_tax": "PPN_11",
    "purchase_tax": null,
    "current_stock": 186.0,
    "stock_value": 595200.0,
    "low_stock": false,
    "conversions": [{
      "conversion_unit": "Dus",
      "conversion_factor": 24,
      "sales_price": 100000,
      "purchase_price": 72000
    }],
    "created_at": "2026-01-14T10:16:13",
    "updated_at": "2026-01-14T10:16:13"
  }],
  "total": 66,
  "has_more": true
}
```

---

## 2. GET ITEM DETAIL

```
GET /api/items/{item_id}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "id": "uuid",
    "name": "Air Mineral Aqua 600ml",
    "item_type": "goods",
    "track_inventory": true,
    "base_unit": "Pcs",
    "barcode": "8992755000105",
    "kategori": "Makanan & Minuman",
    "deskripsi": "Air mineral kemasan",
    "is_returnable": true,
    "image_url": "https://minio.milkyhoop.com/...",
    "reorder_level": 10.0,
    "preferred_vendor_id": "uuid",
    "sales_account": "Sales",
    "purchase_account": "Cost of Goods Sold",
    "sales_account_id": "uuid",
    "purchase_account_id": "uuid",
    "sales_tax": "PPN_11",
    "purchase_tax": null,
    "sales_price": 4500.0,
    "purchase_price": 3200.0,
    "current_stock": 186.0,
    "stock_value": 595200.0,
    "conversions": [...],
    "created_at": "2026-01-14T10:16:13",
    "updated_at": "2026-01-14T10:16:13"
  }
}
```

---

## 3. CREATE ITEM

```
POST /api/items
```

**Request Body:**
```json
{
  "name": "Produk Baru",
  "item_type": "goods",
  "base_unit": "Pcs",
  "track_inventory": true,
  "barcode": "1234567890",
  "kategori": "Makanan & Minuman",
  "deskripsi": "Deskripsi produk",
  "is_returnable": true,
  "sales_account": "Sales",
  "purchase_account": "Cost of Goods Sold",
  "sales_tax": "PPN_11",
  "purchase_tax": null,
  "sales_price": 10000,
  "purchase_price": 7000,
  "image_url": "https://...",
  "reorder_level": 10,
  "preferred_vendor_id": "uuid",
  "sales_account_id": "uuid",
  "purchase_account_id": "uuid",
  "conversions": [{
    "conversion_unit": "Dus",
    "conversion_factor": 12,
    "sales_price": 110000,
    "purchase_price": 80000
  }]
}
```

**Response:** `201`
```json
{
  "success": true,
  "message": "Item 'Produk Baru' berhasil ditambahkan",
  "data": { "id": "uuid", "name": "Produk Baru", "item_type": "goods" }
}
```

---

## 4. UPDATE ITEM

```
PUT /api/items/{item_id}
```

**Request Body:** Same fields as CREATE, all optional. Only send fields to update.

**Response:**
```json
{
  "success": true,
  "message": "Item berhasil diperbarui",
  "data": { "id": "uuid" }
}
```

---

## 5. DELETE ITEM

```
DELETE /api/items/{item_id}
```

**Response:**
```json
{
  "success": true,
  "message": "Item 'Produk Baru' berhasil dihapus"
}
```

---

## 6. DUPLICATE ITEM

```
POST /api/items/{item_id}/duplicate
```

Duplicates the item with name suffix "(Copy)". Does NOT copy stock or images.

**Response:**
```json
{
  "success": true,
  "message": "Item 'Air Mineral Aqua 600ml (Copy)' berhasil diduplikasi",
  "data": {
    "id": "new-uuid",
    "name": "Air Mineral Aqua 600ml (Copy)",
    "item_type": "goods",
    "source_id": "original-uuid"
  }
}
```

---

## 7. ITEM TRANSACTIONS (Tab Riwayat)

```
GET /api/items/{item_id}/transactions?limit=20&offset=0
```

Returns purchase/sales/adjustment transaction history for the item.

**Response:**
```json
{
  "success": true,
  "transactions": [{
    "id": "tx-id",
    "date": "2026-01-15",
    "transaction_type": "pembelian",
    "document_number": "doc-id",
    "qty_change": 50.0,
    "unit_price": 3200,
    "total": 160000,
    "notes": null
  }],
  "total": 5,
  "has_more": false
}
```

---

## 8. ITEM RELATED DOCUMENTS (Tab Terkait)

```
GET /api/items/{item_id}/related
```

Returns invoices, bills, and POs that contain this item.

**Response:**
```json
{
  "success": true,
  "invoices": [{
    "id": "uuid",
    "document_type": "invoice",
    "document_number": "INV-001",
    "date": "2026-01-10",
    "counterparty": "Customer Name",
    "qty": 5.0,
    "unit_price": 4500,
    "total": 22500,
    "status": "paid"
  }],
  "bills": [{
    "id": "uuid",
    "document_type": "bill",
    "document_number": "BILL-001",
    "date": "2026-01-08",
    "counterparty": "Vendor Name",
    "qty": 100.0,
    "unit_price": 3200,
    "total": 320000,
    "status": "paid"
  }],
  "purchase_orders": []
}
```

---

## 9. ITEMS SUMMARY

```
GET /api/items/summary
```

**Response:**
```json
{
  "success": true,
  "data": {
    "total": 66,
    "goods_count": 50,
    "service_count": 16,
    "tracked_count": 47,
    "in_stock_count": 45,
    "low_stock_count": 0,
    "out_of_stock_count": 2
  }
}
```

---

## 10. CATEGORIES

### List Categories
```
GET /api/items/categories
```

**Response:**
```json
{
  "success": true,
  "categories": ["ATK & Perlengkapan Kantor", "Elektronik & Gadget", "Makanan & Minuman", ...]
}
```

### Create Category
```
POST /api/items/categories
```

**Request Body:**
```json
{ "name": "Kategori Baru" }
```

**Response:**
```json
{
  "success": true,
  "message": "Kategori 'Kategori Baru' siap digunakan",
  "category": "Kategori Baru"
}
```

---

## 11. UNITS

### List Units
```
GET /api/items/units
```

**Response:**
```json
{
  "success": true,
  "default_units": ["Pcs", "Box", "Karton", "Lusin", ...],
  "custom_units": ["Bulan", "Hari", "Jam", ...]
}
```

### Create Unit
```
POST /api/items/units
```

**Request Body:**
```json
{ "name": "Batang" }
```

---

## 12. ACCOUNTS (from Chart of Accounts)

### Sales Accounts
```
GET /api/items/accounts/sales
```

### Purchase Accounts
```
GET /api/items/accounts/purchase
```

### Inventory Accounts
```
GET /api/items/accounts/inventory
```

**Response (all three):**
```json
{
  "success": true,
  "accounts": [{
    "id": "uuid",
    "code": "4-10100",
    "name": "Penjualan",
    "account_type": "INCOME"
  }]
}
```

---

## 13. TAX OPTIONS

```
GET /api/items/taxes
```

**Response:**
```json
{
  "success": true,
  "goods_taxes": [
    { "value": "", "label": "Tidak Ada", "rate": 0 },
    { "value": "PPN_11", "label": "PPN 11%", "rate": 11 },
    { "value": "PPN_12", "label": "PPN 12%", "rate": 12 }
  ],
  "service_taxes": [
    { "value": "", "label": "Tidak Ada", "rate": 0 },
    { "value": "PPN_11", "label": "PPN 11%", "rate": 11 },
    { "value": "PPH_23_2", "label": "PPh 23 - 2% (Jasa)", "rate": 2 }
  ]
}
```

---

## 14. VENDORS (for dropdown)

```
GET /api/vendors/autocomplete?search=aqua&limit=10
```

**Response:**
```json
{
  "success": true,
  "data": [{
    "id": "uuid",
    "name": "PT Aqua Golden Mississippi",
    "code": "V001"
  }]
}
```

---

## 15. PRODUCT IMAGES (via Documents system)

### Upload Image
```
POST /api/documents/upload
Content-Type: multipart/form-data
```
Form fields: `file` (image file), `category` = "product_image"

### Attach to Product
```
POST /api/documents/{document_id}/attach
```
```json
{
  "entity_type": "product",
  "entity_id": "product-uuid",
  "attachment_type": "image"
}
```

### List Product Images
```
GET /api/documents/product/{product_id}/documents
```

### Detach Image
```
DELETE /api/documents/{document_id}/detach
```
```json
{ "entity_type": "product", "entity_id": "product-uuid" }
```

---

## 16. STOCK ADJUSTMENT

```
POST /api/inventory/products/{product_id}/adjust
```

**Request Body:**
```json
{
  "adjustment_qty": 10,
  "reason": "opname",
  "notes": "Stok opname bulanan"
}
```

Reasons: `opname`, `rusak`, `hilang`, `koreksi`, `lainnya`

---

## Calculated Fields (Frontend Only)

These are NOT stored in DB, calculate on frontend:
```
margin_nominal = sales_price - purchase_price
margin_persen = ((sales_price - purchase_price) / sales_price) * 100
nilai_stok = current_stock * purchase_price
```

## Stock Status Logic

```
- Aktif, stok > reorder_level  → Hijau (in_stock)
- Aktif, 0 < stok <= reorder   → Kuning (low_stock)
- Aktif, stok = 0              → Merah (out_of_stock)
- Nonaktif                     → Abu-abu
```
The `low_stock` boolean is returned in the list endpoint for convenience.
