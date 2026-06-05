"""
Payroll Calculation Engine

Handles: TER PPh 21 (PP 58/2023), BPJS (5 components), proration, overtime.
All amounts use Decimal for precision.

Account references — Law 27 (Fase D4.3): callers use `AccountRole.*` from
`app.services.role_resolver`, NEVER literal codes. The previous module-level
`COA_*` constants duplicated the role catalog and were dropped — single
source of truth is `account_roles` table + `role_resolver._CATALOG`.

Roles available for payroll posting paths (V162 mapping, 7/7 tenants):
  AccountRole.SALARY_EXPENSE       -> 5-20100 Beban Gaji
  AccountRole.SALARY_PAYABLE       -> 2-10400 Utang Gaji
  AccountRole.PPH21_PAYABLE        -> 2-10310 Utang PPh 21 (payroll-exclusive)
  AccountRole.BPJS_EE_PAYABLE      -> 2-10410 Utang BPJS Karyawan
  AccountRole.BPJS_ER_PAYABLE      -> 2-10420 Utang BPJS Perusahaan
  AccountRole.BPJS_ER_EXPENSE      -> 5-20150 Beban BPJS Perusahaan
  AccountRole.PPH21_ER_EXPENSE     -> 5-80100 Beban PPh 21 Perusahaan (nett)
"""

from decimal import Decimal, ROUND_HALF_UP
from datetime import date, timedelta
from typing import Optional, Dict, List, Any
import logging

logger = logging.getLogger(__name__)

TWO_PLACES = Decimal("0.01")

# TER category mapping (PTKP status -> TER category)
TER_CATEGORY_MAP = {
    "TK0": "A",
    "TK1": "A",
    "TK2": "B",
    "TK3": "B",
    "K0": "B",
    "K1": "B",
    "K2": "C",
    "K3": "C",
    "KI0": "A",
    "KI1": "A",
    "KI2": "B",
    "KI3": "B",
}

# JKK risk level rates
JKK_RATES = {
    1: Decimal("0.0024"),
    2: Decimal("0.0054"),
    3: Decimal("0.0089"),
    4: Decimal("0.0127"),
    5: Decimal("0.0174"),
}

# Payroll CoA codes — REMOVED in Fase D4.3. Use `AccountRole.*` from
# `app.services.role_resolver` instead. Historic note: COA_HUTANG_PPH21 was
# the literal "2-10300" (Hutang Pajak generic) but the correct payroll-
# exclusive PPh 21 account is "2-10310" (mapped via AccountRole.PPH21_PAYABLE,
# V162). Keeping the generic 2-10300 would have violated the PPh 21 PAYROLL
# BOUNDARY in MAPPING-ROLE-AKUN-LOCKED.md §"PPH 21 PAYROLL BOUNDARY".


def d(val) -> Decimal:
    if val is None:
        return Decimal("0")
    if isinstance(val, Decimal):
        return val
    return Decimal(str(val))


def round2(val: Decimal) -> Decimal:
    return val.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


async def lookup_ter_rate(
    conn, ptkp_status: str, gross_monthly: Decimal, year: int = 2024
) -> Decimal:
    category = TER_CATEGORY_MAP.get(ptkp_status, "A")
    row = await conn.fetchrow(
        """
        SELECT rate FROM ter_rates
        WHERE category = $1 AND effective_year = $2
          AND income_from <= $3
          AND (income_to IS NULL OR income_to > $3)
        ORDER BY income_from DESC LIMIT 1
        """,
        category,
        year,
        float(gross_monthly),
    )
    return d(row["rate"]) if row else Decimal("0")


async def get_ptkp_amount(conn, ptkp_status: str) -> Decimal:
    row = await conn.fetchrow(
        "SELECT annual_amount FROM ptkp_rates WHERE status = $1 ORDER BY effective_year DESC LIMIT 1",
        ptkp_status,
    )
    return d(row["annual_amount"]) if row else Decimal("54000000")


