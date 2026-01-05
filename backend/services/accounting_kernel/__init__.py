"""
MilkyHoop Accounting Kernel
============================

QuickBooks-like accounting engine with:
- Double-entry bookkeeping
- General Ledger management
- Accounts Receivable (AR) / Accounts Payable (AP)
- Financial reporting (P&L, Balance Sheet, Cash Flow)

Core Principles:
- Append-only journal entries (no edit/delete)
- Source-traceable (every journal linked to source document)
- Idempotent posting (trace_id for exactly-once)
- All reports derive from ledger (not source tables)

Usage:
    from accounting_kernel import AccountingFacade

    facade = AccountingFacade(pool)
    await facade.record_sale(tenant_id, transaction_id, amount, "tunai")
    report = await facade.get_profit_loss(tenant_id, start_date, end_date)
"""

__version__ = "1.0.0"
__author__ = "MilkyHoop Team"

# Services
from .services import (
    CoAService,
    JournalService,
    LedgerService,
    ARService,
    APService,
    AutoPostingService,
    create_auto_posting_service,
)

# Models
from .models import (
    Account,
    AccountCreate,
    AccountUpdate,
    JournalEntry,
    JournalLine,
    JournalLineInput,
    CreateJournalRequest,
    JournalResponse,
    AccountReceivable,
    ARPaymentApplication,
    AccountPayable,
    APPaymentApplication,
    FiscalPeriod,
)

# Constants
from .constants import (
    AccountType,
    NormalBalance,
    JournalStatus,
    SourceType,
    ARAPStatus,
    AgingBucket,
    EventType,
)

# Reports
from .reports import (
    ProfitLossReport,
    ProfitLossGenerator,
    BalanceSheetReport,
    BalanceSheetGenerator,
    CashFlowReport,
    CashFlowGenerator,
    GeneralLedgerReport,
    GeneralLedgerGenerator,
)

# Integration (main entry points)
from .integration import (
    TransactionEventHandler,
    AccountingFacade,
)

# Validators
from .validators import DoubleEntryValidator

__all__ = [
    # Version
    "__version__",

    # Services
    "CoAService",
    "JournalService",
    "LedgerService",
    "ARService",
    "APService",
    "AutoPostingService",
    "create_auto_posting_service",

    # Models
    "Account",
    "AccountCreate",
    "AccountUpdate",
    "JournalEntry",
    "JournalLine",
    "JournalLineInput",
    "CreateJournalRequest",
    "JournalResponse",
    "AccountReceivable",
    "ARPaymentApplication",
    "AccountPayable",
    "APPaymentApplication",
    "FiscalPeriod",

    # Constants
    "AccountType",
    "NormalBalance",
    "JournalStatus",
    "SourceType",
    "ARAPStatus",
    "AgingBucket",
    "EventType",

    # Reports
    "ProfitLossReport",
    "ProfitLossGenerator",
    "BalanceSheetReport",
    "BalanceSheetGenerator",
    "CashFlowReport",
    "CashFlowGenerator",
    "GeneralLedgerReport",
    "GeneralLedgerGenerator",

    # Integration
    "TransactionEventHandler",
    "AccountingFacade",

    # Validators
    "DoubleEntryValidator",
]
