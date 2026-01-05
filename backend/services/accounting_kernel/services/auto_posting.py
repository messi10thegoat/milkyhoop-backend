"""
Auto-Posting Rules Service
==========================

Automatically creates journal entries from business transactions.
Implements the "QuickBooks-like" behavior where every transaction
automatically posts to the General Ledger.

Rules:
1. POS Cash Sale → Debit Cash, Credit Sales, Credit Inventory, Debit COGS
2. POS Credit Sale → Debit AR, Credit Sales, Credit Inventory, Debit COGS
3. Purchase Cash → Debit Inventory, Credit Cash
4. Purchase Credit → Debit Inventory, Credit AP
5. Invoice → Debit AR, Credit Sales
6. Bill → Debit Expense/Inventory, Credit AP
7. Payment Received → Debit Cash/Bank, Credit AR
8. Payment Made → Debit AP, Credit Cash/Bank
"""
from datetime import date
from decimal import Decimal
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from uuid import UUID
from enum import Enum

import asyncpg

from ..constants import SourceType, AccountType
from ..models.journal import CreateJournalRequest, JournalLineInput
from ..config import settings


class TransactionType(str, Enum):
    """Types of business transactions that trigger auto-posting"""
    POS_CASH_SALE = "POS_CASH_SALE"
    POS_CREDIT_SALE = "POS_CREDIT_SALE"
    PURCHASE_CASH = "PURCHASE_CASH"
    PURCHASE_CREDIT = "PURCHASE_CREDIT"
    INVOICE = "INVOICE"
    BILL = "BILL"
    PAYMENT_RECEIVED = "PAYMENT_RECEIVED"
    PAYMENT_MADE = "PAYMENT_MADE"
    EXPENSE = "EXPENSE"
    TRANSFER = "TRANSFER"
    ADJUSTMENT = "ADJUSTMENT"


@dataclass
class SaleLineItem:
    """Line item for a sale transaction"""
    product_name: str
    quantity: Decimal
    unit_price: Decimal
    total: Decimal
    cost: Optional[Decimal] = None  # For COGS calculation


@dataclass
class PurchaseLineItem:
    """Line item for a purchase transaction"""
    product_name: str
    quantity: Decimal
    unit_price: Decimal
    total: Decimal


@dataclass
class AutoPostResult:
    """Result of auto-posting operation"""
    success: bool
    journal_id: Optional[UUID] = None
    journal_number: Optional[str] = None
    error: Optional[str] = None
    lines_created: int = 0


