"""
Core ledger query — all reports derive from this single function.
Law 1: journal_lines = single source of truth.

Column names (PRODUCTION):
  journal_entries.journal_date (NOT posting_date)
  journal_entries.journal_number (NOT number)
  journal_lines.journal_id (NOT journal_entry_id)
  chart_of_accounts.account_code (NOT code)
"""
from datetime import date as date_type
from decimal import Decimal
from typing import Optional


def _to_date(s: str) -> date_type:
    """Convert YYYY-MM-DD string to date object for asyncpg."""
    parts = s.split("-")
    return date_type(int(parts[0]), int(parts[1]), int(parts[2]))


def _build_where(tenant_id, account_filter, start_date, end_date):
    """Shared WHERE builder for compute_balance and compute_balance_detail."""
    conditions = ["je.status = 'POSTED'", "je.tenant_id = $1"]
    params: list = [tenant_id]
    idx = 2

    if end_date:
        conditions.append(f"je.journal_date <= ${idx}")
        params.append(_to_date(end_date) if isinstance(end_date, str) else end_date)
        idx += 1

    if start_date:
        conditions.append(f"je.journal_date >= ${idx}")
        params.append(_to_date(start_date) if isinstance(start_date, str) else start_date)
        idx += 1

    for key, val in account_filter.items():
        if key == "account_types":
            conditions.append(f"coa.account_type = ANY(${idx}::text[])")
            params.append(val)
            idx += 1
        elif key == "account_type":
            conditions.append(f"coa.account_type = ${idx}")
            params.append(val)
            idx += 1
        elif key == "is_cash":
            conditions.append(f"coa.is_cash = ${idx}")
            params.append(val)
            idx += 1
        elif key == "psak_sub_category":
            conditions.append(f"coa.psak_sub_category = ${idx}")
            params.append(val)
            idx += 1
        elif key == "psak_sub_category_not_in":
            placeholders = ", ".join(f"${idx + i}" for i in range(len(val)))
            conditions.append(
                f"(coa.psak_sub_category IS NULL OR coa.psak_sub_category NOT IN ({placeholders}))"
            )
            for v in val:
                params.append(v)
                idx += 1
        elif key == "cash_flow_category":
            conditions.append(f"coa.cash_flow_category = ${idx}")
            params.append(val)
            idx += 1
        elif key == "account_id":
            conditions.append(f"jl.account_id = ${idx}::uuid")
            params.append(val)
            idx += 1
        elif key == "is_header_false":
            conditions.append("coa.is_header = FALSE")
        elif key == "cash_only":
            if val:
                conditions.append("""
                    EXISTS (
                        SELECT 1 FROM journal_lines cash_jl
                        JOIN chart_of_accounts cash_coa ON cash_coa.id = cash_jl.account_id
                        WHERE cash_jl.journal_id = jl.journal_id
                        AND cash_coa.is_cash = TRUE
                    )
                """)

    return conditions, params


async def compute_balance(
    conn,
    tenant_id: str,
    account_filter: dict,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Decimal:
    """
    Compute account balance from journal_lines.

    account_filter examples:
      {"account_type": "ASSET", "is_cash": True}
      {"account_types": ["ASSET", "RECEIVABLE"]}  # multi-type
      {"psak_sub_category": "TRADE_RECEIVABLE"}
      {"account_id": "some-uuid"}
    """
    conditions, params = _build_where(tenant_id, account_filter, start_date, end_date)

    sql = f"""
        SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) AS balance
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE {' AND '.join(conditions)}
    """
    row = await conn.fetchrow(sql, *params)
    return Decimal(str(row["balance"])) if row else Decimal("0")


async def compute_balance_detail(
    conn,
    tenant_id: str,
    account_filter: dict,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list:
    """
    Same as compute_balance() but returns BREAKDOWN per account.
    Returns: [{"account_code": "1-10200", "account_name": "Kas Kecil", "balance": Decimal(...)}, ...]
    Only includes accounts with non-zero balance.
    """
    conditions, params = _build_where(tenant_id, account_filter, start_date, end_date)

    sql = f"""
        SELECT coa.account_code,
               coa.name AS account_name,
               COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) AS balance
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE {' AND '.join(conditions)}
        GROUP BY coa.account_code, coa.name
        HAVING ABS(COALESCE(SUM(jl.debit) - SUM(jl.credit), 0)) > 0.001
        ORDER BY coa.account_code
    """
    rows = await conn.fetch(sql, *params)
    return [
        {
            "account_code": r["account_code"],
            "account_name": r["account_name"],
            "balance": Decimal(str(r["balance"])),
        }
        for r in rows
    ]


async def compute_balances_grouped(
    conn,
    tenant_id: str,
    group_by: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    extra_filter: Optional[dict] = None,
) -> dict:
    """
    Compute balances grouped by a CoA column (account_type, psak_sub_category, etc).
    Returns dict mapping group_value -> Decimal balance.
    """
    conditions = ["je.status = 'POSTED'", "je.tenant_id = $1"]
    params: list = [tenant_id]
    idx = 2

    if end_date:
        conditions.append(f"je.journal_date <= ${idx}")
        params.append(_to_date(end_date) if isinstance(end_date, str) else end_date)
        idx += 1

    if start_date:
        conditions.append(f"je.journal_date >= ${idx}")
        params.append(_to_date(start_date) if isinstance(start_date, str) else start_date)
        idx += 1

    if extra_filter:
        for key, val in extra_filter.items():
            col_map = {
                "account_type": "coa.account_type",
                "account_types": None,
                "is_cash": "coa.is_cash",
                "psak_sub_category": "coa.psak_sub_category",
            }
            col = col_map.get(key)
            if key == "account_types":
                conditions.append(f"coa.account_type = ANY(${idx}::text[])")
                params.append(val)
                idx += 1
            elif col:
                conditions.append(f"{col} = ${idx}")
                params.append(val)
                idx += 1

    group_col_map = {
        "account_type": "coa.account_type",
        "psak_sub_category": "coa.psak_sub_category",
        "cash_flow_category": "coa.cash_flow_category",
        "is_cash": "coa.is_cash",
    }
    group_col = group_col_map.get(group_by, f"coa.{group_by}")

    sql = f"""
        SELECT {group_col} AS grp,
               COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) AS balance
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE {' AND '.join(conditions)}
        GROUP BY {group_col}
    """
    rows = await conn.fetch(sql, *params)
    return {r["grp"]: Decimal(str(r["balance"])) for r in rows if r["grp"] is not None}
