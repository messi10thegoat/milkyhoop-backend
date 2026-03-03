"""
Bank Combination Matcher — Subset-Sum Algorithm for Reconciliation.

Finds combinations of transactions that sum to a statement line (one-to-many)
or multiple statement lines that sum to a transaction (many-to-one).

Stage 5 in the 8-Stage Pipeline.
"""

from dataclasses import dataclass, field
from itertools import combinations
from typing import Optional


# ============ CONFIGURATION ============

MAX_COMBINATION_SIZE = 5       # Max items in a combination
AMOUNT_TOLERANCE = 0.01        # Rp tolerance for "exact" match
AMOUNT_TOLERANCE_PERCENT = 0.5 # 0.5% tolerance for near-match
MAX_CANDIDATES = 50            # Max candidates to consider per target
MAX_RESULTS_PER_TARGET = 3     # Max suggestions per target item


# ============ TYPES ============

@dataclass
class CombinationItem:
    """A single item (statement line or transaction) for matching."""
    id: str
    amount: float
    description: str = ""
    date: str = ""


@dataclass
class CombinationSuggestion:
    """Result of a combination match."""
    match_type: str              # 'one_to_many' or 'many_to_one'
    target_id: str               # The single item ID
    target_amount: float         # The single item amount
    combined_ids: list           # IDs of combined items
    combined_amounts: list       # Amounts of combined items
    combined_total: float        # Sum of combined amounts
    difference: float            # target_amount - combined_total
    confidence: str              # 'exact', 'high', 'medium'
    item_count: int              # Number of combined items
    description: str = ""        # Human-readable description


# ============ CORE ALGORITHM ============

def _find_subsets(
    target: float,
    candidates: list[CombinationItem],
    max_size: int = MAX_COMBINATION_SIZE,
    tolerance: float = AMOUNT_TOLERANCE,
    tolerance_pct: float = AMOUNT_TOLERANCE_PERCENT,
) -> list[tuple[list[CombinationItem], float, str]]:
    """
    Find subsets of candidates that sum to target amount.

    Returns list of (items, difference, confidence) tuples.
    """
    results = []

    # Limit candidates to avoid combinatorial explosion
    # Sort by amount descending so larger amounts checked first
    cands = sorted(candidates, key=lambda c: abs(c.amount), reverse=True)[:MAX_CANDIDATES]

    # Try combinations of size 2..max_size
    for size in range(2, min(max_size + 1, len(cands) + 1)):
        for combo in combinations(cands, size):
            combo_total = sum(c.amount for c in combo)
            diff = abs(target - combo_total)

            # Exact match (within tolerance)
            if diff <= tolerance:
                results.append((list(combo), target - combo_total, "exact"))
                if len(results) >= MAX_RESULTS_PER_TARGET:
                    return results
                continue

            # Near match (within percentage tolerance)
            pct_diff = (diff / abs(target)) * 100 if target != 0 else 100
            if pct_diff <= tolerance_pct:
                confidence = "high" if pct_diff <= 0.1 else "medium"
                results.append((list(combo), target - combo_total, confidence))
                if len(results) >= MAX_RESULTS_PER_TARGET:
                    return results

    return results


def find_combination_matches(
    statement_lines: list[CombinationItem],
    transactions: list[CombinationItem],
    max_size: int = MAX_COMBINATION_SIZE,
) -> list[CombinationSuggestion]:
    """
    Find combination matches between statement lines and transactions.

    Two directions:
    1. One-to-many: 1 statement line → N transactions
    2. Many-to-one: N statement lines → 1 transaction

    Only considers unmatched items (caller should filter).
    """
    suggestions: list[CombinationSuggestion] = []
    used_txn_ids: set[str] = set()
    used_stmt_ids: set[str] = set()

    # --- Direction 1: One statement line matched by N transactions ---
    for stmt in statement_lines:
        if stmt.id in used_stmt_ids:
            continue

        # Filter out already-used transactions
        available_txns = [t for t in transactions if t.id not in used_txn_ids]
        if len(available_txns) < 2:
            continue

        subsets = _find_subsets(
            target=stmt.amount,
            candidates=available_txns,
            max_size=max_size,
        )

        for items, diff, confidence in subsets:
            suggestion = CombinationSuggestion(
                match_type="one_to_many",
                target_id=stmt.id,
                target_amount=stmt.amount,
                combined_ids=[i.id for i in items],
                combined_amounts=[i.amount for i in items],
                combined_total=sum(i.amount for i in items),
                difference=diff,
                confidence=confidence,
                item_count=len(items),
                description=f"{len(items)} transaksi = 1 mutasi bank",
            )
            suggestions.append(suggestion)

            # Mark as used if exact match (prevent double-suggestion)
            if confidence == "exact":
                used_stmt_ids.add(stmt.id)
                for i in items:
                    used_txn_ids.add(i.id)
                break  # Only take best exact match per statement line

    # --- Direction 2: N statement lines matched by 1 transaction ---
    for txn in transactions:
        if txn.id in used_txn_ids:
            continue

        # Filter out already-used statement lines
        available_stmts = [s for s in statement_lines if s.id not in used_stmt_ids]
        if len(available_stmts) < 2:
            continue

        subsets = _find_subsets(
            target=txn.amount,
            candidates=available_stmts,
            max_size=max_size,
        )

        for items, diff, confidence in subsets:
            suggestion = CombinationSuggestion(
                match_type="many_to_one",
                target_id=txn.id,
                target_amount=txn.amount,
                combined_ids=[i.id for i in items],
                combined_amounts=[i.amount for i in items],
                combined_total=sum(i.amount for i in items),
                difference=diff,
                confidence=confidence,
                item_count=len(items),
                description=f"{len(items)} mutasi bank = 1 transaksi",
            )
            suggestions.append(suggestion)

            if confidence == "exact":
                used_txn_ids.add(txn.id)
                for i in items:
                    used_stmt_ids.add(i.id)
                break

    # Sort by confidence: exact > high > medium, then by fewer items
    confidence_order = {"exact": 0, "high": 1, "medium": 2}
    suggestions.sort(key=lambda s: (confidence_order.get(s.confidence, 3), s.item_count))

    return suggestions