async def calc_pasal17(conn, pkp: Decimal, year: int = 2024) -> Decimal:
    if pkp <= 0:
        return Decimal("0")
    brackets = await conn.fetch(
        "SELECT income_from, income_to, rate FROM pasal17_brackets WHERE effective_year = $1 ORDER BY income_from",
        year,
    )
    total_tax = Decimal("0")
    remaining = pkp
    for b in brackets:
        if remaining <= 0:
            break
        bracket_to = d(b["income_to"]) if b["income_to"] else None
        bracket_from = d(b["income_from"])
        rate = d(b["rate"])
        taxable = min(remaining, bracket_to - bracket_from) if bracket_to else remaining
        total_tax += taxable * rate
        remaining -= taxable
    return round2(total_tax)


async def get_bpjs_config(conn, tenant_id: str) -> Dict[str, Dict]:
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (component) component, employer_rate, employee_rate, ceiling_amount
        FROM bpjs_config
        WHERE tenant_id = $1 AND is_active = true
        ORDER BY component, effective_date DESC
        """,
        tenant_id,
    )
    return {r["component"]: dict(r) for r in rows}


def calc_business_days(start: date, end: date) -> int:
    if start > end:
        return 0
    days = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days


def calc_proration(
    join_date: Optional[date],
    resign_date: Optional[date],
    period_start: date,
    period_end: date,
) -> Decimal:
    effective_start = max(join_date, period_start) if join_date else period_start
    effective_end = min(resign_date, period_end) if resign_date else period_end
    if effective_start > effective_end:
        return Decimal("0")
    total_days = calc_business_days(period_start, period_end)
    if total_days == 0:
        return Decimal("0")
    work_days = calc_business_days(effective_start, effective_end)
    return d(work_days) / d(total_days)


def calc_overtime(basic_salary: Decimal, overtime_hours: Decimal) -> Decimal:
    """Kepmenaker 102/2004 weekday: 1st hour 1.5x, subsequent 2x. Rate = basic/173."""
    if overtime_hours <= 0:
        return Decimal("0")
    hourly = basic_salary / Decimal("173")
    if overtime_hours <= 1:
        return round2(overtime_hours * hourly * Decimal("1.5"))
    first = hourly * Decimal("1.5")
    rest = (overtime_hours - Decimal("1")) * hourly * Decimal("2")
    return round2(first + rest)


async def calculate_employee_slip(
    conn,
    tenant_id: str,
    employee: dict,
    salary_config: List[dict],
    components_map: Dict[str, dict],
    bpjs_cfg: Dict[str, Dict],
    period_start: date,
    period_end: date,
    period_month: int,
    variable_inputs: Dict[str, Any],
    ytd_data: Optional[dict] = None,
) -> Dict[str, Any]:
    """Calculate a single employee's payroll slip."""

    proration = calc_proration(
        employee.get("join_date"), employee.get("resign_date"), period_start, period_end
    )
    if proration == 0:
        return _empty_slip(employee)

    # Build earnings
    earnings = []
    basic_salary = Decimal("0")

    for cfg in salary_config:
        comp = components_map.get(str(cfg["component_id"]))
        if not comp or comp["type"] != "earning":
            continue
        amount = d(cfg["amount"])
        category = comp["category"]

        if comp["is_fixed"]:
            amount = round2(amount * proration)

        if category == "lembur":
            ot_hours = d(variable_inputs.get("overtime_hours", 0))
            if ot_hours > 0 and basic_salary > 0:
                amount = calc_overtime(basic_salary, ot_hours)
            elif "LEMBUR" in variable_inputs:
                amount = d(variable_inputs["LEMBUR"])
            else:
                continue  # skip lembur if no input

        if category in ("bonus", "thr"):
            var_amount = variable_inputs.get(comp["code"])
            if var_amount is not None:
                amount = d(var_amount)
            elif amount == 0:
                continue  # skip zero variable components

        if category == "gaji_pokok":
            basic_salary = amount

        if amount > 0:
            earnings.append(
                {
                    "component_id": str(comp["id"]),
                    "component_name": comp["name"],
                    "component_type": "earning",
                    "component_category": category,
                    "amount": float(round2(amount)),
                    "is_taxable": comp["is_taxable"],
                    "sort_order": comp["sort_order"],
                }
            )

    gross = round2(sum(d(e["amount"]) for e in earnings))

    # BPJS employee deductions
    deductions = []
    bpjs_kes_ee = Decimal("0")
    bpjs_jht_ee = Decimal("0")
    bpjs_jp_ee = Decimal("0")

    kes_cfg = bpjs_cfg.get("kes", {})
    jht_cfg = bpjs_cfg.get("jht", {})
    jp_cfg = bpjs_cfg.get("jp", {})

    if employee.get("is_bpjs_kes", True) and kes_cfg:
        ceiling = d(kes_cfg.get("ceiling_amount")) or gross
        bpjs_kes_ee = round2(min(gross, ceiling) * d(kes_cfg.get("employee_rate", 0)))
        deductions.append(
            {
                "component_id": None,
                "component_name": "BPJS Kesehatan",
                "component_type": "deduction",
                "component_category": "bpjs_kes_ee",
                "amount": float(bpjs_kes_ee),
                "is_taxable": False,
                "sort_order": 100,
            }
        )

    if employee.get("is_bpjs_jht", True) and jht_cfg:
        bpjs_jht_ee = round2(gross * d(jht_cfg.get("employee_rate", 0)))
        deductions.append(
            {
                "component_id": None,
                "component_name": "BPJS JHT",
                "component_type": "deduction",
                "component_category": "bpjs_jht_ee",
                "amount": float(bpjs_jht_ee),
                "is_taxable": False,
                "sort_order": 101,
            }
        )

    if employee.get("is_bpjs_jp", True) and jp_cfg:
        jp_ceiling = d(jp_cfg.get("ceiling_amount")) or gross
        bpjs_jp_ee = round2(min(gross, jp_ceiling) * d(jp_cfg.get("employee_rate", 0)))
        deductions.append(
            {
                "component_id": None,
                "component_name": "BPJS JP",
                "component_type": "deduction",
                "component_category": "bpjs_jp_ee",
                "amount": float(bpjs_jp_ee),
                "is_taxable": False,
                "sort_order": 102,
            }
        )

    # PPh 21
    if period_month <= 11:
        ter_rate = await lookup_ter_rate(
            conn, employee.get("ptkp_status", "TK0"), gross
        )
        pph21 = round2(gross * ter_rate)
    else:
        ytd = ytd_data or {}
        annual_gross = d(ytd.get("gross_ytd", 0)) + gross
        biaya_jabatan = min(annual_gross * Decimal("0.05"), Decimal("6000000"))
        iuran_pensiun = d(ytd.get("jp_ee_ytd", 0)) + bpjs_jp_ee
        neto = annual_gross - biaya_jabatan - iuran_pensiun
        ptkp = await get_ptkp_amount(conn, employee.get("ptkp_status", "TK0"))
        pkp = max(neto - ptkp, Decimal("0"))
        pph21_annual = await calc_pasal17(conn, pkp)
        pph21 = max(pph21_annual - d(ytd.get("pph21_ytd", 0)), Decimal("0"))
        pph21 = round2(pph21)

    tax_method = employee.get("tax_method", "gross")
    if tax_method == "nett":
        pph21_deduction = Decimal("0")
        employer_pph21 = pph21
    else:
        pph21_deduction = pph21
        employer_pph21 = Decimal("0")

    if pph21_deduction > 0:
        deductions.append(
            {
                "component_id": None,
                "component_name": "PPh 21",
                "component_type": "deduction",
                "component_category": "pph21",
                "amount": float(pph21_deduction),
                "is_taxable": False,
                "sort_order": 110,
            }
        )

    total_deductions = round2(sum(d(dd["amount"]) for dd in deductions))
    net = round2(gross - total_deductions)

    # Employer costs
    employer_costs = []
    bpjs_er_total = Decimal("0")

    if employee.get("is_bpjs_kes", True) and kes_cfg:
        ceiling = d(kes_cfg.get("ceiling_amount")) or gross
        amt = round2(min(gross, ceiling) * d(kes_cfg.get("employer_rate", 0)))
        bpjs_er_total += amt
        employer_costs.append(
            {
                "component_name": "BPJS Kesehatan (Perusahaan)",
                "component_type": "employer_cost",
                "component_category": "bpjs_kes_er",
                "amount": float(amt),
                "sort_order": 200,
            }
        )

    if employee.get("is_bpjs_jht", True) and jht_cfg:
        amt = round2(gross * d(jht_cfg.get("employer_rate", 0)))
        bpjs_er_total += amt
        employer_costs.append(
            {
                "component_name": "BPJS JHT (Perusahaan)",
                "component_type": "employer_cost",
                "component_category": "bpjs_jht_er",
                "amount": float(amt),
                "sort_order": 201,
            }
        )

    if employee.get("is_bpjs_jp", True) and jp_cfg:
        jp_ceiling = d(jp_cfg.get("ceiling_amount")) or gross
        amt = round2(min(gross, jp_ceiling) * d(jp_cfg.get("employer_rate", 0)))
        bpjs_er_total += amt
        employer_costs.append(
            {
                "component_name": "BPJS JP (Perusahaan)",
                "component_type": "employer_cost",
                "component_category": "bpjs_jp_er",
                "amount": float(amt),
                "sort_order": 202,
            }
        )

    jkk_rate = JKK_RATES.get(employee.get("jkk_risk_level", 1), Decimal("0.0024"))
    jkk_amt = round2(gross * jkk_rate)
    bpjs_er_total += jkk_amt
    employer_costs.append(
        {
            "component_name": "BPJS JKK (Perusahaan)",
            "component_type": "employer_cost",
            "component_category": "bpjs_jkk",
            "amount": float(jkk_amt),
            "sort_order": 203,
        }
    )

    jkm_amt = round2(gross * Decimal("0.0030"))
    bpjs_er_total += jkm_amt
    employer_costs.append(
        {
            "component_name": "BPJS JKM (Perusahaan)",
            "component_type": "employer_cost",
            "component_category": "bpjs_jkm",
            "amount": float(jkm_amt),
            "sort_order": 204,
        }
    )

    if employer_pph21 > 0:
        employer_costs.append(
            {
                "component_name": "PPh 21 (Perusahaan)",
                "component_type": "employer_cost",
                "component_category": "pph21_employer",
                "amount": float(employer_pph21),
                "sort_order": 210,
            }
        )

    total_employer_cost = round2(sum(d(ec["amount"]) for ec in employer_costs))

    return {
        "employee_id": str(employee["id"]),
        "employee_name": employee["name"],
        "earnings": sorted(earnings, key=lambda x: x["sort_order"]),
        "deductions": sorted(deductions, key=lambda x: x["sort_order"]),
        "employer_costs": sorted(employer_costs, key=lambda x: x["sort_order"]),
        "gross": float(gross),
        "total_deductions": float(total_deductions),
        "net": float(net),
        "total_employer_cost": float(total_employer_cost),
        "pph21_amount": float(pph21),
        "bpjs_ee_total": float(bpjs_kes_ee + bpjs_jht_ee + bpjs_jp_ee),
        "bpjs_er_total": float(bpjs_er_total),
        "proration_factor": float(proration),
    }