class AutoPostingService:
    """
    Auto-Posting Service.

    Automatically creates journal entries from business transactions.
    Ensures every financial transaction is properly recorded in the GL.
    """

    def __init__(self, pool: asyncpg.Pool, journal_service=None):
        self.pool = pool
        self.journal_service = journal_service
        self.account_config = settings.accounting

    async def post_pos_sale(
        self,
        tenant_id: str,
        transaction_date: date,
        transaction_id: UUID,
        total_amount: Decimal,
        payment_method: str,
        customer_name: Optional[str] = None,
        items: Optional[List[SaleLineItem]] = None,
        description: Optional[str] = None
    ) -> AutoPostResult:
        """
        Post a POS sale transaction.

        Cash Sale:
            Debit: Cash/Bank
            Credit: Sales Revenue
            (If perpetual inventory: Debit COGS, Credit Inventory)

        Credit Sale:
            Debit: Accounts Receivable
            Credit: Sales Revenue
            (If perpetual inventory: Debit COGS, Credit Inventory)
        """
        is_credit = payment_method.lower() in ['kredit', 'credit', 'piutang']

        # Determine accounts
        if is_credit:
            debit_account = self.account_config.AR_ACCOUNT
            source_type = SourceType.INVOICE
        else:
            debit_account = self._resolve_payment_account(payment_method)
            source_type = SourceType.POS

        lines = []

        # Revenue entry
        lines.append(JournalLineInput(
            account_code=debit_account,
            debit=total_amount,
            credit=Decimal("0"),
            memo=f"Penjualan - {customer_name or 'Customer'}"
        ))

        lines.append(JournalLineInput(
            account_code=self.account_config.SALES_REVENUE_ACCOUNT,
            debit=Decimal("0"),
            credit=total_amount,
            memo=description or "Penjualan POS"
        ))

        # COGS entries if items have cost data (perpetual inventory)
        if items:
            total_cogs = Decimal("0")
            for item in items:
                if item.cost and item.cost > 0:
                    total_cogs += item.cost * item.quantity

            if total_cogs > 0:
                lines.append(JournalLineInput(
                    account_code=self.account_config.COGS_ACCOUNT,
                    debit=total_cogs,
                    credit=Decimal("0"),
                    memo="HPP - Penjualan"
                ))
                lines.append(JournalLineInput(
                    account_code=self.account_config.INVENTORY_ACCOUNT,
                    debit=Decimal("0"),
                    credit=total_cogs,
                    memo="Pengurangan persediaan"
                ))

        # Create journal
        request = CreateJournalRequest(
            tenant_id=tenant_id,
            journal_date=transaction_date,
            description=description or f"Penjualan POS - {customer_name or 'Customer'}",
            source_type=source_type,
            source_id=transaction_id,
            trace_id=str(transaction_id),  # String for idempotency
            lines=lines
        )

        return await self._create_journal(request)

    async def post_purchase(
        self,
        tenant_id: str,
        transaction_date: date,
        transaction_id: UUID,
        total_amount: Decimal,
        payment_method: str,
        supplier_name: str,
        items: Optional[List[PurchaseLineItem]] = None,
        description: Optional[str] = None,
        is_inventory: bool = True
    ) -> AutoPostResult:
        """
        Post a purchase transaction (kulakan/pembelian).

        Cash Purchase:
            Debit: Inventory (or Purchase Expense)
            Credit: Cash/Bank

        Credit Purchase:
            Debit: Inventory (or Purchase Expense)
            Credit: Accounts Payable
        """
        is_credit = payment_method.lower() in ['kredit', 'credit', 'hutang', 'tempo']

        # Determine accounts
        if is_inventory:
            debit_account = self.account_config.INVENTORY_ACCOUNT
        else:
            debit_account = self.account_config.PURCHASE_ACCOUNT

        if is_credit:
            credit_account = self.account_config.AP_ACCOUNT
            source_type = SourceType.BILL
        else:
            credit_account = self._resolve_payment_account(payment_method)
            source_type = SourceType.POS

        lines = [
            JournalLineInput(
                account_code=debit_account,
                debit=total_amount,
                credit=Decimal("0"),
                memo=f"Pembelian dari {supplier_name}"
            ),
            JournalLineInput(
                account_code=credit_account,
                debit=Decimal("0"),
                credit=total_amount,
                memo=description or f"Pembayaran ke {supplier_name}"
            )
        ]

        request = CreateJournalRequest(
            tenant_id=tenant_id,
            journal_date=transaction_date,
            description=description or f"Pembelian dari {supplier_name}",
            source_type=source_type,
            source_id=transaction_id,
            trace_id=str(transaction_id),  # String for idempotency
            lines=lines
        )

        return await self._create_journal(request)

    async def post_invoice(
        self,
        tenant_id: str,
        invoice_date: date,
        invoice_id: UUID,
        customer_name: str,
        total_amount: Decimal,
        items: Optional[List[SaleLineItem]] = None,
        description: Optional[str] = None
    ) -> AutoPostResult:
        """
        Post an invoice (create AR and recognize revenue).

        Debit: Accounts Receivable
        Credit: Sales Revenue
        """
        lines = [
            JournalLineInput(
                account_code=self.account_config.AR_ACCOUNT,
                debit=total_amount,
                credit=Decimal("0"),
                memo=f"Piutang - {customer_name}"
            ),
            JournalLineInput(
                account_code=self.account_config.SALES_REVENUE_ACCOUNT,
                debit=Decimal("0"),
                credit=total_amount,
                memo=description or f"Penjualan ke {customer_name}"
            )
        ]

        # COGS if items have cost
        if items:
            total_cogs = sum(
                (item.cost or Decimal("0")) * item.quantity
                for item in items
            )
            if total_cogs > 0:
                lines.extend([
                    JournalLineInput(
                        account_code=self.account_config.COGS_ACCOUNT,
                        debit=total_cogs,
                        credit=Decimal("0"),
                        memo="HPP"
                    ),
                    JournalLineInput(
                        account_code=self.account_config.INVENTORY_ACCOUNT,
                        debit=Decimal("0"),
                        credit=total_cogs,
                        memo="Pengurangan persediaan"
                    )
                ])

        request = CreateJournalRequest(
            tenant_id=tenant_id,
            journal_date=invoice_date,
            description=description or f"Invoice - {customer_name}",
            source_type=SourceType.INVOICE.value,
            source_id=invoice_id,
            trace_id=str(invoice_id),  # String for idempotency
            lines=lines
        )

        return await self._create_journal(request)

    async def post_bill(
        self,
        tenant_id: str,
        bill_date: date,
        bill_id: UUID,
        supplier_name: str,
        total_amount: Decimal,
        expense_account: Optional[str] = None,
        description: Optional[str] = None
    ) -> AutoPostResult:
        """
        Post a bill (create AP and recognize expense/inventory).

        Debit: Expense or Inventory
        Credit: Accounts Payable
        """
        debit_account = expense_account or self.account_config.INVENTORY_ACCOUNT

        lines = [
            JournalLineInput(
                account_code=debit_account,
                debit=total_amount,
                credit=Decimal("0"),
                memo=f"Pembelian dari {supplier_name}"
            ),
            JournalLineInput(
                account_code=self.account_config.AP_ACCOUNT,
                debit=Decimal("0"),
                credit=total_amount,
                memo=f"Hutang ke {supplier_name}"
            )
        ]

        request = CreateJournalRequest(
            tenant_id=tenant_id,
            journal_date=bill_date,
            description=description or f"Bill dari {supplier_name}",
            source_type=SourceType.BILL.value,
            source_id=bill_id,
            trace_id=str(bill_id),  # String for idempotency
            lines=lines
        )

        return await self._create_journal(request)

    async def post_payment_received(
        self,
        tenant_id: str,
        payment_date: date,
        payment_id: UUID,
        customer_name: str,
        amount: Decimal,
        payment_method: str,
        invoice_number: Optional[str] = None,
        description: Optional[str] = None
    ) -> AutoPostResult:
        """
        Post a payment received from customer.

        Debit: Cash/Bank
        Credit: Accounts Receivable
        """
        debit_account = self._resolve_payment_account(payment_method)

        lines = [
            JournalLineInput(
                account_code=debit_account,
                debit=amount,
                credit=Decimal("0"),
                memo=f"Pembayaran dari {customer_name}"
            ),
            JournalLineInput(
                account_code=self.account_config.AR_ACCOUNT,
                debit=Decimal("0"),
                credit=amount,
                memo=f"Pelunasan {invoice_number or 'piutang'}"
            )
        ]

        request = CreateJournalRequest(
            tenant_id=tenant_id,
            journal_date=payment_date,
            description=description or f"Pembayaran dari {customer_name}",
            source_type=SourceType.PAYMENT_RECEIVED.value,
            source_id=payment_id,
            trace_id=str(payment_id),  # String for idempotency
            lines=lines
        )

        return await self._create_journal(request)

    async def post_payment_made(
        self,
        tenant_id: str,
        payment_date: date,
        payment_id: UUID,
        supplier_name: str,
        amount: Decimal,
        payment_method: str,
        bill_number: Optional[str] = None,
        description: Optional[str] = None
    ) -> AutoPostResult:
        """
        Post a payment made to supplier.

        Debit: Accounts Payable
        Credit: Cash/Bank
        """
        credit_account = self._resolve_payment_account(payment_method)

        lines = [
            JournalLineInput(
                account_code=self.account_config.AP_ACCOUNT,
                debit=amount,
                credit=Decimal("0"),
                memo=f"Pembayaran hutang {bill_number or ''}"
            ),
            JournalLineInput(
                account_code=credit_account,
                debit=Decimal("0"),
                credit=amount,
                memo=f"Pembayaran ke {supplier_name}"
            )
        ]

        request = CreateJournalRequest(
            tenant_id=tenant_id,
            journal_date=payment_date,
            description=description or f"Pembayaran ke {supplier_name}",
            source_type=SourceType.PAYMENT_BILL.value,
            source_id=payment_id,
            trace_id=str(payment_id),  # String for idempotency
            lines=lines
        )

        return await self._create_journal(request)

    async def post_expense(
        self,
        tenant_id: str,
        expense_date: date,
        expense_id: UUID,
        expense_account: str,
        amount: Decimal,
        payment_method: str,
        description: str,
        vendor_name: Optional[str] = None
    ) -> AutoPostResult:
        """
        Post a direct expense.

        Debit: Expense Account
        Credit: Cash/Bank or AP (if credit)
        """
        is_credit = payment_method.lower() in ['kredit', 'credit', 'hutang']

        if is_credit:
            credit_account = self.account_config.AP_ACCOUNT
        else:
            credit_account = self._resolve_payment_account(payment_method)

        lines = [
            JournalLineInput(
                account_code=expense_account,
                debit=amount,
                credit=Decimal("0"),
                memo=description
            ),
            JournalLineInput(
                account_code=credit_account,
                debit=Decimal("0"),
                credit=amount,
                memo=f"Pembayaran - {vendor_name or 'Biaya'}"
            )
        ]

        request = CreateJournalRequest(
            tenant_id=tenant_id,
            journal_date=expense_date,
            description=description,
            source_type=SourceType.MANUAL.value,
            source_id=expense_id,
            trace_id=str(expense_id),  # String for idempotency
            lines=lines
        )

        return await self._create_journal(request)

    async def post_bank_transfer(
        self,
        tenant_id: str,
        transfer_date: date,
        transfer_id: UUID,
        from_account: str,
        to_account: str,
        amount: Decimal,
        description: Optional[str] = None
    ) -> AutoPostResult:
        """
        Post a bank-to-bank or cash-to-bank transfer.

        Debit: Destination Account
        Credit: Source Account
        """
        lines = [
            JournalLineInput(
                account_code=to_account,
                debit=amount,
                credit=Decimal("0"),
                memo=description or "Transfer masuk"
            ),
            JournalLineInput(
                account_code=from_account,
                debit=Decimal("0"),
                credit=amount,
                memo=description or "Transfer keluar"
            )
        ]

        request = CreateJournalRequest(
            tenant_id=tenant_id,
            journal_date=transfer_date,
            description=description or "Transfer antar rekening",
            source_type=SourceType.MANUAL.value,
            source_id=transfer_id,
            trace_id=str(transfer_id),  # String for idempotency
            lines=lines
        )

        return await self._create_journal(request)

    async def post_inventory_adjustment(
        self,
        tenant_id: str,
        adjustment_date: date,
        adjustment_id: UUID,
        amount: Decimal,
        adjustment_type: str,  # 'increase' or 'decrease'
        reason: str
    ) -> AutoPostResult:
        """
        Post an inventory adjustment.

        Increase:
            Debit: Inventory
            Credit: Inventory Adjustment (Income/Equity)

        Decrease:
            Debit: Inventory Adjustment (Expense)
            Credit: Inventory
        """
        if adjustment_type.lower() == 'increase':
            lines = [
                JournalLineInput(
                    account_code=self.account_config.INVENTORY_ACCOUNT,
                    debit=amount,
                    credit=Decimal("0"),
                    memo=reason
                ),
                JournalLineInput(
                    account_code="4-90000",  # Other Income - Inventory Adjustment
                    debit=Decimal("0"),
                    credit=amount,
                    memo="Penyesuaian persediaan (tambah)"
                )
            ]
        else:  # decrease
            lines = [
                JournalLineInput(
                    account_code="5-90000",  # Other Expense - Inventory Adjustment
                    debit=amount,
                    credit=Decimal("0"),
                    memo="Penyesuaian persediaan (kurang)"
                ),
                JournalLineInput(
                    account_code=self.account_config.INVENTORY_ACCOUNT,
                    debit=Decimal("0"),
                    credit=amount,
                    memo=reason
                )
            ]

        request = CreateJournalRequest(
            tenant_id=tenant_id,
            journal_date=adjustment_date,
            description=f"Penyesuaian Persediaan: {reason}",
            source_type=SourceType.ADJUSTMENT.value,
            source_id=adjustment_id,
            trace_id=str(adjustment_id),  # String for idempotency
            lines=lines
        )

        return await self._create_journal(request)

    def _resolve_payment_account(self, payment_method: str) -> str:
        """Resolve payment method to account code."""
        method_lower = payment_method.lower()
        method_upper = payment_method.upper()

        # Check configured mappings first (uses uppercase keys)
        method_mapping = self.account_config.PAYMENT_ACCOUNT_MAPPING
        if method_upper in method_mapping:
            return method_mapping[method_upper]

        # Default mappings
        if method_lower in ['cash', 'tunai', 'kas']:
            return self.account_config.CASH_ACCOUNT
        elif method_lower in ['bank', 'transfer', 'bca', 'mandiri', 'bni', 'bri']:
            return self.account_config.BANK_ACCOUNT
        elif method_lower in ['qris', 'gopay', 'ovo', 'dana', 'shopeepay']:
            return self.account_config.BANK_ACCOUNT  # E-wallet to bank
        else:
            # Default to cash
            return self.account_config.CASH_ACCOUNT

    async def _create_journal(
        self,
        request: CreateJournalRequest
    ) -> AutoPostResult:
        """Create journal entry using JournalService."""
        if not self.journal_service:
            return AutoPostResult(
                success=False,
                error="JournalService not configured"
            )

        try:
            response = await self.journal_service.create_journal(request)

            # Check if journal creation succeeded
            if not response.success:
                return AutoPostResult(
                    success=False,
                    error=response.message or "; ".join(response.errors)
                )

            return AutoPostResult(
                success=response.success,
                journal_id=response.journal_id,
                journal_number=response.journal_number,
                lines_created=len(request.lines)
            )
        except Exception as e:
            return AutoPostResult(
                success=False,
                error=str(e)
            )


# Convenience function for creating the service with all dependencies
async def create_auto_posting_service(pool: asyncpg.Pool) -> AutoPostingService:
    """
    Factory function to create AutoPostingService with JournalService.
    """
    from .journal_service import JournalService
    from .coa_service import CoAService

    coa_service = CoAService(pool)
    journal_service = JournalService(pool, coa_service)

    return AutoPostingService(pool, journal_service)
