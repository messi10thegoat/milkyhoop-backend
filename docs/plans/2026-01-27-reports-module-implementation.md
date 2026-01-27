# Reports Module Enhancements - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add drill-down, period comparison, custom aging buckets, and export capabilities to the reports module.

**Architecture:** Extend existing `reports.py` router with new endpoints. Add schemas to `schemas/reports.py`. Create `services/export_service.py` for Excel/CSV generation. Leverage existing `PDFService` for PDF exports.

**Tech Stack:** FastAPI, asyncpg, Pydantic, WeasyPrint (PDF), openpyxl (Excel), csv (CSV)

---

## Task 1: Add openpyxl Dependency

**Files:**
- Modify: `backend/api_gateway/requirements.txt`

**Step 1: Add openpyxl to requirements.txt**

Add after the PDF Generation section:

```
# Excel Export
openpyxl==3.1.2
```

**Step 2: Verify installation works**

Run: `cd /root/milkyhoop-dev/backend/api_gateway && pip install openpyxl==3.1.2`

Expected: Successfully installed openpyxl-3.1.2

**Step 3: Commit**

```bash
git add backend/api_gateway/requirements.txt
git commit -m "chore: add openpyxl for Excel export"
```

---

## Task 2: Create Drill-Down Schemas

**Files:**
- Create: `backend/api_gateway/app/schemas/drill_down.py`
- Test: `backend/api_gateway/tests/test_drill_down_schemas.py`

**Step 1: Write the failing test**

Create `backend/api_gateway/tests/test_drill_down_schemas.py`:

```python
"""Tests for drill-down report schemas."""
import pytest
from datetime import date
from decimal import Decimal
from uuid import uuid4


class TestDrillDownSchemas:
    """Test drill-down Pydantic schemas."""

    def test_schema_import(self):
        """Schema classes can be imported."""
        from app.schemas.drill_down import (
            DrillDownTransaction,
            DrillDownResponse,
            DrillDownRequest,
        )
        assert DrillDownTransaction is not None
        assert DrillDownResponse is not None
        assert DrillDownRequest is not None

    def test_drill_down_transaction_model(self):
        """DrillDownTransaction model validates correctly."""
        from app.schemas.drill_down import DrillDownTransaction

        tx = DrillDownTransaction(
            journal_id=uuid4(),
            journal_number="JE-2026-0001",
            entry_date=date(2026, 1, 15),
            source_type="invoice",
            source_id=uuid4(),
            description="Sales to PT Maju",
            memo="INV-001",
            debit=Decimal("0"),
            credit=Decimal("10000000"),
            running_balance=Decimal("10000000"),
        )
        assert tx.journal_number == "JE-2026-0001"
        assert tx.credit == Decimal("10000000")

    def test_drill_down_response_model(self):
        """DrillDownResponse model validates correctly."""
        from app.schemas.drill_down import DrillDownResponse, DrillDownTransaction

        response = DrillDownResponse(
            account_id=uuid4(),
            account_code="4-1001",
            account_name="Penjualan Barang",
            account_type="REVENUE",
            normal_balance="CREDIT",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            opening_balance=Decimal("0"),
            total_debit=Decimal("0"),
            total_credit=Decimal("50000000"),
            closing_balance=Decimal("50000000"),
            transactions=[],
            pagination={"page": 1, "limit": 50, "total": 0, "has_more": False},
        )
        assert response.account_code == "4-1001"
        assert response.closing_balance == Decimal("50000000")
```

**Step 2: Run test to verify it fails**

Run: `cd /root/milkyhoop-dev/backend/api_gateway && python -m pytest tests/test_drill_down_schemas.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.schemas.drill_down'`

**Step 3: Write minimal implementation**

Create `backend/api_gateway/app/schemas/drill_down.py`:

```python
"""
Drill-Down Report Schemas
For drilling into account transaction details from P&L or Balance Sheet.
"""
from datetime import date
from decimal import Decimal
from typing import Optional, List, Dict, Any
from uuid import UUID
from pydantic import BaseModel, Field


class DrillDownRequest(BaseModel):
    """Request parameters for drill-down query."""
    account_id: UUID
    start_date: date
    end_date: date
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=50, ge=1, le=200)


class DrillDownTransaction(BaseModel):
    """Single transaction in drill-down results."""
    journal_id: UUID
    journal_number: str
    entry_date: date
    source_type: Optional[str] = None  # invoice, bill, payment, manual
    source_id: Optional[UUID] = None
    description: Optional[str] = None
    memo: Optional[str] = None
    debit: Decimal
    credit: Decimal
    running_balance: Decimal


class DrillDownResponse(BaseModel):
    """Response for drill-down endpoint."""
    account_id: UUID
    account_code: str
    account_name: str
    account_type: str
    normal_balance: str  # DEBIT or CREDIT
    period_start: date
    period_end: date
    opening_balance: Decimal
    total_debit: Decimal
    total_credit: Decimal
    closing_balance: Decimal
    transactions: List[DrillDownTransaction]
    pagination: Dict[str, Any]
```

**Step 4: Run test to verify it passes**

Run: `cd /root/milkyhoop-dev/backend/api_gateway && python -m pytest tests/test_drill_down_schemas.py -v`

Expected: All 3 tests PASS

**Step 5: Commit**

```bash
git add backend/api_gateway/app/schemas/drill_down.py backend/api_gateway/tests/test_drill_down_schemas.py
git commit -m "feat(reports): add drill-down schemas"
```

---

## Task 3: Implement Drill-Down Endpoint

**Files:**
- Modify: `backend/api_gateway/app/routers/reports.py`
- Test: `backend/api_gateway/tests/test_drill_down_endpoint.py`

**Step 1: Write the failing test**

Create `backend/api_gateway/tests/test_drill_down_endpoint.py`:

```python
"""Tests for drill-down endpoint."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4
from datetime import date
from decimal import Decimal


class TestDrillDownEndpoint:
    """Test /reports/drill-down endpoint."""

    @pytest.fixture
    def mock_user_context(self):
        return {
            "tenant_id": "test-tenant",
            "user_id": str(uuid4()),
        }

    @pytest.fixture
    def mock_account_data(self):
        return {
            "id": uuid4(),
            "code": "4-1001",
            "name": "Penjualan Barang",
            "type": "REVENUE",
            "normal_balance": "CREDIT",
        }

    @pytest.fixture
    def mock_journal_lines(self):
        return [
            {
                "journal_id": uuid4(),
                "journal_number": "JE-2026-0001",
                "entry_date": date(2026, 1, 5),
                "source_type": "invoice",
                "source_id": uuid4(),
                "description": "Sales to PT Maju",
                "memo": "INV-001",
                "debit": Decimal("0"),
                "credit": Decimal("10000000"),
            },
            {
                "journal_id": uuid4(),
                "journal_number": "JE-2026-0002",
                "entry_date": date(2026, 1, 10),
                "source_type": "invoice",
                "source_id": uuid4(),
                "description": "Sales to CV Jaya",
                "memo": "INV-002",
                "debit": Decimal("0"),
                "credit": Decimal("15000000"),
            },
        ]

    def test_drill_down_endpoint_exists(self):
        """Endpoint is registered in router."""
        from app.routers.reports import router

        routes = [route.path for route in router.routes]
        assert "/drill-down" in routes

    def test_drill_down_requires_account_id(self):
        """Endpoint requires account_id parameter."""
        from app.schemas.drill_down import DrillDownRequest

        with pytest.raises(ValueError):
            DrillDownRequest(
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 31),
            )

    def test_running_balance_calculation_credit_account(self, mock_journal_lines):
        """Running balance calculated correctly for credit-normal accounts."""
        # For CREDIT normal (Revenue), running_balance = opening + credit - debit
        opening = Decimal("0")
        running = opening

        for line in mock_journal_lines:
            running = running + line["credit"] - line["debit"]

        assert running == Decimal("25000000")

    def test_running_balance_calculation_debit_account(self):
        """Running balance calculated correctly for debit-normal accounts."""
        # For DEBIT normal (Assets), running_balance = opening + debit - credit
        lines = [
            {"debit": Decimal("5000000"), "credit": Decimal("0")},
            {"debit": Decimal("0"), "credit": Decimal("2000000")},
        ]
        opening = Decimal("10000000")
        running = opening

        for line in lines:
            running = running + line["debit"] - line["credit"]

        assert running == Decimal("13000000")
```

**Step 2: Run test to verify it fails**

Run: `cd /root/milkyhoop-dev/backend/api_gateway && python -m pytest tests/test_drill_down_endpoint.py::TestDrillDownEndpoint::test_drill_down_endpoint_exists -v`

Expected: FAIL with `AssertionError: assert '/drill-down' in [...]`

**Step 3: Write minimal implementation**

Add to `backend/api_gateway/app/routers/reports.py` at the end of the file (before the last line):

```python
# ========================================
# Drill-Down Report
# ========================================

from ..schemas.drill_down import (
    DrillDownTransaction,
    DrillDownResponse,
)


@router.get("/drill-down", response_model=DrillDownResponse)
async def get_drill_down(
    request: Request,
    account_id: uuid.UUID = Query(..., description="Account ID to drill into"),
    start_date: date = Query(..., description="Start of period"),
    end_date: date = Query(..., description="End of period"),
    page: int = Query(default=1, ge=1, description="Page number"),
    limit: int = Query(default=50, ge=1, le=200, description="Items per page"),
):
    """
    Get transaction details for a specific account.

    Use this to drill down from P&L or Balance Sheet line items
    to see the underlying journal entries.
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)

            # Get account info
            account = await conn.fetchrow("""
                SELECT id, code, name, type, normal_balance
                FROM chart_of_accounts
                WHERE id = $1 AND tenant_id = $2
            """, account_id, tenant_id)

            if not account:
                raise HTTPException(status_code=404, detail="Account not found")

            # Calculate opening balance (before start_date)
            opening_row = await conn.fetchrow("""
                SELECT
                    COALESCE(SUM(jl.debit), 0) as total_debit,
                    COALESCE(SUM(jl.credit), 0) as total_credit
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_entry_id
                WHERE jl.account_id = $1
                  AND je.tenant_id = $2
                  AND je.entry_date < $3
                  AND je.status = 'POSTED'
            """, account_id, tenant_id, start_date)

            if account['normal_balance'] == 'DEBIT':
                opening_balance = (opening_row['total_debit'] or 0) - (opening_row['total_credit'] or 0)
            else:
                opening_balance = (opening_row['total_credit'] or 0) - (opening_row['total_debit'] or 0)

            # Get total count for pagination
            total_count = await conn.fetchval("""
                SELECT COUNT(*)
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_entry_id
                WHERE jl.account_id = $1
                  AND je.tenant_id = $2
                  AND je.entry_date BETWEEN $3 AND $4
                  AND je.status = 'POSTED'
            """, account_id, tenant_id, start_date, end_date)

            # Get transactions with pagination
            offset = (page - 1) * limit
            rows = await conn.fetch("""
                SELECT
                    je.id as journal_id,
                    je.journal_number,
                    je.entry_date,
                    je.source_type,
                    je.source_id,
                    je.description,
                    jl.memo,
                    jl.debit,
                    jl.credit
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_entry_id
                WHERE jl.account_id = $1
                  AND je.tenant_id = $2
                  AND je.entry_date BETWEEN $3 AND $4
                  AND je.status = 'POSTED'
                ORDER BY je.entry_date, je.created_at
                OFFSET $5 LIMIT $6
            """, account_id, tenant_id, start_date, end_date, offset, limit)

            # Calculate running balance and build transactions
            transactions = []
            running_balance = opening_balance
            total_debit = 0
            total_credit = 0

            for row in rows:
                debit = row['debit'] or 0
                credit = row['credit'] or 0
                total_debit += debit
                total_credit += credit

                if account['normal_balance'] == 'DEBIT':
                    running_balance = running_balance + debit - credit
                else:
                    running_balance = running_balance + credit - debit

                transactions.append(DrillDownTransaction(
                    journal_id=row['journal_id'],
                    journal_number=row['journal_number'],
                    entry_date=row['entry_date'],
                    source_type=row['source_type'],
                    source_id=row['source_id'],
                    description=row['description'],
                    memo=row['memo'],
                    debit=debit,
                    credit=credit,
                    running_balance=running_balance,
                ))

            closing_balance = opening_balance
            if account['normal_balance'] == 'DEBIT':
                closing_balance = opening_balance + total_debit - total_credit
            else:
                closing_balance = opening_balance + total_credit - total_debit

            logger.info(f"Drill-down generated: tenant={tenant_id}, account={account['code']}, transactions={len(transactions)}")

            return DrillDownResponse(
                account_id=account['id'],
                account_code=account['code'],
                account_name=account['name'],
                account_type=account['type'],
                normal_balance=account['normal_balance'],
                period_start=start_date,
                period_end=end_date,
                opening_balance=opening_balance,
                total_debit=total_debit,
                total_credit=total_credit,
                closing_balance=closing_balance,
                transactions=transactions,
                pagination={
                    "page": page,
                    "limit": limit,
                    "total": total_count,
                    "has_more": offset + len(transactions) < total_count,
                },
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get drill-down error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate drill-down report")
```

