"""
Calculation Engine — Code-driven numerical queries.
Fetches data via internal REST, computes AVG/SUM/COUNT/MAX/MIN/RANK in Python.
Zero LLM calls. ~10ms compute time.
"""
import logging
import httpx
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("unified_chat")


@dataclass
class CalculationTemplate:
    calc_type: str  # "AVG", "SUM", "COUNT", "MAX", "MIN", "RANK"
    source_endpoint: str  # "/api/items"
    source_field: str = ""  # "harga_jual" (not needed for COUNT)
    filter_params: dict = field(default_factory=dict)
    limit: int = 200  # fetch enough for accurate aggregation
    label: str = ""
    format_as_currency: bool = False
    list_field: str = ""  # key containing the list in response (e.g. "top_accounts")
    name_field: str = ""  # name field within each list item (e.g. "account_name")


# ── Template Registry ──────────────────────────────────────────────────────

CALCULATION_TEMPLATES: dict[str, CalculationTemplate] = {
    # Items
    "calc_avg_harga_jual": CalculationTemplate(
        calc_type="AVG",
        source_endpoint="/api/items",
        source_field="harga_jual",
        filter_params={"status": "active", "limit": "100"},
        label="Rata-rata Harga Jual Item Aktif",
        format_as_currency=True,
    ),
    "calc_sum_harga_jual": CalculationTemplate(
        calc_type="SUM",
        source_endpoint="/api/items",
        source_field="harga_jual",
        filter_params={"status": "active", "limit": "100"},
        label="Total Harga Jual Semua Item Aktif",
        format_as_currency=True,
    ),
    "calc_count_items_active": CalculationTemplate(
        calc_type="COUNT",
        source_endpoint="/api/items",
        filter_params={"status": "active", "limit": "1"},
        label="Jumlah Item Aktif",
    ),
    "calc_count_items_inactive": CalculationTemplate(
        calc_type="COUNT",
        source_endpoint="/api/items",
        filter_params={"status": "inactive", "limit": "1"},
        label="Jumlah Item Tidak Aktif",
    ),
    "calc_rank_items_by_price": CalculationTemplate(
        calc_type="RANK",
        source_endpoint="/api/items",
        source_field="harga_jual",
        filter_params={"status": "active", "limit": "100"},
        label="Item Berdasarkan Harga Jual (Termahal)",
        format_as_currency=True,
    ),
    "calc_avg_harga_beli": CalculationTemplate(
        calc_type="AVG",
        source_endpoint="/api/items",
        source_field="harga_beli",
        filter_params={"status": "active", "limit": "100"},
        label="Rata-rata Harga Beli Item Aktif",
        format_as_currency=True,
    ),
    "calc_sum_stok": CalculationTemplate(
        calc_type="SUM",
        source_endpoint="/api/items",
        source_field="stok",
        filter_params={"status": "active", "limit": "100"},
        label="Total Stok Semua Item Aktif",
    ),
    # Customers
    "calc_count_customers_active": CalculationTemplate(
        calc_type="COUNT",
        source_endpoint="/api/customers",
        filter_params={"limit": "1"},
        label="Jumlah Pelanggan Aktif",
    ),
    # Vendors
    "calc_count_vendors_active": CalculationTemplate(
        calc_type="COUNT",
        source_endpoint="/api/vendors",
        filter_params={"limit": "1"},
        label="Jumlah Vendor Aktif",
    ),
    # ── AR/AP Outstanding (ARAP Rule 5/6 compliant — journal-derived endpoints) ──
    "calc_count_bills_outstanding": CalculationTemplate(
        calc_type="SUMMARY_FIELD",
        source_endpoint="/api/bills/outstanding-summary",
        source_field="total_count",
        label="Jumlah Faktur Pembelian Belum Lunas",
    ),
    "calc_sum_bills_outstanding": CalculationTemplate(
        calc_type="SUMMARY_FIELD",
        source_endpoint="/api/bills/outstanding-summary",
        source_field="total_outstanding",
        label="Total Hutang Outstanding (AP)",
        format_as_currency=True,
    ),
    "calc_count_invoices_outstanding": CalculationTemplate(
        calc_type="SUMMARY_FIELD",
        source_endpoint="/api/sales-invoices/outstanding-summary",
        source_field="total",
        label="Jumlah Faktur Penjualan Belum Lunas",
    ),
    "calc_sum_invoices_outstanding": CalculationTemplate(
        calc_type="SUMMARY_FIELD",
        source_endpoint="/api/sales-invoices/outstanding-summary",
        source_field="total_outstanding",
        label="Total Piutang Outstanding (AR)",
        format_as_currency=True,
    ),
    # ── Bank & Kas ──
    "calc_count_bank_accounts": CalculationTemplate(
        calc_type="COUNT",
        source_endpoint="/api/bank-accounts",
        filter_params={"limit": "1"},
        label="Jumlah Rekening Kas & Bank",
    ),
    "calc_sum_bank_balance": CalculationTemplate(
        calc_type="SUMMARY_FIELD",
        source_endpoint="/api/kasbank/stats",
        source_field="total_balance",
        label="Total Saldo Kas & Bank",
        format_as_currency=True,
    ),
    # ── Expenses ──
    "calc_count_expenses_month": CalculationTemplate(
        calc_type="SUMMARY_FIELD",
        source_endpoint="/api/expenses/summary",
        source_field="total_count",
        label="Jumlah Pengeluaran Bulan Ini",
    ),
    # -- Batch 1 Group A --
    "calc_rank_items_by_stock": CalculationTemplate(
        calc_type="RANK",
        source_endpoint="/api/items",
        source_field="stok",
        filter_params={"status": "active", "limit": "100"},
        label="Item Berdasarkan Stok (Terbanyak)",
    ),
    "calc_sum_harga_beli": CalculationTemplate(
        calc_type="SUM",
        source_endpoint="/api/items",
        source_field="harga_beli",
        filter_params={"status": "active", "limit": "100"},
        label="Total Harga Beli Semua Item Aktif",
        format_as_currency=True,
    ),
    "calc_rank_customers_by_ar": CalculationTemplate(
        calc_type="RANK",
        source_endpoint="/api/customers",
        source_field="outstanding_balance",
        filter_params={"limit": "100"},
        label="Pelanggan Berdasarkan Piutang (Terbesar)",
        format_as_currency=True,
    ),
    "calc_rank_vendors_by_ap": CalculationTemplate(
        calc_type="RANK",
        source_endpoint="/api/vendors",
        source_field="ap_balance",
        filter_params={"limit": "100"},
        label="Vendor Berdasarkan Hutang (Terbesar)",
        format_as_currency=True,
    ),
    "calc_sum_sales_this_month": CalculationTemplate(
        calc_type="SUMMARY_FIELD",
        source_endpoint="/api/sales-invoices/summary",
        source_field="total_outstanding",
        label="Total Penjualan Outstanding Bulan Ini",
        format_as_currency=True,
    ),
    "calc_sum_purchases_this_month": CalculationTemplate(
        calc_type="SUMMARY_FIELD",
        source_endpoint="/api/bills/summary",
        source_field="total_amount",
        label="Total Pembelian Bulan Ini",
        format_as_currency=True,
    ),
    "calc_sum_expenses_this_month": CalculationTemplate(
        calc_type="SUMMARY_FIELD",
        source_endpoint="/api/expenses/summary",
        source_field="total_amount",
        label="Total Pengeluaran Bulan Ini",
        format_as_currency=True,
    ),
    # -- Batch 1 Group D --
    "calc_sum_received_this_month": CalculationTemplate(
        calc_type="SUMMARY_FIELD",
        source_endpoint="/api/receive-payments/summary",
        source_field="total_received",
        label="Total Diterima Bulan Ini",
        format_as_currency=True,
    ),
    "calc_sum_paid_this_month": CalculationTemplate(
        calc_type="SUMMARY_FIELD",
        source_endpoint="/api/bill-payments/summary",
        source_field="total_paid",
        label="Total Dibayar Bulan Ini",
        format_as_currency=True,
    ),
    "calc_count_sales_invoices_active": CalculationTemplate(
        calc_type="COUNT",
        source_endpoint="/api/sales-invoices",
        filter_params={"status": "active", "limit": "1"},
        label="Faktur Penjualan Aktif",
    ),
    "calc_count_bills_active": CalculationTemplate(
        calc_type="COUNT",
        source_endpoint="/api/bills",
        filter_params={"status": "active", "limit": "1"},
        label="Faktur Pembelian Aktif",
    ),
    "calc_sum_all_bank_balances": CalculationTemplate(
        calc_type="SUM",
        source_endpoint="/api/bank-accounts",
        source_field="current_balance",
        label="Total Saldo Semua Rekening",
        format_as_currency=True,
    ),
    # -- Batch 2 Calculation Intents --
    "calc_rank_expense_accounts": CalculationTemplate(
        calc_type="SUMMARY_LIST",
        source_endpoint="/api/expenses/summary",
        source_field="total_amount",
        list_field="top_accounts",
        name_field="account_name",
        label="Pengeluaran per Akun (Terbesar)",
        format_as_currency=True,
    ),
    "calc_count_customers_inactive": CalculationTemplate(
        calc_type="COUNT",
        source_endpoint="/api/customers",
        filter_params={"is_active": "false"},
        label="Pelanggan Tidak Aktif",
    ),
    "calc_count_vendors_inactive": CalculationTemplate(
        calc_type="COUNT",
        source_endpoint="/api/vendors",
        filter_params={"is_active": "false"},
        label="Vendor Tidak Aktif",
    ),
    "calc_count_expenses_this_month": CalculationTemplate(
        calc_type="SUMMARY_FIELD",
        source_endpoint="/api/expenses/summary",
        source_field="total_count",
        label="Jumlah Pengeluaran Bulan Ini",
    ),
    # -- Batch 3 Cross-module Calc Intents --
    "calc_profit_margin_per_item": CalculationTemplate(
        calc_type="SUMMARY_LIST",
        source_endpoint="/api/inventory/product-margins",
        source_field="unit_margin",
        list_field="products",
        name_field="product_name",
        label="Margin Keuntungan per Produk",
        format_as_currency=True,
    ),
    "calc_top_selling_items": CalculationTemplate(
        calc_type="SUMMARY_LIST",
        source_endpoint="/api/inventory/top-products",
        source_field="total_qty_sold",
        list_field="products",
        name_field="product_name",
        label="Produk Terlaris",
        format_as_currency=False,
    ),
    # ── Manufacturing ──
    "calc_count_work_orders_active": CalculationTemplate(
        calc_type="COUNT",
        source_endpoint="/api/production",
        filter_params={"status": "in_progress", "limit": "100"},
        label="Work Order Aktif (In Progress)",
    ),
    "calc_count_bom_active": CalculationTemplate(
        calc_type="COUNT",
        source_endpoint="/api/bom",
        filter_params={"status": "active", "limit": "100"},
        label="BOM Aktif",
    ),
    "calc_count_work_orders_draft": CalculationTemplate(
        calc_type="COUNT",
        source_endpoint="/api/production",
        filter_params={"status": "draft", "limit": "100"},
        label="Work Order Draft (Belum Release)",
    ),
    "calc_count_work_centers": CalculationTemplate(
        calc_type="COUNT",
        source_endpoint="/api/bom/work-centers",
        label="Jumlah Work Center",
    ),
    "calc_rank_work_orders_by_quantity": CalculationTemplate(
        calc_type="RANK",
        source_endpoint="/api/production",
        source_field="planned_quantity",
        filter_params={"limit": "100"},
        name_field="order_number",
        label="Work Order Berdasarkan Jumlah Produksi",
    ),
}


