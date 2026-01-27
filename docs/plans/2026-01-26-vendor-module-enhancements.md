# Vendor Module Enhancements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add duplicate check endpoint, vendor status toggle, and extended vendor fields to support the frontend vendor management features.

**Architecture:** Extend existing FastAPI vendor router with new endpoints. Create database migration for missing fields. Follow existing codebase patterns (AsyncPG, connection pooling, tenant isolation).

**Tech Stack:** FastAPI, AsyncPG, PostgreSQL, Pydantic

---

## Summary of Changes

| Priority | Feature | Type |
|----------|---------|------|
| 1 | Duplicate check endpoint (GET /check-duplicate) | New endpoint |
| 2 | Extended vendor fields (account_number, bank details, tax address) | DB migration + schema update |
| 3 | Vendor status toggle (already exists in PATCH, needs explicit endpoint) | Enhancement |

**Note:** Vendor Credits module already exists (`vendor_credits.py`). Phase 2 credit system improvements are out of scope for this plan.

---

## Task 1: Database Migration for Extended Vendor Fields

**Files:**
- Create: `backend/migrations/V083__vendor_extended_fields.sql`

**Step 1: Write the migration SQL**

```sql
-- ============================================================================
-- V083: Vendor Extended Fields
-- Adds account_number, bank details, and tax address fields
-- ============================================================================

-- Account number (vendor's internal reference number)
ALTER TABLE vendors
ADD COLUMN IF NOT EXISTS account_number VARCHAR(50);

-- Bank details
ALTER TABLE vendors
ADD COLUMN IF NOT EXISTS bank_name VARCHAR(100);

ALTER TABLE vendors
ADD COLUMN IF NOT EXISTS bank_account_number VARCHAR(50);

ALTER TABLE vendors
ADD COLUMN IF NOT EXISTS bank_account_holder VARCHAR(255);

-- Tax address (separate from main address for e-Faktur)
ALTER TABLE vendors
ADD COLUMN IF NOT EXISTS tax_address TEXT;

ALTER TABLE vendors
ADD COLUMN IF NOT EXISTS tax_city VARCHAR(100);

ALTER TABLE vendors
ADD COLUMN IF NOT EXISTS tax_province VARCHAR(100);

ALTER TABLE vendors
ADD COLUMN IF NOT EXISTS tax_postal_code VARCHAR(20);

-- Create index for account_number lookups
CREATE INDEX IF NOT EXISTS idx_vendors_tenant_account_number
    ON vendors(tenant_id, account_number)
    WHERE account_number IS NOT NULL;

-- Documentation
COMMENT ON COLUMN vendors.account_number IS 'Vendor internal account/reference number';
COMMENT ON COLUMN vendors.bank_name IS 'Bank name for payments';
COMMENT ON COLUMN vendors.bank_account_number IS 'Bank account number';
COMMENT ON COLUMN vendors.bank_account_holder IS 'Bank account holder name';
COMMENT ON COLUMN vendors.tax_address IS 'Tax address street (for e-Faktur)';
COMMENT ON COLUMN vendors.tax_city IS 'Tax address city';
COMMENT ON COLUMN vendors.tax_province IS 'Tax address province';
COMMENT ON COLUMN vendors.tax_postal_code IS 'Tax address postal code';
```

**Step 2: Run the migration**

Run: `cd /root/milkyhoop-dev && docker compose exec postgres psql -U milkyhoop -d milkyhoop -f /migrations/V083__vendor_extended_fields.sql`

Or via Flyway if configured.

**Step 3: Commit**