**Step 4: Run tests to verify they pass**

Run: `cd /root/milkyhoop-dev/backend/api_gateway && python -m pytest tests/test_drill_down_endpoint.py -v`

Expected: All tests PASS

**Step 5: Commit**

```bash
git add backend/api_gateway/app/routers/reports.py backend/api_gateway/app/schemas/drill_down.py backend/api_gateway/tests/test_drill_down_endpoint.py
git commit -m "feat(reports): add drill-down endpoint for account transactions"
```

---

## Task 4: Create Period Comparison Schemas

**Files:**
- Create: `backend/api_gateway/app/schemas/report_comparison.py`
- Test: `backend/api_gateway/tests/test_report_comparison_schemas.py`

**Step 1: Write the failing test**

Create `backend/api_gateway/tests/test_report_comparison_schemas.py`:

```python
"""Tests for report comparison schemas."""
import pytest
from datetime import date
from decimal import Decimal
from uuid import uuid4


class TestReportComparisonSchemas:
    """Test comparison Pydantic schemas."""

    def test_schema_import(self):
        """Schema classes can be imported."""
        from app.schemas.report_comparison import (
            ComparisonType,
            PeriodInfo,
            LineItemWithComparison,
            SectionWithComparison,
        )
        assert ComparisonType is not None
        assert PeriodInfo is not None
        assert LineItemWithComparison is not None
        assert SectionWithComparison is not None

    def test_comparison_type_enum(self):
        """ComparisonType enum has expected values."""
        from app.schemas.report_comparison import ComparisonType

        assert ComparisonType.PREVIOUS_YEAR.value == "previous-year"
        assert ComparisonType.PREVIOUS_PERIOD.value == "previous-period"
        assert ComparisonType.CUSTOM.value == "custom"

    def test_line_item_variance_calculation(self):
        """LineItemWithComparison calculates variance correctly."""
        from app.schemas.report_comparison import LineItemWithComparison

        item = LineItemWithComparison(
            account_id=uuid4(),
            account_code="4-1001",
            account_name="Penjualan",
            amount=Decimal("150000000"),
            comparison_amount=Decimal("120000000"),
        )

        # Variance = current - comparison
        expected_variance = Decimal("30000000")
        # Variance percent = (variance / comparison) * 100
        expected_pct = Decimal("25.0")

        assert item.variance == expected_variance
        assert item.variance_percent == expected_pct

    def test_line_item_no_comparison(self):
        """LineItemWithComparison handles no comparison data."""
        from app.schemas.report_comparison import LineItemWithComparison

        item = LineItemWithComparison(
            account_id=uuid4(),
            account_code="4-1001",
            account_name="Penjualan",
            amount=Decimal("150000000"),
            comparison_amount=None,
        )

        assert item.variance is None
        assert item.variance_percent is None
```

**Step 2: Run test to verify it fails**

Run: `cd /root/milkyhoop-dev/backend/api_gateway && python -m pytest tests/test_report_comparison_schemas.py -v`

Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

Create `backend/api_gateway/app/schemas/report_comparison.py`:

```python
"""
Report Comparison Schemas
For period-over-period comparison in financial reports.
"""
from datetime import date
from decimal import Decimal
from typing import Optional, List
from uuid import UUID
from pydantic import BaseModel, Field, computed_field
from enum import Enum


class ComparisonType(str, Enum):
    """Type of period comparison."""
    PREVIOUS_YEAR = "previous-year"      # Same period, previous year (PSAK compliant)
    PREVIOUS_PERIOD = "previous-period"  # Immediately prior period
    CUSTOM = "custom"                    # Custom date range


class PeriodInfo(BaseModel):
    """Information about a reporting period."""
    start_date: date
    end_date: date
    label: str  # e.g., "Januari 2026"


class LineItemWithComparison(BaseModel):
    """A report line item with optional comparison data."""
    account_id: UUID
    account_code: str
    account_name: str
    amount: Decimal
    comparison_amount: Optional[Decimal] = None

    @computed_field
    @property
    def variance(self) -> Optional[Decimal]:
        """Calculate variance (current - comparison)."""
        if self.comparison_amount is None:
            return None
        return self.amount - self.comparison_amount

    @computed_field
    @property
    def variance_percent(self) -> Optional[Decimal]:
        """Calculate variance percentage."""
        if self.comparison_amount is None or self.comparison_amount == 0:
            return None
        return ((self.amount - self.comparison_amount) / self.comparison_amount * 100).quantize(Decimal("0.1"))


class SectionWithComparison(BaseModel):
    """A report section (e.g., Revenue) with comparison totals."""
    label: str
    items: List[LineItemWithComparison]
    total: Decimal
    comparison_total: Optional[Decimal] = None

    @computed_field
    @property
    def variance(self) -> Optional[Decimal]:
        if self.comparison_total is None:
            return None
        return self.total - self.comparison_total

    @computed_field
    @property
    def variance_percent(self) -> Optional[Decimal]:
        if self.comparison_total is None or self.comparison_total == 0:
            return None
        return ((self.total - self.comparison_total) / self.comparison_total * 100).quantize(Decimal("0.1"))


class ProfitLossComparisonResponse(BaseModel):
    """Profit & Loss report with period comparison."""
    success: bool = True
    period: PeriodInfo
    comparison_period: Optional[PeriodInfo] = None
    comparison_type: Optional[ComparisonType] = None
    basis: str  # cash or accrual

    revenue: SectionWithComparison
    cost_of_goods_sold: SectionWithComparison
    gross_profit: Decimal
    gross_profit_comparison: Optional[Decimal] = None

    operating_expenses: SectionWithComparison
    operating_income: Decimal
    operating_income_comparison: Optional[Decimal] = None

    other_income: SectionWithComparison
    other_expenses: SectionWithComparison

    net_income: Decimal
    net_income_comparison: Optional[Decimal] = None

    @computed_field
    @property
    def gross_profit_variance(self) -> Optional[Decimal]:
        if self.gross_profit_comparison is None:
            return None
        return self.gross_profit - self.gross_profit_comparison

    @computed_field
    @property
    def net_income_variance(self) -> Optional[Decimal]:
        if self.net_income_comparison is None:
            return None
        return self.net_income - self.net_income_comparison
```

**Step 4: Run tests to verify they pass**

Run: `cd /root/milkyhoop-dev/backend/api_gateway && python -m pytest tests/test_report_comparison_schemas.py -v`

Expected: All tests PASS

**Step 5: Commit**

```bash
git add backend/api_gateway/app/schemas/report_comparison.py backend/api_gateway/tests/test_report_comparison_schemas.py
git commit -m "feat(reports): add period comparison schemas"
```

---

## Task 5: Add Comparison to Profit/Loss Endpoint

**Files:**
- Modify: `backend/api_gateway/app/routers/reports.py`
- Test: `backend/api_gateway/tests/test_profit_loss_comparison.py`

**Step 1: Write the failing test**

Create `backend/api_gateway/tests/test_profit_loss_comparison.py`:

```python
"""Tests for P&L comparison functionality."""
import pytest
from datetime import date, timedelta


class TestProfitLossComparison:
    """Test P&L period comparison."""

    def test_comparison_date_calculation_previous_year(self):
        """Previous year comparison calculates correct dates."""
        from app.routers.reports import get_comparison_dates
        from app.schemas.report_comparison import ComparisonType

        start = date(2026, 1, 1)
        end = date(2026, 1, 31)

        comp_start, comp_end = get_comparison_dates(
            start, end, ComparisonType.PREVIOUS_YEAR
        )

        assert comp_start == date(2025, 1, 1)
        assert comp_end == date(2025, 1, 31)

    def test_comparison_date_calculation_previous_period(self):
        """Previous period comparison calculates correct dates."""
        from app.routers.reports import get_comparison_dates
        from app.schemas.report_comparison import ComparisonType

        start = date(2026, 1, 1)
        end = date(2026, 1, 31)

        comp_start, comp_end = get_comparison_dates(
            start, end, ComparisonType.PREVIOUS_PERIOD
        )

        # Previous period for Jan 2026 is Dec 2025
        assert comp_start == date(2025, 12, 1)
        assert comp_end == date(2025, 12, 31)

    def test_comparison_date_calculation_custom(self):
        """Custom comparison uses provided dates."""
        from app.routers.reports import get_comparison_dates
        from app.schemas.report_comparison import ComparisonType

        start = date(2026, 1, 1)
        end = date(2026, 1, 31)
        custom_start = date(2025, 6, 1)
        custom_end = date(2025, 6, 30)

        comp_start, comp_end = get_comparison_dates(
            start, end, ComparisonType.CUSTOM,
            custom_start=custom_start,
            custom_end=custom_end
        )

        assert comp_start == custom_start
        assert comp_end == custom_end

    def test_profit_loss_endpoint_accepts_comparison_param(self):
        """P&L endpoint accepts comparison query parameter."""
        from app.routers.reports import router

        # Find the profit-loss route
        for route in router.routes:
            if "/profit-loss/" in route.path:
                # Check that comparison parameter exists
                param_names = [p.name for p in route.dependant.query_params]
                assert "comparison" in param_names
                break
        else:
            pytest.fail("profit-loss route not found")
```

**Step 2: Run test to verify it fails**

Run: `cd /root/milkyhoop-dev/backend/api_gateway && python -m pytest tests/test_profit_loss_comparison.py::TestProfitLossComparison::test_comparison_date_calculation_previous_year -v`

Expected: FAIL with `ImportError: cannot import name 'get_comparison_dates'`

**Step 3: Write the comparison date helper function**

Add to `backend/api_gateway/app/routers/reports.py` after the `parse_periode` function:

```python
from ..schemas.report_comparison import ComparisonType, PeriodInfo

def get_comparison_dates(
    start_date: date,
    end_date: date,
    comparison_type: ComparisonType,
    custom_start: Optional[date] = None,
    custom_end: Optional[date] = None,
) -> tuple[date, date]:
    """
    Calculate comparison period dates based on comparison type.

    Args:
        start_date: Start of current period
        end_date: End of current period
        comparison_type: Type of comparison
        custom_start: Custom start date (required if type is CUSTOM)
        custom_end: Custom end date (required if type is CUSTOM)

    Returns:
        Tuple of (comparison_start, comparison_end)
    """
    if comparison_type == ComparisonType.PREVIOUS_YEAR:
        # Same period, previous year
        comp_start = start_date.replace(year=start_date.year - 1)
        comp_end = end_date.replace(year=end_date.year - 1)
    elif comparison_type == ComparisonType.PREVIOUS_PERIOD:
        # Immediately prior period of same length
        delta = end_date - start_date
        comp_end = start_date - timedelta(days=1)
        comp_start = comp_end - delta
    elif comparison_type == ComparisonType.CUSTOM:
        if not custom_start or not custom_end:
            raise ValueError("Custom comparison requires custom_start and custom_end")
        comp_start = custom_start
        comp_end = custom_end
    else:
        raise ValueError(f"Unknown comparison type: {comparison_type}")

    return comp_start, comp_end


def get_period_label(start_date: date, end_date: date) -> str:
    """Generate human-readable period label."""
    months_id = [
        "Januari", "Februari", "Maret", "April", "Mei", "Juni",
        "Juli", "Agustus", "September", "Oktober", "November", "Desember"
    ]

    if start_date.month == end_date.month and start_date.year == end_date.year:
        # Single month
        return f"{months_id[start_date.month - 1]} {start_date.year}"
    elif start_date.year == end_date.year:
        # Same year, different months
        return f"{months_id[start_date.month - 1]} - {months_id[end_date.month - 1]} {start_date.year}"
    else:
        # Different years
        return f"{months_id[start_date.month - 1]} {start_date.year} - {months_id[end_date.month - 1]} {end_date.year}"
```

**Step 4: Update the profit-loss endpoint to accept comparison parameter**

Modify the `get_profit_loss_by_basis` function signature in `reports.py`:

```python
@router.get("/profit-loss/{periode}", response_model=ProfitLossReportResponse)
async def get_profit_loss_by_basis(
    request: Request,
    periode: str,
    basis: Optional[Literal["cash", "accrual"]] = Query(
        default=None,
        description="Accounting basis: 'cash' or 'accrual'. If not specified, uses tenant's default."
    ),
    comparison: Optional[Literal["previous-year", "previous-period", "custom"]] = Query(
        default=None,
        description="Comparison type: 'previous-year' (default for PSAK), 'previous-period', or 'custom'"
    ),
    compare_start: Optional[date] = Query(
        default=None,
        description="Custom comparison start date (required if comparison='custom')"
    ),
    compare_end: Optional[date] = Query(
        default=None,
        description="Custom comparison end date (required if comparison='custom')"
    ),
):
```

Note: Full implementation of comparison in the endpoint body will be done after tests pass.

**Step 5: Run tests to verify they pass**

Run: `cd /root/milkyhoop-dev/backend/api_gateway && python -m pytest tests/test_profit_loss_comparison.py -v`

Expected: All tests PASS

**Step 6: Commit**

```bash
git add backend/api_gateway/app/routers/reports.py backend/api_gateway/tests/test_profit_loss_comparison.py
git commit -m "feat(reports): add comparison date calculation and P&L comparison param"
```

---

## Task 6: Add Custom Aging Buckets to Settings

**Files:**
- Create: `backend/migrations/V087__aging_brackets_settings.sql`
- Modify: `backend/api_gateway/app/schemas/accounting_settings.py`
- Test: `backend/api_gateway/tests/test_aging_buckets_settings.py`

**Step 1: Create migration**

Create `backend/migrations/V087__aging_brackets_settings.sql`:

```sql
-- Add custom aging brackets to accounting_settings
ALTER TABLE accounting_settings
ADD COLUMN IF NOT EXISTS aging_brackets_ar JSONB DEFAULT '[0, 30, 60, 90, 120]',
ADD COLUMN IF NOT EXISTS aging_brackets_ap JSONB DEFAULT '[0, 30, 60, 90, 120]';

COMMENT ON COLUMN accounting_settings.aging_brackets_ar IS 'AR aging bracket boundaries in days. Default: [0, 30, 60, 90, 120]';
COMMENT ON COLUMN accounting_settings.aging_brackets_ap IS 'AP aging bracket boundaries in days. Default: [0, 30, 60, 90, 120]';
```

**Step 2: Write the failing test**

Create `backend/api_gateway/tests/test_aging_buckets_settings.py`:

```python
"""Tests for custom aging buckets in settings."""
import pytest


class TestAgingBucketsSettings:
    """Test aging brackets configuration."""

    def test_schema_has_aging_fields(self):
        """AccountingSettingsResponse includes aging bracket fields."""
        from app.schemas.accounting_settings import AccountingSettingsResponse

        fields = AccountingSettingsResponse.model_fields
        assert "aging_brackets_ar" in fields
        assert "aging_brackets_ap" in fields

    def test_update_schema_has_aging_fields(self):
        """UpdateAccountingSettingsRequest includes aging bracket fields."""
        from app.schemas.accounting_settings import UpdateAccountingSettingsRequest

        fields = UpdateAccountingSettingsRequest.model_fields
        assert "aging_brackets_ar" in fields
        assert "aging_brackets_ap" in fields

    def test_default_aging_brackets(self):
        """Default aging brackets are [0, 30, 60, 90, 120]."""
        from app.schemas.accounting_settings import AccountingSettingsResponse

        field = AccountingSettingsResponse.model_fields["aging_brackets_ar"]
        assert field.default == [0, 30, 60, 90, 120]

    def test_aging_brackets_validation(self):
        """Aging brackets must be sorted ascending."""
        from app.schemas.accounting_settings import UpdateAccountingSettingsRequest
        from pydantic import ValidationError

        # Valid - sorted ascending
        valid = UpdateAccountingSettingsRequest(aging_brackets_ar=[0, 15, 30, 60])
        assert valid.aging_brackets_ar == [0, 15, 30, 60]

        # Invalid - not sorted (should raise or auto-sort)
        # For simplicity, we'll auto-sort in the schema
        unsorted = UpdateAccountingSettingsRequest(aging_brackets_ar=[30, 0, 60, 15])
        assert unsorted.aging_brackets_ar == [0, 15, 30, 60]
```

**Step 3: Run test to verify it fails**

Run: `cd /root/milkyhoop-dev/backend/api_gateway && python -m pytest tests/test_aging_buckets_settings.py -v`

