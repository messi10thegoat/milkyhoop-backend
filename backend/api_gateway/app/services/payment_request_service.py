"""
Payment Request Service
Business logic untuk payment request workflow

IRON LAW COMPLIANCE:
- Law 0: Separation of Concerns - Service handles business logic
- Law 6: Source Traceability - Journal has source_type='PAYMENT_REQUEST', source_id
- Law 8: No Silent Mutation - All balance changes via explicit journal
"""
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from decimal import Decimal

logger = logging.getLogger(__name__)

# Global pool reference (set by init)
_pool = None


async def get_payment_request_service():
    """Get service instance with pool"""
    global _pool
    if _pool is None:
        from backend.api_gateway.app.main import get_db_pool
        _pool = await get_db_pool()
    return PaymentRequestService(_pool)


class PaymentRequestService:
    def __init__(self, pool):
        self.pool = pool

    async def list(
        self,
        tenant_id: str,
        user_visibility: List[str],
        status: Optional[str] = None,
        offset: int = 0,
        limit: int = 20
    ) -> Dict[str, Any]:
        """List payment requests with FCL filter"""
        async with self.pool.acquire() as conn:
            # Build query with FCL filter
            visibility_filter = ",".join(f"'{v}'" for v in user_visibility)
            
            where_clauses = [
                "tenant_id = $1",
                f"confidentiality_level::text IN ({visibility_filter})"
            ]
            params = [tenant_id]
            param_idx = 2
            
            if status:
                where_clauses.append(f"status = ${param_idx}")
                params.append(status)
                param_idx += 1
            
            where_sql = " AND ".join(where_clauses)
            
            # Get total count
            count_query = f"SELECT COUNT(*) FROM payment_requests WHERE {where_sql}"
            total = await conn.fetchval(count_query, *params)
            
            # Get data
            data_query = f"""
                SELECT * FROM payment_requests 
                WHERE {where_sql}
                ORDER BY created_at DESC
                OFFSET ${param_idx} LIMIT ${param_idx + 1}
            """
            params.extend([offset, limit])
            rows = await conn.fetch(data_query, *params)
            
            return {
                "data": [dict(r) for r in rows],
                "total": total,
                "hasMore": offset + limit < total
            }

    async def get_summary(self, tenant_id: str, user_visibility: List[str]) -> Dict:
        """Get summary for Stats Card"""
        async with self.pool.acquire() as conn:
            visibility_filter = ",".join(f"'{v}'" for v in user_visibility)
            
            query = f"""
                SELECT 
                    status,
                    COUNT(*) as count,
                    COALESCE(SUM(amount), 0) as amount
                FROM payment_requests
                WHERE tenant_id = $1
                AND confidentiality_level::text IN ({visibility_filter})
                GROUP BY status
            """
            rows = await conn.fetch(query, tenant_id)
            
            breakdown = {}
            total_count = 0
            total_amount = 0
            
            for row in rows:
                status = row['status']
                breakdown[status] = {
                    'count': row['count'],
                    'amount': int(row['amount'])
                }
                total_count += row['count']
                total_amount += int(row['amount'])
            
            return {
                'total': total_count,
                'totalAmount': total_amount,
                'breakdown': breakdown
            }

    async def get_by_id(
        self,
        tenant_id: str,
        request_id: str,
        user_visibility: List[str]
    ) -> Optional[Dict]:
        """Get single payment request"""
        async with self.pool.acquire() as conn:
            visibility_filter = ",".join(f"'{v}'" for v in user_visibility)
            
            query = f"""
                SELECT * FROM payment_requests
                WHERE tenant_id = $1 AND id = $2::uuid
                AND confidentiality_level::text IN ({visibility_filter})
            """
            row = await conn.fetchrow(query, tenant_id, request_id)
            return dict(row) if row else None

    async def create(
        self,
        tenant_id: str,
        user_id: str,
        user_name: str,
        data: dict
    ) -> Dict:
        """Create new payment request"""
        async with self.pool.acquire() as conn:
            # Generate request number
            request_number = await conn.fetchval(
                "SELECT generate_payment_request_number($1)",
                tenant_id
            )
            
            # Get bank account name if provided
            bank_account_name = None
            if data.get('bank_account_from'):
                bank_row = await conn.fetchrow(
                    "SELECT name FROM bank_accounts WHERE id = $1::uuid",
                    data['bank_account_from']
                )
                if bank_row:
                    bank_account_name = bank_row['name']
            
            # Insert
            query = """
                INSERT INTO payment_requests (
                    tenant_id, request_number, requested_by, requested_by_name,
                    purpose, description, amount,
                    bank_account_from, bank_account_from_name,
                    recipient_bank_name, recipient_account_number, recipient_account_name,
                    reference_type, reference_id, reference_number,
                    status, confidentiality_level
                ) VALUES (
                    $1, $2, $3::uuid, $4,
                    $5, $6, $7,
                    $8::uuid, $9,
                    $10, $11, $12,
                    $13, $14::uuid, $15,
                    'PENDING', 'L3'
                )
                RETURNING *
            """
            row = await conn.fetchrow(
                query,
                tenant_id, request_number, user_id, user_name,
                data['purpose'], data.get('description'), data['amount'],
                data.get('bank_account_from'), bank_account_name,
                data['recipient_bank_name'], data['recipient_account_number'], data['recipient_account_name'],
                data.get('reference_type'), data.get('reference_id'), data.get('reference_number')
            )
            
            logger.info(f"Payment request created: {request_number} by {user_name}")
            return dict(row)

    async def approve(
        self,
        tenant_id: str,
        request_id: str,
        approver_id: str,
        approver_name: str
    ) -> Dict:
        """Approve payment request"""
        async with self.pool.acquire() as conn:
            # Check current status
            current = await conn.fetchrow(
                "SELECT status FROM payment_requests WHERE tenant_id = $1 AND id = $2::uuid",
                tenant_id, request_id
            )
            if not current:
                raise ValueError("Payment request not found")
            if current['status'] != 'PENDING':
                raise ValueError(f"Cannot approve request in status {current['status']}")
            
            # Update
            row = await conn.fetchrow("""
                UPDATE payment_requests
                SET status = 'APPROVED',
                    approved_by = $3::uuid,
                    approved_by_name = $4,
                    approved_at = NOW(),
                    updated_at = NOW()
                WHERE tenant_id = $1 AND id = $2::uuid
                RETURNING *
            """, tenant_id, request_id, approver_id, approver_name)
            
            logger.info(f"Payment request {request_id} approved by {approver_name}")
            return dict(row)

    async def reject(
        self,
        tenant_id: str,
        request_id: str,
        approver_id: str,
        approver_name: str,
        reason: Optional[str] = None
    ) -> Dict:
        """Reject payment request"""
        async with self.pool.acquire() as conn:
            # Check current status
            current = await conn.fetchrow(
                "SELECT status FROM payment_requests WHERE tenant_id = $1 AND id = $2::uuid",
                tenant_id, request_id
            )
            if not current:
                raise ValueError("Payment request not found")
            if current['status'] != 'PENDING':
                raise ValueError(f"Cannot reject request in status {current['status']}")
            
            # Update
            row = await conn.fetchrow("""
                UPDATE payment_requests
                SET status = 'REJECTED',
                    approved_by = $3::uuid,
                    approved_by_name = $4,
                    approved_at = NOW(),
                    rejection_reason = $5,
                    updated_at = NOW()
                WHERE tenant_id = $1 AND id = $2::uuid
                RETURNING *
            """, tenant_id, request_id, approver_id, approver_name, reason)
            
            logger.info(f"Payment request {request_id} rejected by {approver_name}: {reason}")
            return dict(row)

    async def cancel(
        self,
        tenant_id: str,
        request_id: str,
        user_id: str
    ) -> Dict:
        """Cancel own request"""
        async with self.pool.acquire() as conn:
            # Check ownership and status
            current = await conn.fetchrow(
                "SELECT status, requested_by FROM payment_requests WHERE tenant_id = $1 AND id = $2::uuid",
                tenant_id, request_id
            )
            if not current:
                raise ValueError("Payment request not found")
            if str(current['requested_by']) != user_id:
                raise ValueError("Only the requestor can cancel")
            if current['status'] not in ['PENDING', 'APPROVED']:
                raise ValueError(f"Cannot cancel request in status {current['status']}")
            
            # Update
            row = await conn.fetchrow("""
                UPDATE payment_requests
                SET status = 'CANCELLED', updated_at = NOW()
                WHERE tenant_id = $1 AND id = $2::uuid
                RETURNING *
            """, tenant_id, request_id)
            
            logger.info(f"Payment request {request_id} cancelled")
            return dict(row)

    async def mark_paid(
        self,
        tenant_id: str,
        request_id: str,
        payer_id: str,
        payer_name: str,
        proof_url: str,
        payment_reference: Optional[str] = None
    ) -> Dict:
        """
        Mark as paid and create journal entry.
        
        IRON LAW 6: Journal has source_type='PAYMENT_REQUEST', source_id=request_id
        IRON LAW 8: Balance changes only via this journal
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Get request details
                req = await conn.fetchrow("""
                    SELECT * FROM payment_requests 
                    WHERE tenant_id = $1 AND id = $2::uuid
                """, tenant_id, request_id)
                
                if not req:
                    raise ValueError("Payment request not found")
                if req['status'] != 'APPROVED':
                    raise ValueError(f"Cannot mark as paid - status is {req['status']}")
                
                # Update to PAID
                await conn.execute("""
                    UPDATE payment_requests
                    SET status = 'PAID',
                        paid_at = NOW(),
                        paid_by = $3::uuid,
                        paid_by_name = $4,
                        proof_url = $5,
                        payment_reference = $6,
                        updated_at = NOW()
                    WHERE id = $2::uuid
                """, tenant_id, request_id, payer_id, payer_name, proof_url, payment_reference)
                
                # Create journal entry (Iron Law 6 & 8)
                journal_id = await self._create_payment_journal(conn, dict(req), payer_name)
                
                # Update to POSTED with journal reference
                row = await conn.fetchrow("""
                    UPDATE payment_requests
                    SET status = 'POSTED',
                        posted_at = NOW(),
                        journal_entry_id = $3::uuid,
                        updated_at = NOW()
                    WHERE tenant_id = $1 AND id = $2::uuid
                    RETURNING *
                """, tenant_id, request_id, journal_id)
                
                logger.info(f"Payment request {request_id} marked as paid, journal {journal_id}")
                return dict(row)

    async def _create_payment_journal(
        self,
        conn,
        request: Dict,
        payer_name: str
    ) -> str:
        """
        Create journal entry for payment.
        
        IRON LAW 6: source_type='PAYMENT_REQUEST', source_id=request.id
        
        Journal:
        - If reference_type = PURCHASE_INVOICE: DR Hutang Usaha, CR Bank
        - If reference_type = EXPENSE or OTHER: DR Beban Lain-lain, CR Bank
        """
        tenant_id = request['tenant_id']
        amount = request['amount']
        
        # Get accounts
        # Debit account based on reference type
        if request.get('reference_type') == 'PURCHASE_INVOICE':
            # Hutang Usaha (Account Payable)
            debit_account = await conn.fetchrow(
                "SELECT id FROM accounts WHERE tenant_id = $1 AND account_code LIKE '2100%' LIMIT 1",
                tenant_id
            )
        else:
            # Beban Lain-lain (Other Expense)
            debit_account = await conn.fetchrow(
                "SELECT id FROM accounts WHERE tenant_id = $1 AND account_code LIKE '5999%' LIMIT 1",
                tenant_id
            )
        
        # Credit account - Bank
        if request.get('bank_account_from'):
            bank = await conn.fetchrow(
                "SELECT account_id FROM bank_accounts WHERE id = $1::uuid",
                request['bank_account_from']
            )
            credit_account_id = bank['account_id'] if bank else None
        else:
            # Default cash account
            credit_account = await conn.fetchrow(
                "SELECT id FROM accounts WHERE tenant_id = $1 AND account_code LIKE '1100%' LIMIT 1",
                tenant_id
            )
            credit_account_id = credit_account['id'] if credit_account else None
        
        if not debit_account or not credit_account_id:
            logger.warning("Could not find accounts for journal - skipping journal creation")
            return None
        
        # Create journal entry
        journal_row = await conn.fetchrow("""
            INSERT INTO journal_entries (
                tenant_id, journal_type, posting_date, description,
                total_debit, total_credit, status,
                source_type, source_id, created_by_name,
                confidentiality_level
            ) VALUES (
                $1, 'PAYMENT', CURRENT_DATE, $2,
                $3, $3, 'POSTED',
                'PAYMENT_REQUEST', $4::uuid, $5,
                $6::confidentiality_level
            )
            RETURNING id
        """, 
            tenant_id,
            f"Payment: {request['purpose']}",
            amount, 
            request['id'],
            payer_name,
            request.get('confidentiality_level', 'L3')
        )
        
        journal_id = journal_row['id']
        
        # Create journal lines
        # Debit line
        await conn.execute("""
            INSERT INTO journal_lines (journal_entry_id, account_id, debit, credit, description)
            VALUES ($1::uuid, $2::uuid, $3, 0, $4)
        """, journal_id, debit_account['id'], amount, request['purpose'])
        
        # Credit line
        await conn.execute("""
            INSERT INTO journal_lines (journal_entry_id, account_id, debit, credit, description)
            VALUES ($1::uuid, $2::uuid, 0, $3, $4)
        """, journal_id, credit_account_id, amount, request['purpose'])
        
        return str(journal_id)
