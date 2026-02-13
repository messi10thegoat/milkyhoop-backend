"""
Layer 2: ACCOUNTING_RULES
Validates business rules against the database:
- Fiscal period is open for the posting date
- Vendor/customer exists and is active
- Chart of accounts entries exist and are active
- Master data name uniqueness checks
"""
import logging
from datetime import date, datetime
from typing import Optional

from .base import BaseValidator, ValidationContext

logger = logging.getLogger(__name__)

# Proto ActionType enum values for master data
ACTION_TYPE_CREATE_CUSTOMER = 0
ACTION_TYPE_UPDATE_CUSTOMER = 1
ACTION_TYPE_CREATE_VENDOR = 2
ACTION_TYPE_CREATE_PRODUCT = 3

# Master data action types - skip vendor/customer existence checks for these
MASTER_DATA_ACTIONS = {
    ACTION_TYPE_CREATE_CUSTOMER,
    ACTION_TYPE_UPDATE_CUSTOMER,
    ACTION_TYPE_CREATE_VENDOR,
    ACTION_TYPE_CREATE_PRODUCT,
}


def _parse_date(value) -> Optional[date]:
    """Parse a date string to date object. Supports YYYY-MM-DD and ISO 8601."""
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not value or not isinstance(value, str):
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


class AccountingValidator(BaseValidator):
    """Layer 2: Validate accounting rules against the database."""

    async def validate(self, ctx: ValidationContext) -> None:
        logger.debug("Running ACCOUNTING_RULES validation")
        payload = ctx.payload

        # --- Check fiscal period for posting date ---
        posting_date_raw = payload.get("posting_date") or payload.get("date") or payload.get("invoice_date")
        if posting_date_raw:
            posting_date = _parse_date(posting_date_raw)
            if posting_date is None:
                ctx.add_error(
                    layer="ACCOUNTING_RULES",
                    code="INVALID_DATE_FORMAT",
                    message=f"Cannot parse posting date: {posting_date_raw}",
                    blocking=True,
                    field_name="posting_date",
                )
            else:
                row = await ctx.pool.fetchrow(
                    """
                    SELECT status FROM fiscal_periods
                    WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2
                    ORDER BY start_date DESC LIMIT 1
                    """,
                    ctx.tenant_id,
                    posting_date,
                )
                if row is None:
                    ctx.add_error(
                        layer="ACCOUNTING_RULES",
                        code="NO_FISCAL_PERIOD",
                        message=f"No fiscal period found for date {posting_date}",
                        blocking=True,
                        field_name="posting_date",
                    )
                elif row["status"] != "OPEN":
                    period_status = row["status"]
                    ctx.add_error(
                        layer="ACCOUNTING_RULES",
                        code="PERIOD_CLOSED",
                        message=f"Fiscal period for {posting_date} is {period_status} (must be OPEN)",
                        blocking=True,
                        field_name="posting_date",
                    )

        # --- Master data vs document/payment actions ---
        if ctx.action_type in MASTER_DATA_ACTIONS:
            # For master data creation, check name uniqueness
            await self._check_master_data_unique(ctx)
        else:
            # For documents/payments, check vendor/customer existence

            # --- Check vendor exists (for purchase-related actions) ---
            vendor_id = payload.get("vendor_id") or payload.get("vendor")
            if vendor_id:
                vendor_row = await ctx.pool.fetchrow(
                    """
                    SELECT id, is_active FROM vendors
                    WHERE tenant_id = $1 AND (id::text = $2 OR LOWER(name) LIKE ('%' || LOWER($2) || '%'))
                    LIMIT 1
                    """,
                    ctx.tenant_id,
                    str(vendor_id),
                )
                if vendor_row is None:
                    ctx.add_error(
                        layer="ACCOUNTING_RULES",
                        code="VENDOR_NOT_FOUND",
                        message=f"Vendor not found: {vendor_id}",
                        blocking=True,
                        field_name="vendor_id",
                    )
                elif not vendor_row["is_active"]:
                    ctx.add_error(
                        layer="ACCOUNTING_RULES",
                        code="VENDOR_INACTIVE",
                        message=f"Vendor is inactive: {vendor_id}",
                        blocking=True,
                        field_name="vendor_id",
                    )

            # --- Check customer exists (for sales-related actions) ---
            customer_id = payload.get("customer_id") or payload.get("customer")
            if customer_id:
                customer_row = await ctx.pool.fetchrow(
                    """
                    SELECT id, is_active FROM customers
                    WHERE tenant_id = $1 AND (id::text = $2 OR LOWER(nama) LIKE ('%' || LOWER($2) || '%'))
                    LIMIT 1
                    """,
                    ctx.tenant_id,
                    str(customer_id),
                )
                if customer_row is None:
                    ctx.add_warning(
                        layer="ACCOUNTING_RULES",
                        code="CUSTOMER_NOT_FOUND",
                        message=f"Customer not found: {customer_id}",
                        field_name="customer_id",
                    )
                elif not customer_row["is_active"]:
                    ctx.add_error(
                        layer="ACCOUNTING_RULES",
                        code="CUSTOMER_INACTIVE",
                        message=f"Customer is inactive: {customer_id}",
                        blocking=True,
                        field_name="customer_id",
                    )

        # --- Check chart of accounts entries ---
        accounts_to_check = set()
        # Direct account references
        for key in ("debit_account", "credit_account", "account_code", "expense_account"):
            acc = payload.get(key)
            if acc:
                accounts_to_check.add(str(acc))
        # Items with account codes
        items = payload.get("items") or payload.get("line_items") or []
        for item in items:
            if isinstance(item, dict):
                acc = item.get("account_code") or item.get("account")
                if acc:
                    accounts_to_check.add(str(acc))

        for account_code in accounts_to_check:
            acc_row = await ctx.pool.fetchrow(
                """
                SELECT account_code, is_active FROM chart_of_accounts
                WHERE tenant_id = $1 AND account_code = $2
                """,
                ctx.tenant_id,
                account_code,
            )
            if acc_row is None:
                ctx.add_error(
                    layer="ACCOUNTING_RULES",
                    code="ACCOUNT_NOT_FOUND",
                    message=f"Account not found: {account_code}",
                    blocking=True,
                    field_name="account_code",
                )
            elif not acc_row["is_active"]:
                ctx.add_error(
                    layer="ACCOUNTING_RULES",
                    code="ACCOUNT_INACTIVE",
                    message=f"Account is inactive: {account_code}",
                    blocking=True,
                    field_name="account_code",
                )

        logger.debug("ACCOUNTING_RULES validation completed")

    async def _check_master_data_unique(self, ctx: ValidationContext) -> None:
        """Check name uniqueness for master data creation."""
        action_type = ctx.action_type
        payload = ctx.payload
        tenant_id = ctx.tenant_id

        if action_type == ACTION_TYPE_CREATE_VENDOR:
            name = payload.get("vendor_name") or payload.get("name", "")
            if name:
                existing = await ctx.pool.fetchrow(
                    "SELECT id, name FROM vendors WHERE tenant_id = $1 AND LOWER(name) = LOWER($2) AND is_active = true",
                    tenant_id, name.strip()
                )
                if existing:
                    ctx.add_warning(
                        layer="ACCOUNTING_RULES",
                        code="VENDOR_NAME_DUPLICATE",
                        message=f"Vendor '{name}' sudah terdaftar (ID: {existing['id']}). Maksudnya vendor ini, atau buat baru dengan nama berbeda?",
                        field_name="vendor_name",
                    )

        elif action_type == ACTION_TYPE_CREATE_CUSTOMER:
            name = payload.get("customer_name") or payload.get("name", "")
            if name:
                existing = await ctx.pool.fetchrow(
                    "SELECT id, nama FROM customers WHERE tenant_id = $1 AND LOWER(nama) = LOWER($2) AND is_active = true",
                    tenant_id, name.strip()
                )
                if existing:
                    ctx.add_warning(
                        layer="ACCOUNTING_RULES",
                        code="CUSTOMER_NAME_DUPLICATE",
                        message=f"Pelanggan '{name}' sudah terdaftar (ID: {existing['id']}). Maksudnya pelanggan ini?",
                        field_name="customer_name",
                    )

        elif action_type == ACTION_TYPE_CREATE_PRODUCT:
            name = payload.get("product_name") or payload.get("name", "")
            if name:
                existing = await ctx.pool.fetchrow(
                    "SELECT id, nama_produk FROM products WHERE tenant_id = $1 AND LOWER(nama_produk) = LOWER($2) AND deleted_at IS NULL",
                    tenant_id, name.strip()
                )
                if existing:
                    ctx.add_warning(
                        layer="ACCOUNTING_RULES",
                        code="PRODUCT_NAME_DUPLICATE",
                        message=f"Produk '{name}' sudah terdaftar (ID: {existing['id']}). Maksudnya produk ini?",
                        field_name="product_name",
                    )

            # Check SKU uniqueness
            sku = payload.get("sku")
            if sku:
                existing_sku = await ctx.pool.fetchrow(
                    "SELECT id, nama_produk FROM products WHERE tenant_id = $1 AND sku = $2 AND deleted_at IS NULL",
                    tenant_id, sku
                )
                if existing_sku:
                    ctx.add_error(
                        layer="ACCOUNTING_RULES",
                        code="SKU_DUPLICATE",
                        message=f"SKU '{sku}' sudah dipakai oleh produk '{existing_sku['nama_produk']}'.",
                        blocking=True,
                        field_name="sku",
                    )
