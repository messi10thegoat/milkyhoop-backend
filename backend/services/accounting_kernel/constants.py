"""
Accounting Kernel Constants
"""
from enum import Enum


class AccountType(str, Enum):
    """Chart of Accounts types"""
    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY = "EQUITY"
    INCOME = "INCOME"
    EXPENSE = "EXPENSE"


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
    AccountType.LIABILITY: NormalBalance.CREDIT,
    AccountType.EQUITY: NormalBalance.CREDIT,
    AccountType.INCOME: NormalBalance.CREDIT,
    AccountType.EXPENSE: NormalBalance.DEBIT,
}


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
