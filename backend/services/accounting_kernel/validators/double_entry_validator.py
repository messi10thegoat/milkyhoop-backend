"""
Double-Entry Bookkeeping Validator
==================================

Validates that all journal entries follow double-entry accounting rules.
"""
from decimal import Decimal
from typing import List, Tuple

from ..models.journal import JournalLineInput, CreateJournalRequest
from ..config import settings


class DoubleEntryValidator:
    """
    Validator for double-entry bookkeeping rules.

    Rules enforced:
    1. Every journal must have at least 2 lines
    2. Sum of debits must equal sum of credits
    3. Each line must have either debit or credit (not both)
    4. Amounts must be non-negative
    """

    def __init__(self, tolerance: float = None):
        """
        Initialize validator.

        Args:
            tolerance: Acceptable difference for floating point comparison
                      (default from settings)
        """
        self.tolerance = tolerance or settings.accounting.BALANCE_TOLERANCE

    def validate_lines(
        self,
        lines: List[JournalLineInput]
    ) -> Tuple[bool, List[str]]:
        """
        Validate a list of journal lines.

        Args:
            lines: List of JournalLineInput

        Returns:
            Tuple of (is_valid, error_messages)
        """
        errors = []

        # Rule 1: At least 2 lines
        if len(lines) < 2:
            errors.append(
                "Journal must have at least 2 lines for double-entry bookkeeping"
            )

        # Rule 2: Each line must have either debit or credit
        for idx, line in enumerate(lines, 1):
            if line.debit < 0:
                errors.append(f"Line {idx}: Debit cannot be negative")
            if line.credit < 0:
                errors.append(f"Line {idx}: Credit cannot be negative")
            if line.debit > 0 and line.credit > 0:
                errors.append(
                    f"Line {idx}: A line cannot have both debit and credit"
                )
            if line.debit == 0 and line.credit == 0:
                errors.append(
                    f"Line {idx}: A line must have either debit or credit"
                )

        # Rule 3: Debits must equal credits
        total_debit = sum(line.debit for line in lines)
        total_credit = sum(line.credit for line in lines)

        if abs(total_debit - total_credit) >= Decimal(str(self.tolerance)):
            errors.append(
                f"Journal is not balanced: "
                f"Total Debit={total_debit:,.2f}, Total Credit={total_credit:,.2f}, "
                f"Difference={abs(total_debit - total_credit):,.2f}"
            )

        return len(errors) == 0, errors

    def validate_request(
        self,
        request: CreateJournalRequest
    ) -> Tuple[bool, List[str]]:
        """
        Validate a CreateJournalRequest.

        Args:
            request: Journal creation request

        Returns:
            Tuple of (is_valid, error_messages)
        """
        errors = []

        # Validate required fields
        if not request.tenant_id:
            errors.append("tenant_id is required")

        if not request.journal_date:
            errors.append("journal_date is required")

        if not request.lines:
            errors.append("lines are required")
            return False, errors

        # Validate lines
        is_valid, line_errors = self.validate_lines(request.lines)
        errors.extend(line_errors)

        return len(errors) == 0, errors

    def calculate_totals(
        self,
        lines: List[JournalLineInput]
    ) -> Tuple[Decimal, Decimal, Decimal]:
        """
        Calculate totals from lines.

        Returns:
            Tuple of (total_debit, total_credit, difference)
        """
        total_debit = sum(line.debit for line in lines)
        total_credit = sum(line.credit for line in lines)
        difference = abs(total_debit - total_credit)

        return total_debit, total_credit, difference

    def is_balanced(self, lines: List[JournalLineInput]) -> bool:
        """
        Quick check if lines are balanced.

        Args:
            lines: List of journal lines

        Returns:
            True if balanced within tolerance
        """
        total_debit, total_credit, difference = self.calculate_totals(lines)
        return difference < Decimal(str(self.tolerance))
