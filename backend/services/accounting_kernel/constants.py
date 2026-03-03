"""
Accounting Kernel Constants
"""
from enum import Enum


class AccountType(str, Enum):
    """Chart of Accounts types"""
    ASSET = "ASSET"
    RECEIVABLE = "RECEIVABLE"      # subtipe ASSET (debit normal)
    LIABILITY = "LIABILITY"
    PAYABLE = "PAYABLE"            # subtipe LIABILITY (credit normal)
    EQUITY = "EQUITY"
    REVENUE = "REVENUE"
    COGS = "COGS"
    EXPENSE = "EXPENSE"
    OTHER_INCOME = "OTHER_INCOME"
    OTHER_EXPENSE = "OTHER_EXPENSE"


class NormalBalance(str, Enum):
    """Account normal balance"""
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class JournalStatus(str, Enum):
    """Journal entry status"""
    DRAFT = "DRAFT"
    POSTED = "POSTED"
    VOID = "VOID"


class SourceType(str, Enum):
    """Journal source types"""
    INVOICE = "INVOICE"
    BILL = "BILL"
    PAYMENT_RECEIVED = "PAYMENT_RECEIVED"
    PAYMENT_BILL = "PAYMENT_BILL"
    POS = "POS"
    ADJUSTMENT = "ADJUSTMENT"
    MANUAL = "MANUAL"
    CLOSING = "CLOSING"
    OPENING = "OPENING"


class ARAPStatus(str, Enum):
    """AR/AP status"""
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    PAID = "PAID"
    VOID = "VOID"


class PeriodStatus(str, Enum):
    """Fiscal period status

    OPEN:   Normal operation, all posting allowed
    CLOSED: Soft close, only system reversals allowed
    LOCKED: Immutable, audit-ready
    """
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    LOCKED = "LOCKED"


class AgingBucket(str, Enum):
    """Aging report buckets"""
    CURRENT = "CURRENT"
    DAYS_1_30 = "1-30"
    DAYS_31_60 = "31-60"
    DAYS_61_90 = "61-90"
    DAYS_OVER_90 = "90+"


# Account type to normal balance mapping
ACCOUNT_TYPE_NORMAL_BALANCE = {
    AccountType.ASSET: NormalBalance.DEBIT,
    AccountType.RECEIVABLE: NormalBalance.DEBIT,
    AccountType.LIABILITY: NormalBalance.CREDIT,
    AccountType.PAYABLE: NormalBalance.CREDIT,
    AccountType.EQUITY: NormalBalance.CREDIT,
    AccountType.REVENUE: NormalBalance.CREDIT,
    AccountType.COGS: NormalBalance.DEBIT,
    AccountType.EXPENSE: NormalBalance.DEBIT,
    AccountType.OTHER_INCOME: NormalBalance.CREDIT,
    AccountType.OTHER_EXPENSE: NormalBalance.DEBIT,
}

# =============================================================================
# TYPE GROUP CONSTANTS — Single source of truth for account grouping
# All report/balance sheet/grouping logic MUST use these, not scattered if-else
# =============================================================================

BALANCE_SHEET_ASSET_TYPES = {'ASSET', 'RECEIVABLE'}
BALANCE_SHEET_LIABILITY_TYPES = {'LIABILITY', 'PAYABLE'}
INCOME_STATEMENT_REVENUE_TYPES = {'REVENUE', 'OTHER_INCOME'}
INCOME_STATEMENT_EXPENSE_TYPES = {'EXPENSE', 'COGS', 'OTHER_EXPENSE'}

DEBIT_NORMAL_TYPES = BALANCE_SHEET_ASSET_TYPES | INCOME_STATEMENT_EXPENSE_TYPES
CREDIT_NORMAL_TYPES = BALANCE_SHEET_LIABILITY_TYPES | {'EQUITY'} | INCOME_STATEMENT_REVENUE_TYPES


# Contra accounts (opposite normal balance)
CONTRA_ACCOUNTS = {
    "1-20900": NormalBalance.CREDIT,   # Accumulated Depreciation (contra-asset)
    "4-10200": NormalBalance.DEBIT,    # Sales Discount (contra-revenue)
    "4-10300": NormalBalance.DEBIT,    # Sales Returns (contra-revenue)
    "5-10200": NormalBalance.CREDIT,   # Purchase Discount (contra-expense)
    "5-10300": NormalBalance.CREDIT,   # Purchase Returns (contra-expense)
    "3-40000": NormalBalance.DEBIT,    # Prive/Drawings (contra-equity)
}


# Event types for outbox/Kafka
class EventType(str, Enum):
    JOURNAL_POSTED = "accounting.journal.posted"
    JOURNAL_VOIDED = "accounting.journal.voided"
    JOURNAL_REVERSED = "accounting.journal.reversed"  # First-class reversal
    AR_CREATED = "accounting.ar.created"
    AR_PAID = "accounting.ar.paid"
    AP_CREATED = "accounting.ap.created"
    AP_PAID = "accounting.ap.paid"
    PERIOD_CLOSED = "accounting.period.closed"
    PERIOD_LOCKED = "accounting.period.locked"
    PERIOD_UNLOCKED = "accounting.period.unlocked"
    BALANCE_UPDATED = "accounting.balance.updated"