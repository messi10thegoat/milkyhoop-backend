"""
InsightEngine — Rule-based interpretation layer.
Pure code, zero LLM. Evaluates data -> structured insights.
Gemini Flash ONLY renders insights to natural language.
"""
import logging
from dataclasses import dataclass

logger = logging.getLogger("unified_chat")


@dataclass
class InsightObject:
    insight_type: str  # "cashflow_risk", "ar_aging", "expense_spike", "low_stock"
    severity: str  # "high", "medium", "low", "info"
    title: str
    evidence: list
    recommended_action: str
    data_completeness: float = 1.0
    emoji: str = ""


@dataclass
class InsightRule:
    intent_pattern: str
    condition: str  # Python expression string
    insight_type: str
    severity: str
    title_template: str
    evidence_template: list
    action_template: str
    emoji: str = ""


# ======================================================================
# RULES REGISTRY — add rules here, zero code change elsewhere
# ======================================================================

INSIGHT_RULES = [
    # -- AR/AP Net Position --
    InsightRule(
        intent_pattern="query_ar_outstanding",
        condition="data.get('net_position', 0) < 0",
        insight_type="cashflow_risk",
        severity="high",
        title_template="Net Position Negatif",
        evidence_template=[
            "Piutang: Rp {total_ar:,.0f}",
            "Hutang: Rp {total_ap:,.0f}",
            "Net position: Rp {net_position:,.0f}",
        ],
        action_template="Prioritas kejar piutang yang paling dekat jatuh tempo",
        emoji="\U0001f534",
    ),
    InsightRule(
        intent_pattern="query_ar_outstanding",
        condition="any(inv.get('days_overdue', 0) > 30 for inv in data.get('invoices', []))",
        insight_type="ar_aging",
        severity="high",
        title_template="Piutang Overdue >30 Hari",
        evidence_template=[
            "{overdue_count} faktur sudah overdue >30 hari",
            "Total overdue: Rp {overdue_total:,.0f}",
        ],
        action_template="Hubungi {top_overdue_customer} segera",
        emoji="\u26a0\ufe0f",
    ),
    InsightRule(
        intent_pattern="query_ar_outstanding",
        condition="data.get('counts', {}).get('current', 0) >= 3",
        insight_type="ar_clustering",
        severity="medium",
        title_template="Banyak Jatuh Tempo Sebentar Lagi",
        evidence_template=[
            "{count_due_soon} faktur jatuh tempo dalam 30 hari",
        ],
        action_template="Siapkan cash flow untuk pembayaran mendatang",
        emoji="\U0001f4c5",
    ),
    InsightRule(
        intent_pattern="query_ar_outstanding",
        condition="data.get('net_position', 0) > 0",
        insight_type="positive_cashflow",
        severity="info",
        title_template="Posisi Keuangan Sehat",
        evidence_template=[
            "Net position positif: Rp {net_position:,.0f}",
            "Piutang > Hutang",
        ],
        action_template="",
        emoji="\u2705",
    ),
    # -- AP Outstanding --
    InsightRule(
        intent_pattern="query_ap_outstanding",
        condition="data.get('counts', {}).get('overdue', 0) > 0",
        insight_type="ap_overdue",
        severity="high",
        title_template="Ada Hutang Jatuh Tempo",
        evidence_template=[
            "{overdue_count} tagihan sudah jatuh tempo",
            "Total overdue: Rp {overdue_amount:,.0f}",
        ],
        action_template="Segera bayar untuk hindari denda atau masalah dengan vendor",
        emoji="\U0001f534",
    ),
    InsightRule(
        intent_pattern="query_ap_outstanding",
        condition="data.get('counts', {}).get('overdue', 0) == 0 and data.get('total_outstanding', 0) > 0",
        insight_type="ap_healthy",
        severity="info",
        title_template="Hutang Terkendali",
        evidence_template=[
            "Semua tagihan belum jatuh tempo",
        ],
        action_template="",
        emoji="\u2705",
    ),
    # -- Expenses --
    InsightRule(
        intent_pattern="query_expenses_summary",
        condition="data.get('growth_pct', 0) > 50",
        insight_type="expense_spike",
        severity="high",
        title_template="Pengeluaran Melonjak",
        evidence_template=[
            "Naik {growth_pct:.0f}% vs bulan lalu",
        ],
        action_template="Segera review pengeluaran terbesar bulan ini",
        emoji="\U0001f534",
    ),
    InsightRule(
        intent_pattern="query_expenses_summary",
        condition="data.get('growth_pct', 0) > 20",
        insight_type="expense_spike",
        severity="medium",
        title_template="Pengeluaran Naik",
        evidence_template=[
            "Naik {growth_pct:.0f}% vs bulan lalu",
        ],
        action_template="Cek kategori pengeluaran terbesar \u2014 mau saya breakdown?",
        emoji="\U0001f4c8",
    ),
    # -- Cash/Bank --
    InsightRule(
        intent_pattern="query_cash_balance",
        condition="data.get('total_balance', 0) < 0",
        insight_type="cashflow_risk",
        severity="high",
        title_template="Saldo Negatif",
        evidence_template=[
            "Saldo saat ini: Rp {total_balance:,.0f}",
        ],
        action_template="Segera pastikan ada pemasukan atau kurangi pengeluaran",
        emoji="\U0001f534",
    ),
    InsightRule(
        intent_pattern="query_cash_balance",
        condition="0 < data.get('total_balance', 0) < data.get('monthly_expense_avg', 999999999) * 2",
        insight_type="cashflow_warning",
        severity="medium",
        title_template="Cash Flow Perlu Perhatian",
        evidence_template=[
            "Saldo: Rp {total_balance:,.0f}",
            "Cukup untuk ~{runway_days:.0f} hari operasional",
        ],
        action_template="Ada piutang yang bisa dikejar untuk tambah kas?",
        emoji="\u26a0\ufe0f",
    ),
    InsightRule(
        intent_pattern="query_cash_balance",
        condition="data.get('total_balance', 0) >= data.get('monthly_expense_avg', 999999999) * 2",
        insight_type="cashflow_healthy",
        severity="info",
        title_template="Kas Aman",
        evidence_template=[
            "Saldo cukup untuk {runway_days:.0f}+ hari operasional",
        ],
        action_template="",
        emoji="\u2705",
    ),
    # -- Low Stock --
    InsightRule(
        intent_pattern="query_items_low_stock",
        condition="len(data.get('items', [])) > 5",
        insight_type="stock_critical",
        severity="high",
        title_template="Banyak Item Stok Kritis",
        evidence_template=[
            "{low_stock_count} item di bawah stok minimum",
            "Termasuk: {top_low_stock_names}",
        ],
        action_template="Segera order \u2014 mau saya buatkan daftar restock?",
        emoji="\U0001f4e6",
    ),
    InsightRule(
        intent_pattern="query_items_low_stock",
        condition="0 < len(data.get('items', [])) <= 5",
        insight_type="stock_warning",
        severity="low",
        title_template="Beberapa Item Stok Rendah",
        evidence_template=[
            "{low_stock_count} item perlu restock",
        ],
        action_template="Mau saya buatkan pesanan pembelian?",
        emoji="\U0001f4e6",
    ),
]


