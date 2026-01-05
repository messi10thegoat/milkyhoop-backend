"""
Transaction Event Handler
=========================

Handles transaction events from existing services and auto-posts
journal entries to the Accounting Kernel.

This is the bridge between the existing transaction flow and the
new accounting engine.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, Any, Optional
from uuid import UUID
import logging

import asyncpg

from ..services import JournalService, CoAService, ARService, APService
from ..services.auto_posting import AutoPostingService, SaleLineItem, PurchaseLineItem
from ..constants import SourceType

logger = logging.getLogger(__name__)


class TransactionEventHandler:
    """
    Handles transaction events and posts them to the Accounting Kernel.

    Listens to events from:
    - transaction_service (POS sales, purchases)
    - invoice_service (invoices)
    - bill_service (bills)
    - payment_service (payments)

    And creates corresponding journal entries in the General Ledger.
    """

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
        self._services_initialized = False
        self._coa_service: Optional[CoAService] = None
        self._journal_service: Optional[JournalService] = None
        self._ar_service: Optional[ARService] = None
        self._ap_service: Optional[APService] = None
        self._auto_posting: Optional[AutoPostingService] = None

    async def _ensure_services(self):
        """Lazily initialize services."""
        if not self._services_initialized:
            self._coa_service = CoAService(self.pool)
            self._journal_service = JournalService(self.pool, self._coa_service)
            self._ar_service = ARService(self.pool, self._journal_service)
            self._ap_service = APService(self.pool, self._journal_service)
            self._auto_posting = AutoPostingService(self.pool, self._journal_service)
            self._services_initialized = True

    async def handle_event(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main event handler dispatcher.

        Args:
            event_type: Type of event (e.g., 'transaction.sale.completed')
            payload: Event payload data

        Returns:
            Result of handling the event
        """
        await self._ensure_services()

        handlers = {
            # Sales events
            "transaction.sale.completed": self._handle_sale,
            "transaction.pos.completed": self._handle_sale,

            # Purchase events
            "transaction.purchase.completed": self._handle_purchase,
            "transaction.kulakan.completed": self._handle_purchase,

            # Invoice events
            "invoice.created": self._handle_invoice_created,
            "invoice.paid": self._handle_payment_received,

            # Bill events
            "bill.created": self._handle_bill_created,
            "bill.paid": self._handle_payment_made,

            # Payment events
            "payment.received": self._handle_payment_received,
            "payment.made": self._handle_payment_made,

            # Inventory events
            "inventory.adjusted": self._handle_inventory_adjustment,

            # Expense events
            "expense.recorded": self._handle_expense,
        }

        handler = handlers.get(event_type)
        if handler:
            try:
                result = await handler(payload)
                logger.info(f"Successfully handled event {event_type}: {result}")
                return {"success": True, "result": result}
            except Exception as e:
                logger.error(f"Error handling event {event_type}: {e}")
                return {"success": False, "error": str(e)}
        else:
            logger.warning(f"Unknown event type: {event_type}")
            return {"success": False, "error": f"Unknown event type: {event_type}"}

    async def _handle_sale(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle a sale transaction.

        Payload expected:
        - tenant_id: str
        - transaction_id: UUID
        - transaction_date: date or str
        - total_amount: Decimal or float
        - payment_method: str
        - customer_name: str (optional)
        - items: List of items with product_name, quantity, unit_price, total, cost
        """
        tenant_id = UUID(payload['tenant_id'])
        transaction_id = UUID(payload['transaction_id'])
        transaction_date = self._parse_date(payload.get('transaction_date'))
        total_amount = Decimal(str(payload['total_amount']))
        payment_method = payload.get('payment_method', 'tunai')
        customer_name = payload.get('customer_name', 'Customer')
        description = payload.get('description')

        # Parse items if provided
        items = None
        if 'items' in payload:
            items = [
                SaleLineItem(
                    product_name=item.get('product_name', ''),
                    quantity=Decimal(str(item.get('quantity', 1))),
                    unit_price=Decimal(str(item.get('unit_price', 0))),
                    total=Decimal(str(item.get('total', 0))),
                    cost=Decimal(str(item.get('cost', 0))) if item.get('cost') else None
                )
                for item in payload['items']
            ]

        result = await self._auto_posting.post_pos_sale(
            tenant_id=tenant_id,
            transaction_date=transaction_date,
            transaction_id=transaction_id,
            total_amount=total_amount,
            payment_method=payment_method,
            customer_name=customer_name,
            items=items,
            description=description
        )

        return {
            "journal_id": str(result.journal_id) if result.journal_id else None,
            "journal_number": result.journal_number,
            "success": result.success,
            "error": result.error
        }

    async def _handle_purchase(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle a purchase transaction (kulakan/pembelian).

        Payload expected:
        - tenant_id: str
        - transaction_id: UUID
        - transaction_date: date or str
        - total_amount: Decimal or float
        - payment_method: str
        - supplier_name: str
        - items: List of items (optional)
        - is_inventory: bool (default True)
        """
        tenant_id = UUID(payload['tenant_id'])
        transaction_id = UUID(payload['transaction_id'])
        transaction_date = self._parse_date(payload.get('transaction_date'))
        total_amount = Decimal(str(payload['total_amount']))
        payment_method = payload.get('payment_method', 'tunai')
        supplier_name = payload.get('supplier_name', 'Supplier')
        is_inventory = payload.get('is_inventory', True)
        description = payload.get('description')

        # Parse items if provided
        items = None
        if 'items' in payload:
            items = [
                PurchaseLineItem(
                    product_name=item.get('product_name', ''),
                    quantity=Decimal(str(item.get('quantity', 1))),
                    unit_price=Decimal(str(item.get('unit_price', 0))),
                    total=Decimal(str(item.get('total', 0)))
                )
                for item in payload['items']
            ]

        result = await self._auto_posting.post_purchase(
            tenant_id=tenant_id,
            transaction_date=transaction_date,
            transaction_id=transaction_id,
            total_amount=total_amount,
            payment_method=payment_method,
            supplier_name=supplier_name,
            items=items,
            description=description,
            is_inventory=is_inventory
        )

        # Create AP record if credit purchase
        if payment_method.lower() in ['kredit', 'credit', 'hutang', 'tempo']:
            due_date = self._parse_date(
                payload.get('due_date'),
                default_days=30
            )

            await self._ap_service.create_payable(
                tenant_id=tenant_id,
                supplier_id=payload.get('supplier_id'),
                supplier_name=supplier_name,
                bill_number=payload.get('bill_number', f"PUR-{transaction_id}"),
                bill_date=transaction_date,
                due_date=due_date,
                amount=total_amount,
                description=description or f"Pembelian dari {supplier_name}",
                source_type=SourceType.BILL,
                source_id=transaction_id
            )

        return {
            "journal_id": str(result.journal_id) if result.journal_id else None,
            "journal_number": result.journal_number,
            "success": result.success,
            "error": result.error
        }

    async def _handle_invoice_created(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle invoice creation - creates AR and revenue journal."""
        tenant_id = UUID(payload['tenant_id'])
        invoice_id = UUID(payload['invoice_id'])
        invoice_date = self._parse_date(payload.get('invoice_date'))
        customer_name = payload['customer_name']
        total_amount = Decimal(str(payload['total_amount']))

        # Create AR record
        due_date = self._parse_date(payload.get('due_date'), default_days=30)

        await self._ar_service.create_receivable(
            tenant_id=tenant_id,
            customer_id=payload.get('customer_id'),
            customer_name=customer_name,
            invoice_number=payload.get('invoice_number', f"INV-{invoice_id}"),
            invoice_date=invoice_date,
            due_date=due_date,
            amount=total_amount,
            description=payload.get('description', f"Invoice to {customer_name}"),
            source_type=SourceType.INVOICE,
            source_id=invoice_id
        )

        # Post journal entry
        result = await self._auto_posting.post_invoice(
            tenant_id=tenant_id,
            invoice_date=invoice_date,
            invoice_id=invoice_id,
            customer_name=customer_name,
            total_amount=total_amount,
            description=payload.get('description')
        )

        return {
            "journal_id": str(result.journal_id) if result.journal_id else None,
            "journal_number": result.journal_number,
            "success": result.success
        }

    async def _handle_bill_created(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle bill creation - creates AP and expense/inventory journal."""
        tenant_id = UUID(payload['tenant_id'])
        bill_id = UUID(payload['bill_id'])
        bill_date = self._parse_date(payload.get('bill_date'))
        supplier_name = payload['supplier_name']
        total_amount = Decimal(str(payload['total_amount']))
        expense_account = payload.get('expense_account')

        # Create AP record
        due_date = self._parse_date(payload.get('due_date'), default_days=30)

        await self._ap_service.create_payable(
            tenant_id=tenant_id,
            supplier_id=payload.get('supplier_id'),
            supplier_name=supplier_name,
            bill_number=payload.get('bill_number', f"BILL-{bill_id}"),
            bill_date=bill_date,
            due_date=due_date,
            amount=total_amount,
            description=payload.get('description', f"Bill from {supplier_name}"),
            source_type=SourceType.BILL,
            source_id=bill_id
        )

        # Post journal entry
        result = await self._auto_posting.post_bill(
            tenant_id=tenant_id,
            bill_date=bill_date,
            bill_id=bill_id,
            supplier_name=supplier_name,
            total_amount=total_amount,
            expense_account=expense_account,
            description=payload.get('description')
        )

        return {
            "journal_id": str(result.journal_id) if result.journal_id else None,
            "journal_number": result.journal_number,
            "success": result.success
        }

    async def _handle_payment_received(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle payment received from customer."""
        tenant_id = UUID(payload['tenant_id'])
        payment_id = UUID(payload['payment_id'])
        payment_date = self._parse_date(payload.get('payment_date'))
        amount = Decimal(str(payload['amount']))
        payment_method = payload.get('payment_method', 'tunai')
        customer_name = payload.get('customer_name', 'Customer')

        # If AR ID provided, apply payment to AR
        if 'ar_id' in payload:
            ar_id = UUID(payload['ar_id'])
            await self._ar_service.apply_payment(
                tenant_id=tenant_id,
                ar_id=ar_id,
                payment_date=payment_date,
                amount=amount,
                payment_method=payment_method,
                reference_number=payload.get('reference_number'),
                create_journal=True
            )
            return {"success": True, "applied_to_ar": str(ar_id)}

        # Otherwise, just post the payment journal
        result = await self._auto_posting.post_payment_received(
            tenant_id=tenant_id,
            payment_date=payment_date,
            payment_id=payment_id,
            customer_name=customer_name,
            amount=amount,
            payment_method=payment_method,
            invoice_number=payload.get('invoice_number'),
            description=payload.get('description')
        )

        return {
            "journal_id": str(result.journal_id) if result.journal_id else None,
            "journal_number": result.journal_number,
            "success": result.success
        }

    async def _handle_payment_made(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle payment made to supplier."""
        tenant_id = UUID(payload['tenant_id'])
        payment_id = UUID(payload['payment_id'])
        payment_date = self._parse_date(payload.get('payment_date'))
        amount = Decimal(str(payload['amount']))
        payment_method = payload.get('payment_method', 'tunai')
        supplier_name = payload.get('supplier_name', 'Supplier')

        # If AP ID provided, apply payment to AP
        if 'ap_id' in payload:
            ap_id = UUID(payload['ap_id'])
            await self._ap_service.apply_payment(
                tenant_id=tenant_id,
                ap_id=ap_id,
                payment_date=payment_date,
                amount=amount,
                payment_method=payment_method,
                reference_number=payload.get('reference_number'),
                create_journal=True
            )
            return {"success": True, "applied_to_ap": str(ap_id)}

        # Otherwise, just post the payment journal
        result = await self._auto_posting.post_payment_made(
            tenant_id=tenant_id,
            payment_date=payment_date,
            payment_id=payment_id,
            supplier_name=supplier_name,
            amount=amount,
            payment_method=payment_method,
            bill_number=payload.get('bill_number'),
            description=payload.get('description')
        )

        return {
            "journal_id": str(result.journal_id) if result.journal_id else None,
            "journal_number": result.journal_number,
            "success": result.success
        }

    async def _handle_inventory_adjustment(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle inventory adjustment."""
        tenant_id = UUID(payload['tenant_id'])
        adjustment_id = UUID(payload['adjustment_id'])
        adjustment_date = self._parse_date(payload.get('adjustment_date'))
        amount = Decimal(str(payload['amount']))
        adjustment_type = payload.get('adjustment_type', 'decrease')
        reason = payload.get('reason', 'Inventory adjustment')

        result = await self._auto_posting.post_inventory_adjustment(
            tenant_id=tenant_id,
            adjustment_date=adjustment_date,
            adjustment_id=adjustment_id,
            amount=amount,
            adjustment_type=adjustment_type,
            reason=reason
        )

        return {
            "journal_id": str(result.journal_id) if result.journal_id else None,
            "journal_number": result.journal_number,
            "success": result.success
        }

    async def _handle_expense(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle direct expense recording."""
        tenant_id = UUID(payload['tenant_id'])
        expense_id = UUID(payload['expense_id'])
        expense_date = self._parse_date(payload.get('expense_date'))
        expense_account = payload['expense_account']
        amount = Decimal(str(payload['amount']))
        payment_method = payload.get('payment_method', 'tunai')
        description = payload['description']
        vendor_name = payload.get('vendor_name')

        result = await self._auto_posting.post_expense(
            tenant_id=tenant_id,
            expense_date=expense_date,
            expense_id=expense_id,
            expense_account=expense_account,
            amount=amount,
            payment_method=payment_method,
            description=description,
            vendor_name=vendor_name
        )

        return {
            "journal_id": str(result.journal_id) if result.journal_id else None,
            "journal_number": result.journal_number,
            "success": result.success
        }

    def _parse_date(
        self,
        date_value: Any,
        default_days: int = 0
    ) -> date:
        """Parse date from various formats."""
        if date_value is None:
            if default_days > 0:
                from datetime import timedelta
                return date.today() + timedelta(days=default_days)
            return date.today()

        if isinstance(date_value, date):
            return date_value

        if isinstance(date_value, datetime):
            return date_value.date()

        if isinstance(date_value, str):
            # Try ISO format first
            try:
                return datetime.fromisoformat(date_value.replace('Z', '+00:00')).date()
            except ValueError:
                pass

            # Try common formats
            for fmt in ['%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y']:
                try:
                    return datetime.strptime(date_value, fmt).date()
                except ValueError:
                    continue

        # Default to today
        return date.today()
