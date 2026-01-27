# Vendor Module API Integration Guide

> **Status:** ✅ Backend Ready for Integration
> **Date:** 2026-01-26
> **Commits:** `20568514` → `4ed0f7cf`

---

## Summary

Backend sekarang mendukung:
1. **Duplicate Check Endpoint** - Cek duplikat vendor sebelum create/update
2. **Extended Vendor Fields** - Field tambahan untuk bank, tax address, opening balance
3. **Status Toggle Endpoint** - Toggle status vendor dengan 1 klik

---

## 1. Duplicate Check Endpoint (m7)

### Endpoint
```
GET /api/vendors/check-duplicate
```

### Query Parameters
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `nama` | string | No | Nama vendor untuk dicek (partial match, case-insensitive) |
| `npwp` | string | No | NPWP untuk dicek (exact match setelah normalisasi) |
| `excludeId` | string | No | Vendor ID yang dikecualikan (untuk mode edit) |

### Response
```typescript
interface DuplicateCheckResponse {
  byName: VendorMatch[];
  byNpwp: VendorMatch[];
}

interface VendorMatch {
  id: string;
  name: string;
  company: string | null;
  npwp: string | null;
}
```

### Example Usage

**Create Mode - Cek nama baru:**
```typescript
const res = await fetch('/api/vendors/check-duplicate?nama=PT%20Textile');
const data = await res.json();

if (data.byName.length > 0) {
  // Tampilkan warning: "Vendor dengan nama mirip sudah ada"
}
```

**Edit Mode - Cek nama dengan exclude current vendor:**
```typescript
const res = await fetch(`/api/vendors/check-duplicate?nama=${name}&excludeId=${vendorId}`);
```

**Cek NPWP:**
```typescript
const res = await fetch('/api/vendors/check-duplicate?npwp=01.234.567.8-901.000');
// NPWP akan dinormalisasi (hapus titik dan strip) sebelum dicek
```

---

## 2. Extended Vendor Fields

### New Fields di POST/PATCH/GET

```typescript
interface VendorExtendedFields {
  // Account (m2)
  account_number?: string;        // Max 50 chars

  // Vendor Type (m1) - Added LUAR_NEGERI
  vendor_type?: 'BADAN' | 'ORANG_PRIBADI' | 'LUAR_NEGERI';  // Default: 'BADAN'

  // Bank Details (m3)
  bank_name?: string;             // Max 100 chars
  bank_account_number?: string;   // Max 50 chars
  bank_account_holder?: string;   // Max 255 chars

  // Tax Address (m4) - Alamat terpisah untuk e-Faktur
  tax_address?: string;
  tax_city?: string;              // Max 100 chars
  tax_province?: string;          // Max 100 chars
  tax_postal_code?: string;       // Max 20 chars

  // Opening Balance (m1)
  opening_balance?: number;       // In Rupiah, >= 0
  opening_balance_date?: string;  // Format: YYYY-MM-DD
}
```

### POST /api/vendors

```typescript
// Request body sekarang bisa include extended fields
const newVendor = {
  name: "PT Supplier Baru",
  vendor_type: "BADAN",
  account_number: "ACC-001",
  bank_name: "BCA",
  bank_account_number: "1234567890",
  bank_account_holder: "PT Supplier Baru",
  tax_address: "Jl. Pajak No. 123",
  tax_city: "Jakarta",
  opening_balance: 5000000,
  opening_balance_date: "2026-01-01"
};

const res = await fetch('/api/vendors', {
  method: 'POST',
  body: JSON.stringify(newVendor)
});
```

### PATCH /api/vendors/:id

```typescript
// Partial update - hanya kirim field yang berubah
const updates = {
  bank_name: "Mandiri",
  bank_account_number: "9876543210"
};

const res = await fetch(`/api/vendors/${id}`, {
  method: 'PATCH',
  body: JSON.stringify(updates)
});
```

### GET /api/vendors/:id

Response sekarang include semua extended fields:

```typescript
interface VendorDetail {
  id: string;
  code: string | null;
  name: string;
  contact_person: string | null;
  phone: string | null;
  email: string | null;
  address: string | null;
  city: string | null;
  province: string | null;
  postal_code: string | null;
  tax_id: string | null;
  payment_terms_days: number;
  credit_limit: number | null;
  notes: string | null;

  // Extended fields (NEW)
  account_number: string | null;
  vendor_type: string | null;
  bank_name: string | null;
  bank_account_number: string | null;
  bank_account_holder: string | null;
  tax_address: string | null;
  tax_city: string | null;
  tax_province: string | null;
  tax_postal_code: string | null;
  opening_balance: number | null;
  opening_balance_date: string | null;  // ISO format or null

  is_active: boolean;
  created_at: string;
  updated_at: string;
}
```

---

## 3. Vendor Status Toggle (m6)

### Endpoint
```
PATCH /api/vendors/:id/status?status=active|inactive
```

### Query Parameter
| Parameter | Type | Required | Values |
|-----------|------|----------|--------|
| `status` | string | Yes | `active` atau `inactive` |

### Response
```typescript
interface StatusToggleResponse {
  success: true;
  message: "Status vendor berhasil diubah";
  data: {
    id: string;
    name: string;
    is_active: boolean;
  };
}
```

### Example Usage