def evaluate(intent: str, data: dict, tenant_config: dict = None) -> list:
    """Evaluate data against matching rules. Returns InsightObjects sorted by severity."""
    # Unwrap API envelope: {"success": true, "data": {...}} -> {...}
    if isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
        data = data["data"]

    insights = []

    for rule in INSIGHT_RULES:
        if rule.intent_pattern != "*" and rule.intent_pattern != intent:
            continue

        try:
            if not _safe_eval(rule.condition, data):
                continue
        except Exception:
            continue

        flat = _flatten_data(data)
        evidence = []
        for tmpl in rule.evidence_template:
            try:
                evidence.append(tmpl.format(**flat))
            except (KeyError, ValueError, TypeError):
                evidence.append(tmpl)

        try:
            action = rule.action_template.format(**flat)
        except (KeyError, ValueError, TypeError):
            action = rule.action_template

        insights.append(
            InsightObject(
                insight_type=rule.insight_type,
                severity=rule.severity,
                title=rule.title_template,
                evidence=evidence,
                recommended_action=action,
                data_completeness=_estimate_completeness(data),
                emoji=rule.emoji,
            )
        )

    severity_order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    insights.sort(key=lambda x: severity_order.get(x.severity, 4))
    return insights


def format_insights_for_prompt(insights: list) -> str:
    """Format insights as text to inject into LLM polish prompt."""
    if not insights:
        return ""

    lines = [
        "\n\nINSIGHT (WAJIB sampaikan ke user dalam bahasa natural, jangan abaikan):"
    ]
    for ins in insights:
        lines.append(ins.emoji + " [" + ins.severity.upper() + "] " + ins.title)
        for ev in ins.evidence:
            lines.append("  - " + ev)
        if ins.recommended_action:
            lines.append("  -> Saran: " + ins.recommended_action)

    return "\n".join(lines)