```bash
git add backend/migrations/V083__vendor_extended_fields.sql
git commit -m "feat(vendors): add migration for extended vendor fields

Adds account_number, bank details, and tax address fields.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 2: Update Vendor Schemas

**Files:**
- Modify: `backend/api_gateway/app/schemas/vendors.py`

**Step 1: Add new fields to CreateVendorRequest**

In `CreateVendorRequest` class, add after `notes` field (around line 31):

```python
    # Extended fields (m1, m2, m3, m4)
    account_number: Optional[str] = Field(None, max_length=50, description="Vendor internal account number")
    vendor_type: Optional[Literal['BADAN', 'ORANG_PRIBADI', 'LUAR_NEGERI']] = Field('BADAN', description="Business type")

    # Bank details (m3)
    bank_name: Optional[str] = Field(None, max_length=100)
    bank_account_number: Optional[str] = Field(None, max_length=50)
    bank_account_holder: Optional[str] = Field(None, max_length=255)

    # Tax address (m4) - separate from main address
    tax_address: Optional[str] = None
    tax_city: Optional[str] = Field(None, max_length=100)
    tax_province: Optional[str] = Field(None, max_length=100)
    tax_postal_code: Optional[str] = Field(None, max_length=20)

    # Opening balance (m1)
    opening_balance: Optional[int] = Field(None, ge=0, description="Opening balance in Rupiah")
    opening_balance_date: Optional[str] = Field(None, description="Opening balance date YYYY-MM-DD")
```

**Step 2: Add import for Literal at top of file**

```python
from typing import Optional, List, Dict, Any, Literal
```

**Step 3: Add same fields to UpdateVendorRequest**

In `UpdateVendorRequest` class, add after `is_active` field (around line 63):

```python
    # Extended fields
    account_number: Optional[str] = None
    vendor_type: Optional[Literal['BADAN', 'ORANG_PRIBADI', 'LUAR_NEGERI']] = None

    # Bank details
    bank_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_account_holder: Optional[str] = None

    # Tax address
    tax_address: Optional[str] = None
    tax_city: Optional[str] = None
    tax_province: Optional[str] = None
    tax_postal_code: Optional[str] = None

    # Opening balance
    opening_balance: Optional[int] = None
    opening_balance_date: Optional[str] = None
```

**Step 4: Add fields to VendorDetail**

In `VendorDetail` class, add after `notes` field (around line 116):

```python
    # Extended fields
    account_number: Optional[str] = None
    vendor_type: Optional[str] = None

    # Bank details
    bank_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_account_holder: Optional[str] = None

    # Tax address
    tax_address: Optional[str] = None
    tax_city: Optional[str] = None
    tax_province: Optional[str] = None
    tax_postal_code: Optional[str] = None

    # Opening balance
    opening_balance: Optional[int] = None
    opening_balance_date: Optional[str] = None
```

**Step 5: Add duplicate check response schema**

At end of file, add:

```python
# =============================================================================
# DUPLICATE CHECK RESPONSE
# =============================================================================

class VendorDuplicateItem(BaseModel):
    """Vendor match item for duplicate check."""
    id: str
    name: str
    company: Optional[str] = None
    npwp: Optional[str] = None


class VendorDuplicateCheckResponse(BaseModel):
    """Response for vendor duplicate check endpoint."""
    byName: List[VendorDuplicateItem] = Field(default_factory=list)
    byNpwp: List[VendorDuplicateItem] = Field(default_factory=list)
```

**Step 6: Commit**

```bash
git add backend/api_gateway/app/schemas/vendors.py
git commit -m "feat(vendors): add schemas for extended fields and duplicate check

- Add account_number, bank details, tax address, opening balance fields
- Add VendorDuplicateCheckResponse schema for duplicate check endpoint

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 3: Add Duplicate Check Endpoint

**Files:**
- Modify: `backend/api_gateway/app/routers/vendors.py`

**Step 1: Import the new schema**

At line 14-22, update imports:

```python
from ..schemas.vendors import (
    CreateVendorRequest,
    UpdateVendorRequest,
    VendorResponse,
    VendorListResponse,
    VendorDetailResponse,
    VendorAutocompleteResponse,
    VendorBalanceResponse,
    VendorDuplicateCheckResponse,
)
```

**Step 2: Add check-duplicate endpoint**

Insert after the `/autocomplete` endpoint (around line 119), before the list endpoint:

```python
# =============================================================================
# DUPLICATE CHECK (for form validation)
# =============================================================================
@router.get("/check-duplicate", response_model=VendorDuplicateCheckResponse)
async def check_duplicate(
    request: Request,
    nama: Optional[str] = Query(None, description="Vendor name to check"),
    npwp: Optional[str] = Query(None, description="NPWP to check"),
    excludeId: Optional[str] = Query(None, description="Vendor ID to exclude (for edit mode)"),
):
    """
    Check for potential duplicate vendors by name or NPWP.

    Used for form validation before creating/updating vendors.

    **Returns:**
    - `byName`: Vendors with similar names (case-insensitive, partial match)
    - `byNpwp`: Vendors with exact NPWP match
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        by_name = []
        by_npwp = []

        async with pool.acquire() as conn:
            # Check by name (partial match, case-insensitive)
            if nama and nama.strip():
                name_query = """
                    SELECT id, name, company_name, tax_id
                    FROM vendors
                    WHERE tenant_id = $1
                      AND is_active = true
                      AND name ILIKE $2
                """
                params = [ctx["tenant_id"], f"%{nama.strip()}%"]

                if excludeId:
                    name_query += " AND id != $3"
                    params.append(UUID(excludeId))

                name_query += " LIMIT 10"

                rows = await conn.fetch(name_query, *params)
                by_name = [
                    {
                        "id": str(row["id"]),
                        "name": row["name"],
                        "company": row["company_name"],
                        "npwp": row["tax_id"],
                    }
                    for row in rows
                ]

            # Check by NPWP (exact match after normalization)
            if npwp and npwp.strip():
                # Normalize NPWP: remove dots and dashes
                normalized_npwp = npwp.replace(".", "").replace("-", "").strip()

                npwp_query = """
                    SELECT id, name, company_name, tax_id
                    FROM vendors
                    WHERE tenant_id = $1
                      AND is_active = true
                      AND REPLACE(REPLACE(tax_id, '.', ''), '-', '') = $2
                """
                params = [ctx["tenant_id"], normalized_npwp]

                if excludeId:
                    npwp_query += " AND id != $3"
                    params.append(UUID(excludeId))

                npwp_query += " LIMIT 10"

                rows = await conn.fetch(npwp_query, *params)
                by_npwp = [
                    {
                        "id": str(row["id"]),
                        "name": row["name"],
                        "company": row["company_name"],
                        "npwp": row["tax_id"],
                    }
                    for row in rows
                ]

        return {
            "byName": by_name,
            "byNpwp": by_npwp
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking vendor duplicates: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Duplicate check failed")
```

**Step 3: Commit**

```bash
git add backend/api_gateway/app/routers/vendors.py
git commit -m "feat(vendors): add duplicate check endpoint GET /check-duplicate

Returns potential duplicates by name (partial match) and NPWP (exact match).
Supports excludeId parameter for edit mode.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 4: Update Create Vendor Endpoint

**Files:**
- Modify: `backend/api_gateway/app/routers/vendors.py`

**Step 1: Update INSERT query in create_vendor**

Replace the INSERT statement in `create_vendor` function (around line 407-430):

```python
            # Insert vendor
            vendor_id = await conn.fetchval("""
                INSERT INTO vendors (
                    tenant_id, code, name, contact_person, phone, email,
                    address, city, province, postal_code, tax_id,
                    payment_terms_days, credit_limit, notes,
                    account_number, vendor_type,
                    bank_name, bank_account_number, bank_account_holder,
                    tax_address, tax_city, tax_province, tax_postal_code,
                    opening_balance, opening_balance_date,
                    created_by
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                    $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, $26
                )
                RETURNING id
            """,
                ctx["tenant_id"],
                body.code,
                body.name,
                body.contact_person,
                body.phone,
                body.email,
                body.address,
                body.city,
                body.province,
                body.postal_code,
                body.tax_id,
                body.payment_terms_days,
                body.credit_limit,
                body.notes,
                body.account_number,
                body.vendor_type,
                body.bank_name,
                body.bank_account_number,
                body.bank_account_holder,
                body.tax_address,
                body.tax_city,
                body.tax_province,
                body.tax_postal_code,
                body.opening_balance,
                body.opening_balance_date,
                ctx["user_id"]
            )
```

**Step 2: Commit**

```bash
git add backend/api_gateway/app/routers/vendors.py
git commit -m "feat(vendors): update create endpoint with extended fields

