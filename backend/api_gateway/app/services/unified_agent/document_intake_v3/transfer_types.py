"""Document Intake V3 — TransferType enum and display names.

Contract between Stage 1 (Classifier) and Stage 2 (Dispatcher).
No strings cross stage boundaries — always TransferType enum values.
"""

from __future__ import annotations

from enum import Enum


class TransferType(str, Enum):
    # AR/AP payments
    RECEIVE_PAYMENT = "receive_payment"
    BILL_PAYMENT = "bill_payment"

    # Deposits
    CUSTOMER_DEPOSIT = "customer_deposit"
    VENDOR_DEPOSIT = "vendor_deposit"

    # Refunds (Phase 1: deposit case only; Phase 2 adds credit-note settlement)
    CUSTOMER_REFUND = "customer_refund"
    VENDOR_REFUND = "vendor_refund"

    # Expense-family
    EXPENSE_OPERATIONAL = "expense_operational"
    BANK_FEE = "bank_fee"

    # Payroll
    PAYROLL = "payroll"

    # Liability payments
    TAX_PAYMENT = "tax_payment"
    BPJS_PAYMENT = "bpjs_payment"
    LOAN_PAYMENT = "loan_payment"

    # Equity
    OWNER_DRAWING = "owner_drawing"
    OWNER_CAPITAL = "owner_capital"

    # Internal (no P&L)
    INTERNAL_TRANSFER = "internal_transfer"

    # Special states
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"


TYPE_DISPLAY_NAMES: dict[TransferType, str] = {
    TransferType.RECEIVE_PAYMENT: "Pembayaran dari Pelanggan",
    TransferType.BILL_PAYMENT: "Pembayaran ke Vendor",
    TransferType.CUSTOMER_DEPOSIT: "Uang Muka dari Pelanggan",
    TransferType.VENDOR_DEPOSIT: "Uang Muka ke Vendor",
    TransferType.CUSTOMER_REFUND: "Refund ke Pelanggan",
    TransferType.VENDOR_REFUND: "Refund dari Vendor",
    TransferType.EXPENSE_OPERATIONAL: "Beban Operasional",
    TransferType.BANK_FEE: "Biaya Admin Bank",
    TransferType.PAYROLL: "Gaji Karyawan",
    TransferType.TAX_PAYMENT: "Pembayaran Pajak",
    TransferType.BPJS_PAYMENT: "Pembayaran BPJS",
    TransferType.LOAN_PAYMENT: "Cicilan Pinjaman",
    TransferType.OWNER_DRAWING: "Prive / Owner Drawing",
    TransferType.OWNER_CAPITAL: "Setor Modal",
    TransferType.INTERNAL_TRANSFER: "Transfer Antar Rekening",
}


# Semantically compatible type pairs — not flagged as conflict during ambiguity detection.
# Example: a customer payment could reasonably be either RECEIVE_PAYMENT or CUSTOMER_DEPOSIT
# depending on whether an invoice exists; classifier should not force AMBIGUOUS for these.
COMPATIBLE_PAIRS: frozenset[frozenset[TransferType]] = frozenset(
    {
        frozenset({TransferType.RECEIVE_PAYMENT, TransferType.CUSTOMER_DEPOSIT}),
        frozenset({TransferType.BILL_PAYMENT, TransferType.VENDOR_DEPOSIT}),
        frozenset({TransferType.EXPENSE_OPERATIONAL, TransferType.BANK_FEE}),
    }
)