def _safe_eval(condition: str, data: dict) -> bool:
    safe_globals = {"__builtins__": {}}
    safe_locals = {
        "data": data,
        "len": len,
        "any": any,
        "all": all,
        "sum": sum,
        "min": min,
        "max": max,
        "abs": abs,
    }
    return bool(eval(condition, safe_globals, safe_locals))


def _flatten_data(data: dict) -> dict:
    flat = dict(data)
    items = data.get("invoices", data.get("items", []))
    if isinstance(items, list):
        flat["overdue_count"] = sum(
            1 for i in items if isinstance(i, dict) and i.get("days_overdue", 0) > 30
        )
        flat["overdue_total"] = sum(
            i.get("remaining", i.get("outstanding", 0))
            for i in items
            if isinstance(i, dict) and i.get("days_overdue", 0) > 30
        )
        flat["low_stock_count"] = len(items)
        overdue_sorted = sorted(
            [i for i in items if isinstance(i, dict) and i.get("days_overdue", 0) > 30],
            key=lambda x: -(x.get("remaining", x.get("outstanding", 0)) or 0),
        )
        if overdue_sorted:
            flat["top_overdue_customer"] = overdue_sorted[0].get(
                "customer_name", overdue_sorted[0].get("vendor_name", "?")
            )
            flat["top_overdue_amount"] = overdue_sorted[0].get(
                "remaining", overdue_sorted[0].get("outstanding", 0)
            )
        flat["top_low_stock_names"] = ", ".join(
            i.get("nama_produk", i.get("name", "?"))
            for i in items[:3]
            if isinstance(i, dict)
        )

    # Counts from summary endpoints
    counts = data.get("counts", {})
    flat["count_due_soon"] = counts.get("current", 0)
    flat["overdue_count"] = flat.get("overdue_count", counts.get("overdue", 0))
    flat["overdue_amount"] = data.get("by_aging", {}).get(
        "overdue", flat.get("overdue_total", 0)
    )

    # Runway
    balance = float(data.get("total_balance", 0) or 0)
    monthly_avg = float(data.get("monthly_expense_avg", 0) or 0)
    flat["runway_days"] = (abs(balance) / monthly_avg) * 30 if monthly_avg > 0 else 999
    flat["total_balance"] = balance

    # Net position
    flat.setdefault("total_ar", data.get("total_ar", data.get("total_outstanding", 0)))
    flat.setdefault("total_ap", data.get("total_ap", data.get("total_payable", 0)))
    flat.setdefault(
        "net_position",
        float(flat.get("total_ar", 0) or 0) - float(flat.get("total_ap", 0) or 0),
    )

    # Growth
    flat.setdefault("growth_pct", data.get("growth_pct", 0))

    return flat


def _estimate_completeness(data: dict) -> float:
    if not data:
        return 0.0
    total = len(data)
    filled = sum(
        1 for v in data.values() if v is not None and v != 0 and v != [] and v != ""
    )
    return min(1.0, filled / max(total, 1))