Adds support for account_number, vendor_type, bank details, tax address,
and opening balance fields in POST /vendors.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 5: Update Get Vendor Detail Endpoint

**Files:**
- Modify: `backend/api_gateway/app/routers/vendors.py`

**Step 1: Update SELECT query in get_vendor**

Replace the query in `get_vendor` function (around line 233-240):

```python
            query = """
                SELECT id, code, name, contact_person, phone, email,
                       address, city, province, postal_code, tax_id,
                       payment_terms_days, credit_limit, notes,
                       account_number, vendor_type,
                       bank_name, bank_account_number, bank_account_holder,
                       tax_address, tax_city, tax_province, tax_postal_code,
                       opening_balance, opening_balance_date,
                       is_active, created_at, updated_at
                FROM vendors
                WHERE id = $1 AND tenant_id = $2
            """
```

**Step 2: Update response data in get_vendor**

Replace the return statement (around line 246-267):

```python
            return {
                "success": True,
                "data": {
                    "id": str(row["id"]),
                    "code": row["code"],
                    "name": row["name"],
                    "contact_person": row["contact_person"],
                    "phone": row["phone"],
                    "email": row["email"],
                    "address": row["address"],
                    "city": row["city"],
                    "province": row["province"],
                    "postal_code": row["postal_code"],
                    "tax_id": row["tax_id"],
                    "payment_terms_days": row["payment_terms_days"],
                    "credit_limit": row["credit_limit"],
                    "notes": row["notes"],
                    # Extended fields
                    "account_number": row["account_number"],
                    "vendor_type": row["vendor_type"],
                    "bank_name": row["bank_name"],
                    "bank_account_number": row["bank_account_number"],
                    "bank_account_holder": row["bank_account_holder"],
                    "tax_address": row["tax_address"],
                    "tax_city": row["tax_city"],
                    "tax_province": row["tax_province"],
                    "tax_postal_code": row["tax_postal_code"],
                    "opening_balance": row["opening_balance"],
                    "opening_balance_date": row["opening_balance_date"].isoformat() if row["opening_balance_date"] else None,
                    # Status and timestamps
                    "is_active": row["is_active"],
                    "created_at": row["created_at"].isoformat(),
                    "updated_at": row["updated_at"].isoformat(),
                }
            }
```

**Step 3: Commit**

