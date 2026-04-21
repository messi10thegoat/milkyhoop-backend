"""Document Intake V3 — Handler Registry.

Phase 1 registers only 3 handlers covering 4 TransferType values.
Unregistered types raise _FallbackToV2PreviewSkip when dispatched.
"""

from __future__ import annotations

from .handlers.bill_payment import BillPaymentHandler
from .handlers.expense import ExpenseHandler
from .handlers.receive_payment import ReceivePaymentHandler
from .transfer_types import TransferType


def build_handler_registry(pool, tenant_id: str) -> dict[TransferType, object]:
    """Construct Phase 1 handler registry.

    Phase 2a-2d will extend this with additional handlers. Do not add
    Phase 2 handlers here until their respective plans are executed.
    """
    return {
        TransferType.RECEIVE_PAYMENT: ReceivePaymentHandler(pool, tenant_id),
        TransferType.BILL_PAYMENT: BillPaymentHandler(pool, tenant_id),
        TransferType.EXPENSE_OPERATIONAL: ExpenseHandler(
            pool, tenant_id, variant="operational"
        ),
        TransferType.BANK_FEE: ExpenseHandler(pool, tenant_id, variant="bank_fee"),
    }