def get_calculation_template(intent: str) -> Optional[CalculationTemplate]:
    """Lookup template by intent key."""
    return CALCULATION_TEMPLATES.get(intent)


def is_calculation_intent(intent: str) -> bool:
    """Check if intent is a calculation intent."""
    return intent in CALCULATION_TEMPLATES


def _extract_items_list(data) -> list:
    """Extract list of items from various API response formats."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "data", "results", "customers", "vendors"):
            if key in data and isinstance(data[key], list):
                return data[key]
    return []


def _extract_total_count(data) -> Optional[int]:
    """Extract total count from API response metadata."""
    if isinstance(data, dict):
        for key in ("total", "total_count", "count", "total_items"):
            if key in data and isinstance(data[key], (int, float)):
                return int(data[key])
    return None


def _safe_float(val) -> Optional[float]:
    """Safely convert value to float."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _format_number(val: float, as_currency: bool = False) -> str:
    """Format number for display."""
    if as_currency:
        formatted = "Rp {:,.0f}".format(val)
        return formatted.replace(",", ".")
    if val == int(val):
        formatted = "{:,}".format(int(val))
        return formatted.replace(",", ".")
    formatted = "{:,.2f}".format(val)
    return formatted.replace(",", ".")


async def execute_calculation(
    template: CalculationTemplate,
    auth_token: str,
    tenant_id: str,
) -> dict:
    """
    Fetch data from REST endpoint + calculate in code. Zero LLM.
    """
    base_url = "http://localhost:8000"
    headers = {
        "Authorization": "Bearer " + auth_token,
        "Content-Type": "application/json",
        "X-Tenant-ID": tenant_id,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                base_url + template.source_endpoint,
                params=template.filter_params,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning("[CALC_ENGINE] REST call failed: %s", e)
        err_msg = str(e)[:100]
        return {"type": "error", "message": "Gagal mengambil data: " + err_msg}

    # SUMMARY_FIELD: extract a specific field from summary endpoint response
    # Used for ARAP-compliant endpoints that return pre-computed journal-derived values
    if template.calc_type == "SUMMARY_FIELD":
        # Navigate nested response: {data: {field}} or {field} or {success: true, data: {field}}
        source = data
        if (
            isinstance(source, dict)
            and "data" in source
            and isinstance(source["data"], dict)
        ):
            source = source["data"]
        # Also check counts sub-object
        val = None
        if isinstance(source, dict):
            val = source.get(template.source_field)
            if val is None and "counts" in source:
                val = source["counts"].get(template.source_field)
            if val is None and "amounts" in source:
                val = source["amounts"].get(template.source_field)
        if val is None:
            val = 0
        result_val = float(val)
        return {
            "type": "scalar",
            "value": result_val,
            "label": template.label,
            "formatted": _format_number(result_val, template.format_as_currency),
            "count": int(result_val) if not template.format_as_currency else 1,
            "source_total": None,
        }

    # SUMMARY_LIST: extract a list from summary endpoint, rank by value
    if template.calc_type == "SUMMARY_LIST":
        source = data
        if (
            isinstance(source, dict)
            and "data" in source
            and isinstance(source["data"], dict)
        ):
            source = source["data"]
        _list_data = (
            source.get(template.list_field, []) if isinstance(source, dict) else []
        )
        if not _list_data:
            return {"type": "error", "message": "Data tidak tersedia"}
        items = []
        for entry in _list_data:
            val = entry.get(template.source_field, 0)
            if isinstance(val, str):
                val = float(val)
            name = entry.get(template.name_field, "Unknown")
            items.append({"name": name, "value": float(val)})
        items.sort(key=lambda x: x["value"], reverse=True)
        items = items[:10]
        for item in items:
            if template.format_as_currency:
                item["formatted_value"] = f"Rp {int(item['value']):,}".replace(",", ".")
            else:
                item["formatted_value"] = f"{item['value']:,.0f}".replace(",", ".")
        return {
            "type": "rank",
            "label": template.label,
            "items": items,
            "total_count": len(_list_data),
        }

    items = _extract_items_list(data)
    total_from_api = _extract_total_count(data)

    # For COUNT, prefer API total (more accurate than len(items) which is limited)
    if template.calc_type == "COUNT":
        count = total_from_api if total_from_api is not None else len(items)
        return {
            "type": "scalar",
            "value": count,
            "label": template.label,
            "formatted": _format_number(count),
            "count": count,
            "source_total": total_from_api,
        }

    # Extract numeric values for field-based calculations
    values = []
    items_with_values = []
    for item in items:
        val = None
        for field_name in (
            template.source_field,
            template.source_field.replace("_", ""),
        ):
            val = _safe_float(item.get(field_name))
            if val is not None:
                break
        # Also try common aliases
        if val is None and template.source_field == "harga_jual":
            val = _safe_float(item.get("sales_price"))
        if val is None and template.source_field == "harga_beli":
            val = _safe_float(item.get("purchase_price"))
        if val is None and template.source_field == "stok":
            val = _safe_float(
                item.get("stock") or item.get("quantity") or item.get("current_stock")
            )

        if val is not None:
            values.append(val)
            items_with_values.append((item, val))

    if not values and template.calc_type != "COUNT":
        return {
            "type": "scalar",
            "value": 0,
            "label": template.label,
            "formatted": "Tidak ada data",
            "count": 0,
            "source_total": total_from_api,
        }

    result_value = 0.0
    if template.calc_type == "AVG":
        result_value = sum(values) / len(values)
    elif template.calc_type == "SUM":
        result_value = sum(values)
    elif template.calc_type == "MAX":
        result_value = max(values)
    elif template.calc_type == "MIN":
        result_value = min(values)
    elif template.calc_type == "RANK":
        sorted_items = sorted(items_with_values, key=lambda x: -x[1])
        top_items = []
        rec_items = []  # REC-ready items for session state (for pronoun/ordinal follow-ups)
        for item, val in sorted_items[:10]:
            name = (
                item.get("nama_produk") or item.get("name") or item.get("nama") or "?"
            )
            _id = item.get("id") or item.get("uuid") or item.get("_id")
            _ref = (
                item.get("invoice_number")
                or item.get("bill_number")
                or item.get("expense_number")
                or item.get("document_number")
                or item.get("code")
            )
            top_items.append(
                {
                    "name": name,
                    "value": val,
                    "formatted_value": _format_number(val, template.format_as_currency),
                }
            )
            rec_items.append(
                {
                    "_name": name,
                    "_id": _id,
                    "_ref": _ref,
                    "_amount": val,
                }
            )
        return {
            "type": "rank",
            "data": top_items,
            "rec_items": rec_items,
            "label": template.label,
            "count": len(values),
            "source_total": total_from_api,
        }
    else:
        return {"type": "error", "message": "Unknown calc_type: " + template.calc_type}

    return {
        "type": "scalar",
        "value": result_value,
        "label": template.label,
        "formatted": _format_number(result_value, template.format_as_currency),
        "count": len(values),
        "source_total": total_from_api,
    }


def format_calculation_result(result: dict) -> str:
    """Format calculation result as human-readable text for LLM polish."""
    if result.get("type") == "error":
        return result.get("message", "Gagal menghitung.")

    rtype = result.get("type")
    rlabel = result.get("label", "")
    rformatted = result.get("formatted", "")
    rcount = result.get("count", 0)

    if rtype == "scalar":
        text = "**" + rlabel + "**: " + rformatted
        if rcount:
            text += "\n(Berdasarkan " + str(rcount) + " data)"
        return text

    if rtype == "rank":
        rdata = result.get("data", result.get("items", []))
        lines = ["**" + rlabel + "** (Top " + str(len(rdata)) + ")\n"]
        lines.append("| No | Nama | Harga |")
        lines.append("|---:|------|------:|")
        for i, item in enumerate(rdata, 1):
            iname = item.get("name", "?")
            ival = item.get("formatted_value", "?")
            lines.append("| " + str(i) + " | " + iname + " | " + ival + " |")
        if rcount:
            lines.append("\n_Total " + str(rcount) + " item dihitung_")
        return "\n".join(lines)

    return str(result)