def _empty_slip(employee: dict = None):
    return {
        "employee_id": str(employee["id"]) if employee else None,
        "employee_name": employee["name"] if employee else None,
        "earnings": [],
        "deductions": [],
        "employer_costs": [],
        "gross": 0,
        "total_deductions": 0,
        "net": 0,
        "total_employer_cost": 0,
        "pph21_amount": 0,
        "bpjs_ee_total": 0,
        "bpjs_er_total": 0,
        "proration_factor": 0,
    }


async def get_ytd_data(
    conn, tenant_id: str, employee_id: str, year: int, up_to_month: int
) -> dict:
    """Get YTD payroll data for December true-up."""
    row = await conn.fetchrow(
        """
        SELECT
            COALESCE(SUM(CASE WHEN psl.component_type = 'earning' THEN psl.amount ELSE 0 END), 0) as gross_ytd,
            COALESCE(SUM(CASE WHEN psl.component_category = 'pph21' THEN psl.amount ELSE 0 END), 0) as pph21_ytd,
            COALESCE(SUM(CASE WHEN psl.component_category = 'bpjs_jp_ee' THEN psl.amount ELSE 0 END), 0) as jp_ee_ytd
        FROM payroll_slip_lines psl
        JOIN payroll_runs pr ON pr.id = psl.payroll_id
        WHERE psl.tenant_id = $1
          AND psl.employee_id = $2::uuid
          AND pr.status = 'posted'
          AND EXTRACT(YEAR FROM pr.period_start) = $3
          AND EXTRACT(MONTH FROM pr.period_start) < $4
        """,
        tenant_id,
        employee_id,
        year,
        up_to_month,
    )
    return dict(row) if row else {"gross_ytd": 0, "pph21_ytd": 0, "jp_ee_ytd": 0}