```typescript
// Deactivate vendor
const res = await fetch(`/api/vendors/${id}/status?status=inactive`, {
  method: 'PATCH'
});

// Activate vendor
const res = await fetch(`/api/vendors/${id}/status?status=active`, {
  method: 'PATCH'
});
```

---

## Migration Notes

### Database Migration Required

File: `backend/migrations/V083__vendor_extended_fields.sql`

**New columns di table `vendors`:**
- `account_number` VARCHAR(50)
- `bank_name` VARCHAR(100)
- `bank_account_number` VARCHAR(50)
- `bank_account_holder` VARCHAR(255)
- `tax_address` TEXT
- `tax_city` VARCHAR(100)
- `tax_province` VARCHAR(100)
- `tax_postal_code` VARCHAR(20)

**Note:** Columns `vendor_type`, `opening_balance`, `opening_balance_date` sudah ada dari V070.

### Run Migration

```bash
# Via Docker
docker compose exec postgres psql -U milkyhoop -d milkyhoop -f /migrations/V083__vendor_extended_fields.sql

# Atau via Flyway jika sudah dikonfigurasi
```

---

## Form Integration Guide

### Vendor Form Sections

Rekomendasi layout form:

```
┌─────────────────────────────────────────────────────────────┐
│ INFORMASI DASAR                                              │
├─────────────────────────────────────────────────────────────┤
│ Nama Vendor*        [____________________________]           │
│ Kode                [____________________________]           │
│ Tipe Vendor         [BADAN ▼] (BADAN/ORANG_PRIBADI/LUAR_NEGERI) │
│ Account Number      [____________________________]           │
│ NPWP                [____________________________]           │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ KONTAK                                                       │
├─────────────────────────────────────────────────────────────┤
│ Nama Kontak         [____________________________]           │
│ Telepon             [____________________________]           │
│ Email               [____________________________]           │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ ALAMAT UTAMA                                                 │
├─────────────────────────────────────────────────────────────┤
│ Alamat              [____________________________]           │
│ Kota                [____________] Provinsi [____________]   │
│ Kode Pos            [____________]                           │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ ALAMAT PAJAK (untuk e-Faktur)                               │
├─────────────────────────────────────────────────────────────┤
│ □ Sama dengan alamat utama                                   │
│ Alamat              [____________________________]           │
│ Kota                [____________] Provinsi [____________]   │
│ Kode Pos            [____________]                           │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ INFORMASI BANK (m3)                                          │
├─────────────────────────────────────────────────────────────┤
│ Nama Bank           [____________________________]           │
│ No. Rekening        [____________________________]           │
│ Atas Nama           [____________________________]           │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ SALDO AWAL (m1)                                              │
├─────────────────────────────────────────────────────────────┤
│ Saldo Awal          Rp [____________________________]        │
│ Tanggal             [____________________________]           │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ PENGATURAN                                                   │
├─────────────────────────────────────────────────────────────┤
│ Termin Pembayaran   [30] hari                                │
│ Batas Kredit        Rp [____________________________]        │
│ Catatan             [____________________________]           │
└─────────────────────────────────────────────────────────────┘
```

### Duplicate Check Integration

```typescript
// Di form vendor, panggil saat user blur dari field nama/NPWP
const checkDuplicates = async (name: string, npwp: string, excludeId?: string) => {
  const params = new URLSearchParams();
  if (name) params.set('nama', name);
  if (npwp) params.set('npwp', npwp);
  if (excludeId) params.set('excludeId', excludeId);

  const res = await fetch(`/api/vendors/check-duplicate?${params}`);
  const data = await res.json();

  return {
    hasDuplicateName: data.byName.length > 0,
    hasDuplicateNpwp: data.byNpwp.length > 0,
    matchesByName: data.byName,
    matchesByNpwp: data.byNpwp
  };
};

// Show warning jika ada duplicate
if (hasDuplicateName) {
  showWarning(`Vendor dengan nama mirip sudah ada: ${matchesByName.map(v => v.name).join(', ')}`);
}
```

### Status Toggle di List

```tsx
// Di vendor list, toggle button
<Button
  onClick={() => toggleStatus(vendor.id, vendor.is_active ? 'inactive' : 'active')}
>
  {vendor.is_active ? 'Nonaktifkan' : 'Aktifkan'}
</Button>
```

---

## Error Codes

| Code | Message | Description |
|------|---------|-------------|
| 400 | "Vendor with name '...' already exists" | Nama vendor duplikat |
| 400 | "Vendor with code '...' already exists" | Kode vendor duplikat |
| 404 | "Vendor not found" | Vendor tidak ditemukan |
| 401 | "Authentication required" | Token tidak valid |
| 500 | "Duplicate check failed" | Error saat cek duplikat |
| 500 | "Failed to update vendor status" | Error saat toggle status |

---

## Testing Checklist

- [ ] Create vendor dengan extended fields
- [ ] Edit vendor - update bank details
- [ ] Get vendor detail - verify extended fields returned
- [ ] Duplicate check by name - partial match
- [ ] Duplicate check by NPWP - exact match
- [ ] Duplicate check with excludeId (edit mode)
- [ ] Toggle vendor status: active → inactive
- [ ] Toggle vendor status: inactive → active
- [ ] Vendor list shows updated status after toggle