```bash
git add backend/api_gateway/app/routers/vendors.py
git commit -m "feat(vendors): return extended fields in GET /vendors/:id

Returns account_number, vendor_type, bank details, tax address,
and opening balance in vendor detail response.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 6: Add Vendor Status Toggle Endpoint

**Files:**
- Modify: `backend/api_gateway/app/routers/vendors.py`

**Step 1: Add status toggle endpoint**

Add after the `update_vendor` endpoint (around line 527), before `delete_vendor`:

```python
# =============================================================================
# TOGGLE VENDOR STATUS
# =============================================================================
@router.patch("/{vendor_id}/status", response_model=VendorResponse)
async def toggle_vendor_status(
    request: Request,
    vendor_id: UUID,
    status: Literal["active", "inactive"] = Query(..., description="New status")
):
    """
    Toggle vendor active/inactive status.

    This is a convenience endpoint for quickly changing vendor status.
    Equivalent to PATCH /vendors/:id with { is_active: true/false }
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check if vendor exists
            existing = await conn.fetchrow(
                "SELECT id, name, is_active FROM vendors WHERE id = $1 AND tenant_id = $2",
                vendor_id, ctx["tenant_id"]
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Vendor not found")

            is_active = status == "active"

            # Update status
            await conn.execute("""
                UPDATE vendors
                SET is_active = $1, updated_at = NOW()
                WHERE id = $2 AND tenant_id = $3
            """, is_active, vendor_id, ctx["tenant_id"])

            logger.info(f"Vendor status changed: {vendor_id}, status={status}")

            return {
                "success": True,
                "message": "Status vendor berhasil diubah",
                "data": {
                    "id": str(vendor_id),
                    "name": existing["name"],
                    "is_active": is_active
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error toggling vendor status {vendor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update vendor status")
```

**Step 2: Commit**

```bash
git add backend/api_gateway/app/routers/vendors.py
git commit -m "feat(vendors): add status toggle endpoint PATCH /vendors/:id/status

Convenience endpoint for toggling vendor active/inactive status.
Returns Indonesian success message as per frontend requirements.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 7: Manual Testing

**Step 1: Test duplicate check endpoint**

```bash
# Test by name
curl -X GET "https://milkyhoop.com/api/vendors/check-duplicate?nama=Textile" \
  -H "Authorization: Bearer <token>"

# Test by NPWP
curl -X GET "https://milkyhoop.com/api/vendors/check-duplicate?npwp=01.234.567.8-901.000" \
  -H "Authorization: Bearer <token>"

# Test with excludeId (edit mode)
curl -X GET "https://milkyhoop.com/api/vendors/check-duplicate?nama=Textile&excludeId=<uuid>" \
  -H "Authorization: Bearer <token>"
```

Expected: Returns `{ "byName": [...], "byNpwp": [...] }` with matching vendors.

**Step 2: Test status toggle endpoint**

```bash
# Deactivate vendor
curl -X PATCH "https://milkyhoop.com/api/vendors/<uuid>/status?status=inactive" \
  -H "Authorization: Bearer <token>"

# Activate vendor
curl -X PATCH "https://milkyhoop.com/api/vendors/<uuid>/status?status=active" \
  -H "Authorization: Bearer <token>"
```

Expected: Returns `{ "success": true, "message": "Status vendor berhasil diubah", "data": {...} }`

**Step 3: Test create vendor with extended fields**

```bash
curl -X POST "https://milkyhoop.com/api/vendors" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test Vendor Extended",
    "vendor_type": "BADAN",
    "account_number": "ACC-001",
    "bank_name": "BCA",
    "bank_account_number": "1234567890",
    "bank_account_holder": "PT Test Vendor",
    "tax_address": "Jl. Pajak No. 123",
    "tax_city": "Jakarta",
    "tax_province": "DKI Jakarta",
    "tax_postal_code": "12345",
    "opening_balance": 5000000,
    "opening_balance_date": "2026-01-01"
  }'
```

Expected: Returns created vendor with all fields populated.

**Step 4: Test get vendor detail with extended fields**

```bash
curl -X GET "https://milkyhoop.com/api/vendors/<uuid>" \
  -H "Authorization: Bearer <token>"
```

Expected: Response includes all extended fields (account_number, bank details, tax address, opening balance).

---

## Task 8: Final Commit and Documentation

**Step 1: Create combined commit if needed**

If all changes pass testing, create a final squash commit or tag:

```bash
git tag -a v1.0.0-vendor-enhancements -m "Vendor module enhancements release"
```

---

## Summary of New Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/vendors/check-duplicate` | Check for duplicate vendors by name/NPWP |
| PATCH | `/api/vendors/:id/status` | Toggle vendor active/inactive status |

## Summary of Updated Endpoints

| Method | Endpoint | Changes |
|--------|----------|---------|
| POST | `/api/vendors` | Now accepts extended fields |
| PATCH | `/api/vendors/:id` | Now accepts extended fields |
| GET | `/api/vendors/:id` | Now returns extended fields |

## New Database Columns

| Column | Type | Description |
|--------|------|-------------|
| account_number | VARCHAR(50) | Vendor internal account number |
| bank_name | VARCHAR(100) | Bank name for payments |
| bank_account_number | VARCHAR(50) | Bank account number |
| bank_account_holder | VARCHAR(255) | Bank account holder name |
| tax_address | TEXT | Tax address street |
| tax_city | VARCHAR(100) | Tax address city |
| tax_province | VARCHAR(100) | Tax address province |
| tax_postal_code | VARCHAR(20) | Tax address postal code |

**Note:** `vendor_type`, `opening_balance`, `opening_balance_date` already exist from V070 migration.

---

## Out of Scope (Phase 2)

The following features are explicitly NOT included in this plan:

1. **Vendor Credit System (M4)** - Already exists in `vendor_credits.py`
2. **Default Expense Account (M2)** - Requires CoA module integration
3. **Vendor Statement (m9)** - Reporting feature
4. **AP Aging per Vendor (m8)** - Reporting feature
