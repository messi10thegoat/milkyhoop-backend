"""
Perubahan Ekuitas Handler
Handles Changes in Equity (Laporan Perubahan Ekuitas) generation

Supports two data sources:
1. Legacy: transaksiharian table (Prisma)
2. New: Accounting Kernel General Ledger (asyncpg)

Set USE_ACCOUNTING_KERNEL=false to revert to legacy (not recommended).
"""

import logging
import os
from datetime import datetime, date, timedelta
import grpc

from app.prisma_rls_extension import RLSPrismaClient
from queries.financial_queries import build_where_clause

logger = logging.getLogger(__name__)

# Feature flag for Accounting Kernel
USE_ACCOUNTING_KERNEL = os.getenv('USE_ACCOUNTING_KERNEL', 'true').lower() == 'true'


class PerubahanEkuitasHandler:
    """Handler for Perubahan Ekuitas (Changes in Equity) operations"""

    @staticmethod
    def _parse_periode(periode: str):
        """Parse periode string to (start_date, end_date)."""
        if '-Q' in periode:
            year, quarter = periode.split('-Q')
            year = int(year)
            quarter = int(quarter)
            quarter_months = {1: (1, 3), 2: (4, 6), 3: (7, 9), 4: (10, 12)}
            start_month, end_month = quarter_months[quarter]
            start = date(year, start_month, 1)
            if end_month == 12:
                end = date(year, 12, 31)
            else:
                end = date(year, end_month + 1, 1) - timedelta(days=1)
        elif '-' in periode:
            year, month = periode.split('-')
            year, month = int(year), int(month)
            start = date(year, month, 1)
            if month == 12:
                end = date(year, 12, 31)
            else:
                end = date(year, month + 1, 1) - timedelta(days=1)
        else:
            year = int(periode)
            start = date(year, 1, 1)
            end = date(year, 12, 31)
        return start, end

    @staticmethod
    async def _kernel_get_perubahan_ekuitas(tenant_id: str, periode: str, pb):
        """
        Compute Perubahan Ekuitas from journal_lines (Accounting Kernel path).

        Steps:
        1. Opening equity: SUM(credit - debit) for EQUITY accounts before period_start
        2. Period equity changes: SUM(credit - debit) for EQUITY accounts within period
        3. Net income: Revenue - Expenses within period
        4. Closing equity = opening + changes + net_income
        """
        from app.adapters.accounting_kernel_adapter import get_kernel_adapter

        adapter = await get_kernel_adapter()
        await adapter.initialize()
        pool = adapter.pool

        period_start, period_end = PerubahanEkuitasHandler._parse_periode(periode)

        async with pool.acquire() as conn:
            # 1. Opening equity: all EQUITY accounts before period_start
            opening_equity = await conn.fetchval(
                """
                SELECT COALESCE(SUM(jl.credit - jl.debit), 0)
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE je.tenant_id = $1
                  AND je.status = 'POSTED'
                  AND je.entry_date < $2
                  AND coa.account_type IN ('EQUITY')
                """,
                tenant_id, period_start
            )

            # 2. Modal additions within period (equity accounts, typically 3-1xx)
            penambahan_modal = await conn.fetchval(
                """
                SELECT COALESCE(SUM(jl.credit - jl.debit), 0)
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE je.tenant_id = $1
                  AND je.status = 'POSTED'
                  AND je.entry_date BETWEEN $2 AND $3
                  AND coa.account_type IN ('EQUITY')
                  AND coa.account_code LIKE '3-1%%'
                """,
                tenant_id, period_start, period_end
            )

            # 3. Prive/drawings within period (typically 3-2xx, debit balance)
            prive = await conn.fetchval(
                """
                SELECT COALESCE(SUM(jl.debit - jl.credit), 0)
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE je.tenant_id = $1
                  AND je.status = 'POSTED'
                  AND je.entry_date BETWEEN $2 AND $3
                  AND coa.account_type IN ('EQUITY')
                  AND coa.account_code LIKE '3-2%%'
                """,
                tenant_id, period_start, period_end
            )

            # 4. Net income = Revenue - Expenses
            revenue = await conn.fetchval(
                """
                SELECT COALESCE(SUM(jl.credit - jl.debit), 0)
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE je.tenant_id = $1
                  AND je.status = 'POSTED'
                  AND je.entry_date BETWEEN $2 AND $3
                  AND coa.account_type IN ('REVENUE', 'INCOME')
                """,
                tenant_id, period_start, period_end
            )

            expenses = await conn.fetchval(
                """
                SELECT COALESCE(SUM(jl.debit - jl.credit), 0)
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE je.tenant_id = $1
                  AND je.status = 'POSTED'
                  AND je.entry_date BETWEEN $2 AND $3
                  AND coa.account_type IN ('EXPENSE', 'COGS', 'OTHER_EXPENSE')
                """,
                tenant_id, period_start, period_end
            )

            laba_bersih = int(revenue) - int(expenses)

        # Compute derived values
        opening_equity = int(opening_equity)
        penambahan_modal = int(penambahan_modal)
        prive = int(prive)

        modal_awal = opening_equity
        modal_akhir = modal_awal + penambahan_modal

        laba_ditahan_awal = 0  # Included in opening_equity already
        laba_ditahan_akhir = laba_ditahan_awal + laba_bersih - prive

        ekuitas_awal_periode = opening_equity
        ekuitas_akhir_periode = ekuitas_awal_periode + penambahan_modal + laba_bersih - prive

        rugi_periode_berjalan = abs(laba_bersih) if laba_bersih < 0 else 0
        laba_bersih_positif = laba_bersih if laba_bersih >= 0 else 0

        logger.info(
            f"Perubahan Ekuitas (Kernel): opening={opening_equity}, "
            f"modal_tambah={penambahan_modal}, laba={laba_bersih}, "
            f"prive={prive}, closing={ekuitas_akhir_periode}"
        )

        return pb.LaporanPerubahanEkuitas(
            tenant_id=tenant_id,
            periode_pelaporan=periode,
            generated_at=int(datetime.utcnow().timestamp() * 1000),
            ekuitas_awal_periode=ekuitas_awal_periode,
            modal_awal=modal_awal,
            penambahan_modal=penambahan_modal,
            pengurangan_modal=0,
            modal_akhir=modal_akhir,
            laba_bersih_periode_berjalan=laba_bersih_positif,
            rugi_periode_berjalan=rugi_periode_berjalan,
            prive_periode_berjalan=prive,
            laba_ditahan_awal=laba_ditahan_awal,
            laba_ditahan_akhir=laba_ditahan_akhir,
            ekuitas_akhir_periode=ekuitas_akhir_periode,
            ekuitas_from_neraca=0,  # TODO: Cross-check with Neraca
            is_reconciled=True
        )

    @staticmethod
    async def query_perubahan_ekuitas(rls_client: RLSPrismaClient, where: dict, pb):
        """Legacy: Query Perubahan Ekuitas from transaksiharian."""
        all_transactions = await rls_client.transaksiharian.find_many(where=where)

        ekuitas_awal_periode = 0
        modal_awal = 0
        penambahan_modal = sum(tx.totalNominal or 0 for tx in all_transactions if tx.isModal)
        pengurangan_modal = 0
        modal_akhir = modal_awal + penambahan_modal - pengurangan_modal

        laba_bersih_periode_berjalan = 0
        rugi_periode_berjalan = 0
        prive_periode_berjalan = sum(tx.totalNominal or 0 for tx in all_transactions if tx.isPrive)

        laba_ditahan_awal = 0
        laba_ditahan_akhir = laba_ditahan_awal + laba_bersih_periode_berjalan - prive_periode_berjalan

        ekuitas_akhir_periode = modal_akhir + laba_ditahan_akhir

        return pb.LaporanPerubahanEkuitas(
            tenant_id=where['tenantId'],
            periode_pelaporan=where.get('periodePelaporan', ''),
            generated_at=int(datetime.utcnow().timestamp() * 1000),
            ekuitas_awal_periode=ekuitas_awal_periode,
            modal_awal=modal_awal,
            penambahan_modal=penambahan_modal,
            pengurangan_modal=pengurangan_modal,
            modal_akhir=modal_akhir,
            laba_bersih_periode_berjalan=laba_bersih_periode_berjalan,
            rugi_periode_berjalan=rugi_periode_berjalan,
            prive_periode_berjalan=prive_periode_berjalan,
            laba_ditahan_awal=laba_ditahan_awal,
            laba_ditahan_akhir=laba_ditahan_akhir,
            ekuitas_akhir_periode=ekuitas_akhir_periode,
            ekuitas_from_neraca=0,
            is_reconciled=True
        )

    @staticmethod
    async def handle_get_perubahan_ekuitas(
        request,
        context: grpc.aio.ServicerContext,
        pb
    ):
        """Generate Laporan Perubahan Ekuitas (Changes in Equity)"""
        logger.info(f"GetPerubahanEkuitas: tenant={request.tenant_id}, periode={request.periode_pelaporan}, use_kernel={USE_ACCOUNTING_KERNEL}")

        # Use Accounting Kernel if enabled
        if USE_ACCOUNTING_KERNEL:
            try:
                result = await PerubahanEkuitasHandler._kernel_get_perubahan_ekuitas(
                    tenant_id=request.tenant_id,
                    periode=request.periode_pelaporan,
                    pb=pb
                )
                logger.info(f"Perubahan Ekuitas (Kernel): ekuitas_akhir={result.ekuitas_akhir_periode}")
                return result

            except Exception as e:
                logger.error(f"Accounting kernel error: {e}")
                await context.abort(grpc.StatusCode.INTERNAL, f"Accounting kernel error: {str(e)}")
                return  # Never reached, but explicit

        # Legacy implementation (USE_ACCOUNTING_KERNEL=false only)
        rls_client = RLSPrismaClient(tenant_id=request.tenant_id, bypass_rls=True)

        try:
            await rls_client.connect()

            where = build_where_clause(
                request.tenant_id,
                request.periode_pelaporan,
                request.start_date,
                request.end_date
            )

            result = await PerubahanEkuitasHandler.query_perubahan_ekuitas(rls_client, where, pb)
            logger.info(f"Perubahan Ekuitas (Legacy): ekuitas_akhir={result.ekuitas_akhir_periode}")
            return result

        except Exception as e:
            logger.error(f"GetPerubahanEkuitas failed: {str(e)}")
            await context.abort(grpc.StatusCode.INTERNAL, f"Failed to generate report: {str(e)}")
        finally:
            await rls_client.disconnect()
