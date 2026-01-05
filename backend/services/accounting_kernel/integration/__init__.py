"""
Accounting Kernel Integration Layer

Connects existing transaction flows to the Accounting Kernel.
"""
from .transaction_handler import TransactionEventHandler
from .facade import AccountingFacade

__all__ = [
    "TransactionEventHandler",
    "AccountingFacade",
]