Expected: FAIL with `KeyError: 'aging_brackets_ar'`

**Step 4: Update accounting_settings schema**

Modify `backend/api_gateway/app/schemas/accounting_settings.py`, adding to the existing classes:

```python
from pydantic import field_validator

# Add to AccountingSettingsResponse class:
    aging_brackets_ar: List[int] = [0, 30, 60, 90, 120]
    aging_brackets_ap: List[int] = [0, 30, 60, 90, 120]

# Add to UpdateAccountingSettingsRequest class:
    aging_brackets_ar: Optional[List[int]] = None
    aging_brackets_ap: Optional[List[int]] = None

    @field_validator('aging_brackets_ar', 'aging_brackets_ap', mode='before')
    @classmethod
    def sort_brackets(cls, v):
        """Ensure brackets are sorted ascending."""
        if v is not None:
            return sorted(v)
        return v
```

**Step 5: Run tests to verify they pass**

Run: `cd /root/milkyhoop-dev/backend/api_gateway && python -m pytest tests/test_aging_buckets_settings.py -v`

Expected: All tests PASS

**Step 6: Commit**

```bash
git add backend/migrations/V087__aging_brackets_settings.sql backend/api_gateway/app/schemas/accounting_settings.py backend/api_gateway/tests/test_aging_buckets_settings.py
git commit -m "feat(reports): add custom aging brackets to accounting settings"
```

---

## Task 7: Create Export Service

**Files:**
- Create: `backend/api_gateway/app/services/export_service.py`
- Test: `backend/api_gateway/tests/test_export_service.py`

**Step 1: Write the failing test**

Create `backend/api_gateway/tests/test_export_service.py`:

```python
"""Tests for export service."""
import pytest
from decimal import Decimal
from io import BytesIO


class TestExportService:
    """Test export service functionality."""

    @pytest.fixture
    def sample_profit_loss_data(self):
        """Sample P&L data for export testing."""
        return {
            "period": {"start_date": "2026-01-01", "end_date": "2026-01-31", "label": "Januari 2026"},
            "revenue": {
                "label": "Pendapatan",
                "total": 150000000,
                "items": [
                    {"account_code": "4-1001", "account_name": "Penjualan", "amount": 150000000}
                ]
            },
            "cost_of_goods_sold": {"label": "HPP", "total": 80000000, "items": []},
            "gross_profit": 70000000,
            "operating_expenses": {"label": "Biaya Operasional", "total": 35000000, "items": []},
            "operating_income": 35000000,
            "net_income": 35000000,
        }

    def test_export_service_can_be_imported(self):
        """ExportService class exists."""
        from app.services.export_service import ExportService
        assert ExportService is not None

    def test_generate_csv(self, sample_profit_loss_data):
        """Generate CSV from P&L data."""
        from app.services.export_service import ExportService

        service = ExportService()
        csv_bytes = service.generate_profit_loss_csv(sample_profit_loss_data)

        assert isinstance(csv_bytes, bytes)
        content = csv_bytes.decode('utf-8')
        assert "Penjualan" in content
        assert "150000000" in content or "150,000,000" in content

    def test_generate_excel(self, sample_profit_loss_data):
        """Generate Excel from P&L data."""
        from app.services.export_service import ExportService

        service = ExportService()
        excel_bytes = service.generate_profit_loss_excel(sample_profit_loss_data)

        assert isinstance(excel_bytes, bytes)
        # Excel files start with PK (ZIP format)
        assert excel_bytes[:2] == b'PK'
```

**Step 2: Run test to verify it fails**

Run: `cd /root/milkyhoop-dev/backend/api_gateway && python -m pytest tests/test_export_service.py -v`

Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

Create `backend/api_gateway/app/services/export_service.py`:

```python
"""
Export Service - Generate PDF, Excel, CSV from report data.
"""
import csv
import io
import logging
from typing import Dict, Any, List
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
from openpyxl.utils import get_column_letter

logger = logging.getLogger(__name__)


class ExportService:
    """Generate exportable files from report data."""

    @staticmethod
    def format_currency(amount: Any) -> str:
        """Format amount as IDR currency."""
        try:
            value = float(amount) if amount else 0
            return f"{value:,.0f}".replace(",", ".")
        except (ValueError, TypeError):
            return "0"

    def generate_profit_loss_csv(self, data: Dict[str, Any]) -> bytes:
        """
        Generate CSV from Profit & Loss data.

        Args:
            data: P&L report data dict

        Returns:
            CSV file as bytes
        """
        output = io.StringIO()
        writer = csv.writer(output)

        # Header
        period = data.get("period", {})
        writer.writerow(["Laporan Laba Rugi"])
        writer.writerow([f"Periode: {period.get('label', '')}"])
        writer.writerow([])

        # Column headers
        writer.writerow(["Kode Akun", "Nama Akun", "Jumlah"])

        # Revenue section
        writer.writerow(["", "PENDAPATAN", ""])
        revenue = data.get("revenue", {})
        for item in revenue.get("items", []):
            writer.writerow([
                item.get("account_code", ""),
                item.get("account_name", ""),
                item.get("amount", 0),
            ])
        writer.writerow(["", "Total Pendapatan", revenue.get("total", 0)])
        writer.writerow([])

        # COGS section
        writer.writerow(["", "HARGA POKOK PENJUALAN", ""])
        cogs = data.get("cost_of_goods_sold", {})
        for item in cogs.get("items", []):
            writer.writerow([
                item.get("account_code", ""),
                item.get("account_name", ""),
                item.get("amount", 0),
            ])
        writer.writerow(["", "Total HPP", cogs.get("total", 0)])
        writer.writerow([])

        # Gross profit
        writer.writerow(["", "LABA KOTOR", data.get("gross_profit", 0)])
        writer.writerow([])

        # Operating expenses
        writer.writerow(["", "BIAYA OPERASIONAL", ""])
        opex = data.get("operating_expenses", {})
        for item in opex.get("items", []):
            writer.writerow([
                item.get("account_code", ""),
                item.get("account_name", ""),
                item.get("amount", 0),
            ])
        writer.writerow(["", "Total Biaya Operasional", opex.get("total", 0)])
        writer.writerow([])

        # Net income
        writer.writerow(["", "LABA BERSIH", data.get("net_income", 0)])

        return output.getvalue().encode('utf-8')

    def generate_profit_loss_excel(self, data: Dict[str, Any]) -> bytes:
        """
        Generate Excel from Profit & Loss data.

        Args:
            data: P&L report data dict

        Returns:
            Excel file as bytes
        """
        wb = Workbook()
        ws = wb.active
        ws.title = "Laba Rugi"

        # Styles
        header_font = Font(bold=True, size=14)
        section_font = Font(bold=True, size=11)
        currency_format = '#,##0'

        # Header
        period = data.get("period", {})
        ws['A1'] = "Laporan Laba Rugi"
        ws['A1'].font = header_font
        ws['A2'] = f"Periode: {period.get('label', '')}"

        row = 4

        # Column headers
        ws.cell(row=row, column=1, value="Kode Akun")
        ws.cell(row=row, column=2, value="Nama Akun")
        ws.cell(row=row, column=3, value="Jumlah")
        for col in range(1, 4):
            ws.cell(row=row, column=col).font = Font(bold=True)
        row += 1

        def write_section(section_name: str, section_data: Dict, start_row: int) -> int:
            """Write a section and return next row."""
            ws.cell(row=start_row, column=2, value=section_name)
            ws.cell(row=start_row, column=2).font = section_font
            start_row += 1

            for item in section_data.get("items", []):
                ws.cell(row=start_row, column=1, value=item.get("account_code", ""))
                ws.cell(row=start_row, column=2, value=item.get("account_name", ""))
                ws.cell(row=start_row, column=3, value=item.get("amount", 0))
                ws.cell(row=start_row, column=3).number_format = currency_format
                start_row += 1

            # Total
            ws.cell(row=start_row, column=2, value=f"Total {section_name}")
            ws.cell(row=start_row, column=2).font = Font(bold=True)
            ws.cell(row=start_row, column=3, value=section_data.get("total", 0))
            ws.cell(row=start_row, column=3).number_format = currency_format
            ws.cell(row=start_row, column=3).font = Font(bold=True)

            return start_row + 2

        # Revenue
        row = write_section("PENDAPATAN", data.get("revenue", {}), row)

        # COGS
        row = write_section("HARGA POKOK PENJUALAN", data.get("cost_of_goods_sold", {}), row)

        # Gross profit
        ws.cell(row=row, column=2, value="LABA KOTOR")
        ws.cell(row=row, column=2).font = section_font
        ws.cell(row=row, column=3, value=data.get("gross_profit", 0))
        ws.cell(row=row, column=3).number_format = currency_format
        ws.cell(row=row, column=3).font = Font(bold=True)
        row += 2

        # Operating expenses
        row = write_section("BIAYA OPERASIONAL", data.get("operating_expenses", {}), row)

        # Net income
        ws.cell(row=row, column=2, value="LABA BERSIH")
        ws.cell(row=row, column=2).font = Font(bold=True, size=12)
        ws.cell(row=row, column=3, value=data.get("net_income", 0))
        ws.cell(row=row, column=3).number_format = currency_format
        ws.cell(row=row, column=3).font = Font(bold=True, size=12)

        # Adjust column widths
        ws.column_dimensions['A'].width = 15
        ws.column_dimensions['B'].width = 40
        ws.column_dimensions['C'].width = 20

        # Save to bytes
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return output.read()


# Singleton instance
_export_service = None


def get_export_service() -> ExportService:
    """Get or create export service singleton."""
    global _export_service
    if _export_service is None:
        _export_service = ExportService()
    return _export_service
```

**Step 4: Run tests to verify they pass**

Run: `cd /root/milkyhoop-dev/backend/api_gateway && python -m pytest tests/test_export_service.py -v`

Expected: All tests PASS

**Step 5: Commit**

```bash
git add backend/api_gateway/app/services/export_service.py backend/api_gateway/tests/test_export_service.py
git commit -m "feat(reports): add export service for CSV and Excel generation"
```

---

## Task 8: Add Export Endpoint

**Files:**
- Modify: `backend/api_gateway/app/routers/reports.py`
- Create: `backend/api_gateway/app/schemas/export.py`
- Test: `backend/api_gateway/tests/test_export_endpoint.py`

**Step 1: Create export schemas**

Create `backend/api_gateway/app/schemas/export.py`:

```python
"""
Export Request/Response Schemas
"""
from typing import Optional, Dict, Any, Literal
from datetime import date
from pydantic import BaseModel


class ExportReportRequest(BaseModel):
    """Request to export a report."""
    report_type: Literal[
        'profit-loss',
        'balance-sheet',
        'cash-flow',
        'ar-aging',
        'ap-aging',
        'trial-balance',
        'drill-down'
    ]
    format: Literal['pdf', 'excel', 'csv']
    parameters: Dict[str, Any]  # Report-specific params


class ExportReportResponse(BaseModel):
    """Response with file download info (if using URL approach)."""
    download_url: str
    filename: str
    expires_at: str
```

**Step 2: Write the failing test**

Create `backend/api_gateway/tests/test_export_endpoint.py`:

```python
"""Tests for export endpoint."""
import pytest


class TestExportEndpoint:
    """Test /reports/export endpoint."""

    def test_export_endpoint_exists(self):
        """Export endpoint is registered."""
        from app.routers.reports import router

        routes = [route.path for route in router.routes]
        assert "/export" in routes

    def test_export_request_schema(self):
        """ExportReportRequest schema validates correctly."""
        from app.schemas.export import ExportReportRequest

        req = ExportReportRequest(
            report_type="profit-loss",
            format="excel",
            parameters={
                "start_date": "2026-01-01",
                "end_date": "2026-01-31",
            }
        )
        assert req.report_type == "profit-loss"
        assert req.format == "excel"

    def test_export_invalid_format_rejected(self):
        """Invalid export format is rejected."""
        from app.schemas.export import ExportReportRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ExportReportRequest(
                report_type="profit-loss",
                format="docx",  # Invalid
                parameters={},
            )
```

