"""
Bank Rule Engine for Reconciliation.

Evaluates user-defined and system-generated rules against bank statement lines.
Rules are evaluated in sort_order priority. First matching rule wins.

Condition operators: contains, not_contains, equals, not_equals, starts_with,
    ends_with, regex, greater_than, less_than, between
Condition fields: description, amount, reference, date
Logic: ALL (every condition must match) or ANY (at least one must match)
"""

import re
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from services.bank_text_normalizer import normalize_bank_text

logger = logging.getLogger(__name__)


# ============ DATA CLASSES ============

@dataclass
class RuleCondition:
    """A single condition within a rule."""
    field: str          # description | amount | reference | date
    operator: str       # contains | equals | starts_with | ends_with | regex | greater_than | less_than | between | not_contains | not_equals
    value: str          # The value to compare against
    value2: str = ""    # Second value for 'between' operator

    @classmethod
    def from_dict(cls, d: dict) -> "RuleCondition":
        return cls(
            field=d.get("field", ""),
            operator=d.get("operator", ""),
            value=str(d.get("value", "")),
            value2=str(d.get("value2", "")),
        )


@dataclass
class RuleAction:
    """Actions to apply when a rule matches."""
    contact_id: Optional[str] = None
    account_id: Optional[str] = None
    tax_rate_id: Optional[str] = None
    allocations: Optional[list] = None  # For split allocations


@dataclass
class BankRule:
    """A complete bank matching rule."""
    id: str
    tenant_id: str
    name: str
    description: str = ""
    is_active: bool = True
    sort_order: int = 0
    rule_type: str = "user"  # user | system | learned
    bank_account_id: Optional[str] = None
    direction: str = "both"  # inflow | outflow | both
    condition_logic: str = "ALL"  # ALL | ANY
    conditions: list[RuleCondition] = field(default_factory=list)
    action: RuleAction = field(default_factory=RuleAction)
    times_applied: int = 0

    @classmethod
    def from_row(cls, row: dict) -> "BankRule":
        """Create BankRule from database row."""
        conditions = []
        raw_conditions = row.get("conditions") or []
        if isinstance(raw_conditions, str):
            import json
            raw_conditions = json.loads(raw_conditions)
        for c in raw_conditions:
            conditions.append(RuleCondition.from_dict(c))

        return cls(
            id=str(row["id"]),
            tenant_id=str(row["tenant_id"]),
            name=row["name"],
            description=row.get("description") or "",
            is_active=row.get("is_active", True),
            sort_order=row.get("sort_order", 0),
            rule_type=row.get("rule_type", "user"),
            bank_account_id=str(row["bank_account_id"]) if row.get("bank_account_id") else None,
            direction=row.get("direction", "both"),
            condition_logic=row.get("condition_logic", "ALL"),
            conditions=conditions,
            action=RuleAction(
                contact_id=str(row["contact_id"]) if row.get("contact_id") else None,
                account_id=str(row["account_id"]) if row.get("account_id") else None,
                tax_rate_id=str(row["tax_rate_id"]) if row.get("tax_rate_id") else None,
                allocations=row.get("allocations"),
            ),
            times_applied=row.get("times_applied", 0),
        )


@dataclass
class RuleMatchResult:
    """Result of evaluating rules against a statement line."""
    matched: bool
    rule: Optional[BankRule] = None
    rule_id: Optional[str] = None
    rule_name: Optional[str] = None
    action: Optional[RuleAction] = None
    conditions_met: list[str] = field(default_factory=list)


# ============ CONDITION EVALUATORS ============

def _evaluate_text_condition(actual: str, operator: str, value: str, value2: str = "") -> bool:
    """Evaluate a text-based condition (description, reference)."""
    # Normalize both sides for fair comparison
    actual_norm = normalize_bank_text(actual).lower() if actual else ""
    value_norm = value.lower().strip() if value else ""

    if operator == "contains":
        return value_norm in actual_norm
    elif operator == "not_contains":
        return value_norm not in actual_norm
    elif operator == "equals":
        return actual_norm == value_norm
    elif operator == "not_equals":
        return actual_norm != value_norm
    elif operator == "starts_with":
        return actual_norm.startswith(value_norm)
    elif operator == "ends_with":
        return actual_norm.endswith(value_norm)
    elif operator == "regex":
        try:
            return bool(re.search(value, actual or "", re.IGNORECASE))
        except re.error:
            logger.warning(f"Invalid regex in rule condition: {value}")
            return False
    else:
        logger.warning(f"Unknown text operator: {operator}")
        return False


