"""
Bank Match Scorer for Reconciliation.

Multi-factor weighted scoring system that computes match confidence
between bank statement lines and system transactions.

Weights: amount(0.40) + reference(0.25) + text(0.25) + date(0.10) = 1.00
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from services.bank_text_normalizer import (
    normalize_bank_text,
    extract_reference_numbers,
    token_set_ratio,
)


# ============ DATA CLASSES ============

@dataclass
class ScoringWeights:
    """Configurable weights for match scoring factors."""
    amount: float = 0.40
    reference: float = 0.25
    text: float = 0.25
    date: float = 0.10

    def validate(self) -> bool:
        total = self.amount + self.reference + self.text + self.date
        return abs(total - 1.0) < 0.001


@dataclass
class ScoreBreakdown:
    """Detailed breakdown of match score components."""
    amount_score: float      # 0.0 - 1.0
    reference_score: float   # 0.0 - 1.0
    text_score: float        # 0.0 - 1.0
    date_score: float        # 0.0 - 1.0
    total_score: float       # weighted sum 0.0 - 1.0
    confidence: str          # exact | high | medium | low

    @property
    def is_auto_match(self) -> bool:
        """Whether this score qualifies for automatic matching."""
        return self.confidence in ("exact", "high")


# ============ DEFAULT WEIGHTS ============
DEFAULT_WEIGHTS = ScoringWeights()


# ============ SCORING FUNCTIONS ============

def _score_amount(
    statement_amount: float,
    transaction_amount: float,
    tolerance_pct: float = 0.001,
) -> float:
    """
    Score amount similarity.

    - Exact match (within tolerance): 1.0
    - Within 1%: 0.9
    - Within 5%: 0.7
    - Within 10%: 0.4
    - Beyond 10%: 0.0

    Args:
        statement_amount: Amount from bank statement
        transaction_amount: Amount from system transaction
        tolerance_pct: Tolerance for exact match (default 0.1%)

    Returns:
        Score 0.0 to 1.0
    """
    if statement_amount == 0 and transaction_amount == 0:
        return 1.0

    if statement_amount == 0 or transaction_amount == 0:
        return 0.0

    # Use absolute values for comparison
    s_abs = abs(statement_amount)
    t_abs = abs(transaction_amount)

    diff = abs(s_abs - t_abs)
    max_val = max(s_abs, t_abs)
    pct_diff = diff / max_val

    if pct_diff <= tolerance_pct:
        return 1.0
    elif pct_diff <= 0.01:
        return 0.9
    elif pct_diff <= 0.05:
        return 0.7
    elif pct_diff <= 0.10:
        return 0.4
    else:
        return 0.0


def _score_reference(
    statement_text: str,
    transaction_ref: str | None,
    transaction_number: str | None = None,
) -> float:
    """
    Score reference number match.

    Extracts references from statement text and compares against
    transaction reference and source number.

    - Exact reference match: 1.0
    - Partial reference overlap: 0.6
    - No reference data: 0.0

    Args:
        statement_text: Raw bank statement description
        transaction_ref: System transaction reference
        transaction_number: Source document number (invoice, etc.)

    Returns:
        Score 0.0 to 1.0
    """
    statement_refs = extract_reference_numbers(statement_text)

    if not statement_refs:
        return 0.0

    # Build candidate refs from transaction
    candidates = set()
    if transaction_ref:
        candidates.add(transaction_ref.upper().strip())
    if transaction_number:
        candidates.add(transaction_number.upper().strip())

    if not candidates:
        return 0.0

    # Check for exact match
    statement_ref_set = set(statement_refs)
    if statement_ref_set & candidates:
        return 1.0

    # Check for partial containment
    for s_ref in statement_refs:
        for c_ref in candidates:
            if s_ref in c_ref or c_ref in s_ref:
                return 0.6

    return 0.0


def _score_text(
    statement_text: str,
    transaction_description: str,
    contact_name: str | None = None,
) -> float:
    """
    Score text similarity using token set ratio.

    Compares normalized bank text against transaction description
    and contact name. Takes the maximum score.

    Args:
        statement_text: Raw bank statement description
        transaction_description: System transaction description
        contact_name: Optional contact/vendor/customer name

    Returns:
        Score 0.0 to 1.0
    """
    norm_statement = normalize_bank_text(statement_text)

    if not norm_statement:
        return 0.0

    scores = []

    # Compare with transaction description
    if transaction_description:
        norm_txn = normalize_bank_text(transaction_description)
        if norm_txn:
            scores.append(token_set_ratio(norm_statement, norm_txn))

    # Compare with contact name
    if contact_name:
        norm_contact = normalize_bank_text(contact_name)
        if norm_contact:
            scores.append(token_set_ratio(norm_statement, norm_contact))

    return max(scores) if scores else 0.0


def _score_date(
    statement_date: str,
    transaction_date: str,
    max_days: int = 7,
) -> float:
    """
    Score date proximity.

    - Same day: 1.0
    - 1 day apart: 0.9
    - 2 days: 0.8
    - 3 days: 0.6
    - 4-5 days: 0.3
    - 6-7 days: 0.1
    - Beyond 7 days: 0.0

    Args:
        statement_date: Date string (YYYY-MM-DD)
        transaction_date: Date string (YYYY-MM-DD)
        max_days: Maximum days apart to consider

    Returns:
        Score 0.0 to 1.0
    """
    try:
        s_date = datetime.strptime(statement_date[:10], "%Y-%m-%d")
        t_date = datetime.strptime(transaction_date[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return 0.0

    days_diff = abs((s_date - t_date).days)

    if days_diff == 0:
        return 1.0
    elif days_diff == 1:
        return 0.9
    elif days_diff == 2:
        return 0.8
    elif days_diff == 3:
        return 0.6
    elif days_diff <= 5:
        return 0.3
    elif days_diff <= max_days:
        return 0.1
    else:
        return 0.0


def _determine_confidence(total_score: float) -> str:
    """
    Map total score to confidence level.

    - >= 0.95: exact
    - >= 0.65: high
    - >= 0.50: medium
    - < 0.50: low
    """
    if total_score >= 0.95:
        return "exact"
    elif total_score >= 0.65:
        return "high"
    elif total_score >= 0.50:
        return "medium"
    else:
        return "low"


# ============ MAIN FUNCTION ============

def compute_match_score(
    statement_line: dict,
    transaction: dict,
    weights: ScoringWeights | None = None,
) -> ScoreBreakdown:
    """
    Compute weighted match score between a statement line and transaction.

    Args:
        statement_line: Dict with keys: description, amount, date, reference
        transaction: Dict with keys: description, amount, date, reference,
                     source_number, contact_name
        weights: Optional custom weights (defaults to 0.40/0.25/0.25/0.10)

    Returns:
        ScoreBreakdown with component scores and confidence level
    """
    w = weights or DEFAULT_WEIGHTS

    # Score each factor
    amount_score = _score_amount(
        statement_line.get("amount", 0),
        transaction.get("amount", 0),
    )

    reference_score = _score_reference(
        statement_line.get("description", ""),
        transaction.get("reference"),
        transaction.get("source_number"),
    )

    text_score = _score_text(
        statement_line.get("description", ""),
        transaction.get("description", ""),
        transaction.get("contact_name"),
    )

    date_score = _score_date(
        statement_line.get("date", ""),
        transaction.get("date", ""),
    )

    # Weighted sum
    total = (
        w.amount * amount_score
        + w.reference * reference_score
        + w.text * text_score
        + w.date * date_score
    )

    # Bonus: exact amount match is a very strong signal in bank reconciliation
    # Bank descriptions are noisy, but exact amounts on similar dates are almost certain matches
    if amount_score >= 1.0:
        total += 0.15
    # Ensure total doesn't exceed 1.0
    total = min(total, 1.0)

    confidence = _determine_confidence(total)

    return ScoreBreakdown(
        amount_score=round(amount_score, 4),
        reference_score=round(reference_score, 4),
        text_score=round(text_score, 4),
        date_score=round(date_score, 4),
        total_score=round(total, 4),
        confidence=confidence,
    )