**Step 3: Run test to verify it fails**

Run: `cd /root/milkyhoop-dev/backend/api_gateway && python -m pytest tests/test_export_endpoint.py::TestExportEndpoint::test_export_endpoint_exists -v`

Expected: FAIL with `AssertionError`

**Step 4: Add export endpoint**

Add to `backend/api_gateway/app/routers/reports.py`:

```python
from fastapi.responses import StreamingResponse
import io

from ..schemas.export import ExportReportRequest
from ..services.export_service import get_export_service


@router.post("/export")
async def export_report(
    request: Request,
    data: ExportReportRequest,
):
    """
    Export a report as PDF, Excel, or CSV.

    Returns the file directly as a streaming response.
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        export_service = get_export_service()
        params = data.parameters

        # Generate report data based on type
        if data.report_type == "profit-loss":
            # Get P&L data
            start_date_str = params.get("start_date")
            end_date_str = params.get("end_date")

            if not start_date_str or not end_date_str:
                raise HTTPException(status_code=400, detail="start_date and end_date required")

            # Parse dates
            from datetime import datetime
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()

            # Get report data (simplified - in production, call actual report logic)
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)

                # Query revenue
                revenue_rows = await conn.fetch("""
                    SELECT
                        coa.code as account_code,
                        coa.name as account_name,
                        COALESCE(SUM(jl.credit - jl.debit), 0)::BIGINT as amount
                    FROM journal_lines jl
                    JOIN journal_entries je ON je.id = jl.journal_entry_id
                    JOIN chart_of_accounts coa ON coa.id = jl.account_id
                    WHERE je.tenant_id = $1
                    AND je.entry_date BETWEEN $2 AND $3
                    AND je.status = 'POSTED'
                    AND coa.type = 'REVENUE'
                    GROUP BY coa.id, coa.code, coa.name
                    HAVING SUM(jl.credit - jl.debit) != 0
                    ORDER BY coa.code
                """, tenant_id, start_date, end_date)

                revenue_items = [
                    {"account_code": r["account_code"], "account_name": r["account_name"], "amount": r["amount"]}
                    for r in revenue_rows
                ]
                revenue_total = sum(r["amount"] for r in revenue_rows)

                # Query expenses (simplified)
                expense_rows = await conn.fetch("""
                    SELECT
                        coa.code as account_code,
                        coa.name as account_name,
                        COALESCE(SUM(jl.debit - jl.credit), 0)::BIGINT as amount
                    FROM journal_lines jl
                    JOIN journal_entries je ON je.id = jl.journal_entry_id
                    JOIN chart_of_accounts coa ON coa.id = jl.account_id
                    WHERE je.tenant_id = $1
                    AND je.entry_date BETWEEN $2 AND $3
                    AND je.status = 'POSTED'
                    AND coa.type IN ('EXPENSE', 'COGS')
                    GROUP BY coa.id, coa.code, coa.name
                    HAVING SUM(jl.debit - jl.credit) != 0
                    ORDER BY coa.code
                """, tenant_id, start_date, end_date)

                expense_items = [
                    {"account_code": r["account_code"], "account_name": r["account_name"], "amount": r["amount"]}
                    for r in expense_rows
                ]
                expense_total = sum(r["amount"] for r in expense_rows)

            report_data = {
                "period": {
                    "start_date": start_date_str,
                    "end_date": end_date_str,
                    "label": get_period_label(start_date, end_date),
                },
                "revenue": {"label": "Pendapatan", "total": revenue_total, "items": revenue_items},
                "cost_of_goods_sold": {"label": "HPP", "total": 0, "items": []},
                "gross_profit": revenue_total,
                "operating_expenses": {"label": "Biaya Operasional", "total": expense_total, "items": expense_items},
                "operating_income": revenue_total - expense_total,
                "net_income": revenue_total - expense_total,
            }

            # Generate file based on format
            if data.format == "csv":
                content = export_service.generate_profit_loss_csv(report_data)
                media_type = "text/csv"
                filename = f"laba-rugi-{start_date_str}-{end_date_str}.csv"
            elif data.format == "excel":
                content = export_service.generate_profit_loss_excel(report_data)
                media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                filename = f"laba-rugi-{start_date_str}-{end_date_str}.xlsx"
            elif data.format == "pdf":
                # TODO: Implement PDF generation using PDFService
                raise HTTPException(status_code=501, detail="PDF export not yet implemented")
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported format: {data.format}")
        else:
            raise HTTPException(status_code=501, detail=f"Export not implemented for {data.report_type}")

        logger.info(f"Export generated: tenant={tenant_id}, type={data.report_type}, format={data.format}")

        return StreamingResponse(
            io.BytesIO(content),
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Export error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to export report")
```

**Step 5: Run tests to verify they pass**

Run: `cd /root/milkyhoop-dev/backend/api_gateway && python -m pytest tests/test_export_endpoint.py -v`

Expected: All tests PASS

**Step 6: Commit**

```bash
git add backend/api_gateway/app/routers/reports.py backend/api_gateway/app/schemas/export.py backend/api_gateway/tests/test_export_endpoint.py
git commit -m "feat(reports): add export endpoint for CSV and Excel"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Add openpyxl dependency | requirements.txt |
| 2 | Create drill-down schemas | schemas/drill_down.py |
| 3 | Implement drill-down endpoint | routers/reports.py |
| 4 | Create comparison schemas | schemas/report_comparison.py |
| 5 | Add comparison to P&L | routers/reports.py |
| 6 | Add aging brackets to settings | migration + schemas |
| 7 | Create export service | services/export_service.py |
| 8 | Add export endpoint | routers/reports.py |

**Total commits:** 8
**Estimated implementation time:** Each task ~10-15 minutes