def _evaluate_amount_condition(actual: float, operator: str, value: str, value2: str = "") -> bool:
    """Evaluate an amount-based condition."""
    try:
        val = float(value)
    except (ValueError, TypeError):
        return False

    if operator == "equals":
        return abs(actual - val) < 0.01
    elif operator == "not_equals":
        return abs(actual - val) >= 0.01
    elif operator == "greater_than":
        return actual > val
    elif operator == "less_than":
        return actual < val
    elif operator == "between":
        try:
            val2 = float(value2)
        except (ValueError, TypeError):
            return False
        return min(val, val2) <= actual <= max(val, val2)
    else:
        logger.warning(f"Unknown amount operator: {operator}")
        return False


def _evaluate_date_condition(actual: str, operator: str, value: str, value2: str = "") -> bool:
    """Evaluate a date-based condition."""
    try:
        actual_date = datetime.strptime(str(actual)[:10], "%Y-%m-%d")
        val_date = datetime.strptime(value[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return False

    if operator == "equals":
        return actual_date == val_date
    elif operator == "not_equals":
        return actual_date != val_date
    elif operator == "greater_than":
        return actual_date > val_date
    elif operator == "less_than":
        return actual_date < val_date
    elif operator == "between":
        try:
            val2_date = datetime.strptime(value2[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            return False
        return min(val_date, val2_date) <= actual_date <= max(val_date, val2_date)
    else:
        logger.warning(f"Unknown date operator: {operator}")
        return False


# ============ CORE ENGINE ============

def evaluate_condition(condition: RuleCondition, statement_line: dict) -> bool:
    """
    Evaluate a single condition against a statement line.

    Args:
        condition: The condition to evaluate
        statement_line: Dict with keys: description, amount, reference, date, type

    Returns:
        True if condition is met
    """
    field_val = statement_line.get(condition.field, "")

    if condition.field in ("description", "reference"):
        return _evaluate_text_condition(
            str(field_val) if field_val else "",
            condition.operator,
            condition.value,
            condition.value2,
        )
    elif condition.field == "amount":
        try:
            amount = float(field_val) if field_val else 0.0
        except (ValueError, TypeError):
            amount = 0.0
        return _evaluate_amount_condition(
            amount, condition.operator, condition.value, condition.value2
        )
    elif condition.field == "date":
        return _evaluate_date_condition(
            str(field_val) if field_val else "",
            condition.operator,
            condition.value,
            condition.value2,
        )
    else:
        logger.warning(f"Unknown condition field: {condition.field}")
        return False


def evaluate_rule(rule: BankRule, statement_line: dict) -> bool:
    """
    Evaluate all conditions of a rule against a statement line.

    Args:
        rule: The rule to evaluate
        statement_line: Dict with keys: description, amount, reference, date, type

    Returns:
        True if the rule matches (all/any conditions depending on logic)
    """
    if not rule.conditions:
        return False

    # Check direction filter
    line_type = statement_line.get("type", "")
    if rule.direction == "inflow" and line_type not in ("credit", "inflow"):
        return False
    if rule.direction == "outflow" and line_type not in ("debit", "outflow"):
        return False

    # Evaluate conditions based on logic (ALL = AND, ANY = OR)
    results = [evaluate_condition(c, statement_line) for c in rule.conditions]

    if rule.condition_logic == "ALL":
        return all(results)
    else:  # ANY
        return any(results)


def evaluate_rules(
    rules: list[BankRule],
    statement_line: dict,
    bank_account_id: Optional[str] = None,
) -> RuleMatchResult:
    """
    Evaluate all rules against a statement line. First match wins.

    Rules are expected to be pre-sorted by sort_order.
    Only active rules are evaluated.

    Args:
        rules: List of BankRule objects (sorted by sort_order)
        statement_line: Dict with keys: description, amount, reference, date, type
        bank_account_id: Optional filter for account-specific rules

    Returns:
        RuleMatchResult with matched=True if a rule matched
    """
    for rule in rules:
        # Skip inactive rules
        if not rule.is_active:
            continue

        # Skip rules for other bank accounts
        if rule.bank_account_id and bank_account_id:
            if rule.bank_account_id != bank_account_id:
                continue

        if evaluate_rule(rule, statement_line):
            # Build list of conditions that were met (for UI display)
            conditions_met = []
            for c in rule.conditions:
                if evaluate_condition(c, statement_line):
                    conditions_met.append(f"{c.field} {c.operator} '{c.value}'")

            return RuleMatchResult(
                matched=True,
                rule=rule,
                rule_id=rule.id,
                rule_name=rule.name,
                action=rule.action,
                conditions_met=conditions_met,
            )

    return RuleMatchResult(matched=False)
