"""
Arus Kas (Cash Flow Statement) — Metode Langsung (Direct Method). PSAK-compliant.
All data from journal_lines (Law 1).

Logic:
1. Net cash per journal (avoids N:M double-counting)
2. Proportional distribution across counterpart classifications
3. Label mapping from counterpart account type + psak_sub_category
"""
from decimal import Decimal
from datetime import date as date_type, datetime, timedelta
from .ledger import compute_balance, _to_date


# Label mapping: (psak_sub_category or account_type) -> display label
# psak_sub_category takes priority over account_type
LABEL_MAP_SUB = {
    "TRADE_RECEIVABLE": "Penerimaan piutang usaha",
    "TRADE_PAYABLE": "Pembayaran utang usaha",
    "INVENTORY": "Pembelian persediaan",
    "PAID_IN_CAPITAL": "Setoran modal",
    "RETAINED_EARNINGS": "Distribusi laba",
    "FIXED_ASSET": "Pembelian/penjualan aset tetap",
    "ACCUM_DEPRECIATION": "Penyusutan aset tetap",
}

LABEL_MAP_TYPE = {
    "REVENUE": "Penerimaan dari pelanggan",
    "COGS": "Pembayaran kas kepada pemasok",
    "EXPENSE": "Pembayaran beban usaha",
    "OTHER_INCOME": "Penerimaan lain-lain",
    "OTHER_EXPENSE": "Pembayaran lain-lain",
    "LIABILITY": "Pembayaran liabilitas",
    "PAYABLE": "Pembayaran utang usaha",
    "ASSET": "Penerimaan/pengeluaran aset",
    "RECEIVABLE": "Penerimaan piutang",
    "EQUITY": "Transaksi ekuitas",
}


def _get_label(account_type, psak_sub_category):
    """Get display label from psak_sub_category (priority) or account_type."""
    if psak_sub_category and psak_sub_category in LABEL_MAP_SUB:
        return LABEL_MAP_SUB[psak_sub_category]
    return LABEL_MAP_TYPE.get(account_type, f"Lain-lain ({account_type})")


async def generate_cash_flow(conn, tenant_id: str, start: str, end: str) -> dict:
    """
    Direct method cash flow statement with line items.
    Uses proportional distribution to avoid N:M double-counting.
    """
    sql = """
        WITH cash_per_journal AS (
            -- Step 1: Net cash movement per journal (avoids double-counting)
            SELECT jl.journal_id,
                   COALESCE(SUM(jl.debit), 0) - COALESCE(SUM(jl.credit), 0) AS net_cash
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            JOIN chart_of_accounts coa ON coa.id = jl.account_id
            WHERE je.status = 'POSTED'
              AND je.tenant_id = $1
              AND je.journal_date >= $2
              AND je.journal_date <= $3
              AND coa.is_cash = TRUE
            GROUP BY jl.journal_id
        ),
        counterpart_weights AS (
            -- Step 2: Each counterpart line's proportional weight within its journal
            SELECT cpj.journal_id,
                   cpj.net_cash,
                   COALESCE(counter_coa.cash_flow_category, 'OPERATING') AS flow_category,
                   counter_coa.account_type,
                   counter_coa.psak_sub_category,
                   ABS(counter_jl.debit - counter_jl.credit) AS line_amount,
                   SUM(ABS(counter_jl.debit - counter_jl.credit))
                       OVER (PARTITION BY cpj.journal_id) AS journal_total
            FROM cash_per_journal cpj
            JOIN journal_lines counter_jl ON counter_jl.journal_id = cpj.journal_id
            JOIN chart_of_accounts counter_coa ON counter_coa.id = counter_jl.account_id
            WHERE counter_coa.is_cash IS NOT TRUE
        )
        -- Step 3: Distribute net_cash proportionally across counterpart classifications
        SELECT flow_category,
               account_type,
               psak_sub_category,
               SUM(net_cash * line_amount / NULLIF(journal_total, 0)) AS net_flow
        FROM counterpart_weights
        GROUP BY flow_category, account_type, psak_sub_category
        HAVING ABS(SUM(net_cash * line_amount / NULLIF(journal_total, 0))) > 0.01
        ORDER BY flow_category, account_type, psak_sub_category
    """
    rows = await conn.fetch(sql, tenant_id, _to_date(start), _to_date(end))

    # Build items per category
    operating_items = []
    investing_items = []
    financing_items = []
    operating_total = Decimal("0")
    investing_total = Decimal("0")
    financing_total = Decimal("0")

    for r in rows:
        category = r["flow_category"]
        amount = Decimal(str(r["net_flow"]))
        label = _get_label(r["account_type"], r["psak_sub_category"])

        item = {"label": label, "amount": float(amount)}

        if category == "OPERATING" or category == "NONE":
            operating_items.append(item)
            operating_total += amount
        elif category == "INVESTING":
            investing_items.append(item)
            investing_total += amount
        elif category == "FINANCING":
            financing_items.append(item)
            financing_total += amount
        else:
            # Unknown category → default to operating
            operating_items.append(item)
            operating_total += amount

    # Merge items with same label
    def merge_items(items):
        merged = {}
        for item in items:
            key = item["label"]
            if key in merged:
                merged[key]["amount"] += item["amount"]
            else:
                merged[key] = {"label": key, "amount": item["amount"]}
        return [v for v in merged.values() if abs(v["amount"]) > 0.01]

    operating_items = merge_items(operating_items)
    investing_items = merge_items(investing_items)
    financing_items = merge_items(financing_items)

    net_change = operating_total + investing_total + financing_total

    # Opening cash = all cash account balances as of day before period start
    day_before = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    cash_start = await compute_balance(conn, tenant_id, {"is_cash": True}, end_date=day_before)
    cash_end = await compute_balance(conn, tenant_id, {"is_cash": True}, end_date=end)

    return {
        "period": {"start": start, "end": end},
        "operasi": {
            "total": float(operating_total),
            "items": operating_items,
        },
        "investasi": {
            "total": float(investing_total),
            "items": investing_items,
        },
        "pendanaan": {
            "total": float(financing_total),
            "items": financing_items,
        },
        "kenaikan_kas_bersih": float(net_change),
        "kas_awal": float(cash_start),
        "kas_akhir": float(cash_end),
        "_reconciled": abs(cash_start + net_change - cash_end) < Decimal("0.01"),
        "basis_applied": "cash",
    }
