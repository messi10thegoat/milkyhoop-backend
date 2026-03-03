"""
PSAK Financial Statements API — Journal-Derived (Law 1).
Generates Balance Sheet, Income Statement, Cash Flow + auto-validation.
"""
from io import BytesIO
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
from datetime import datetime
import logging
import asyncpg

from ..config import settings
from ..services.pdf_service import get_pdf_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["psak-reports"])


async def get_db_connection():
    db_config = settings.get_db_config()
    return await asyncpg.connect(**db_config)


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": tenant_id}


@router.get("/financial-statements")
async def get_financial_statements(
    request: Request,
    as_of: str = Query(..., description="Tanggal neraca, format YYYY-MM-DD"),
    period_start: str = Query(None, description="Awal periode P&L, default 1 Jan tahun as_of"),
    basis: str = Query("accrual", description="Accounting basis: accrual or cash"),
):
    """
    Generate 3 PSAK financial statements + auto-validation.
    All amounts derive from journal_lines (Law 1).

    Returns:
      - balance_sheet: Neraca per as_of
      - income_statement: Laba Rugi period_start..as_of
      - cash_flow: Arus Kas period_start..as_of (direct method)
      - validation_errors: [] if all 4 checks pass
    """
    from ..services.report_engine.balance_sheet import generate_balance_sheet
    from ..services.report_engine.income_statement import generate_income_statement
    from ..services.report_engine.cash_flow import generate_cash_flow
    from ..services.report_engine.validation import validate_reports

    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]

    if not period_start:
        period_start = as_of[:4] + "-01-01"

    conn = None
    try:
        conn = await get_db_connection()

        # Set RLS context
        await conn.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")

        bs = await generate_balance_sheet(conn, tenant_id, as_of, period_start, basis=basis)
        pl = await generate_income_statement(conn, tenant_id, period_start, as_of, basis=basis)
        cf = await generate_cash_flow(conn, tenant_id, period_start, as_of)
        errors = await validate_reports(bs, pl, cf)

        return {
            "balance_sheet": bs,
            "income_statement": pl,
            "cash_flow": cf,
            "validation_errors": errors,
            "generated_at": datetime.utcnow().isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Financial statements error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


@router.get("/financial-statements/pdf")
async def get_financial_statements_pdf(
    request: Request,
    report: str = Query(..., description="Report type: laba_rugi, neraca, arus_kas"),
    as_of: str = Query(..., description="Balance sheet date / period end (YYYY-MM-DD)"),
    period_start: str = Query(None, description="Period start (YYYY-MM-DD)"),
    company_name: str = Query(None, description="Company name for PDF header"),
    basis: str = Query("accrual", description="Accounting basis: accrual or cash"),
):
    """
    Generate PDF for a single PSAK financial statement.
    Uses WeasyPrint + HTML templates. All amounts from journal_lines (Law 1).
    """
    from ..services.report_engine.balance_sheet import generate_balance_sheet
    from ..services.report_engine.income_statement import generate_income_statement
    from ..services.report_engine.cash_flow import generate_cash_flow

    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]

    if not period_start:
        period_start = as_of[:4] + "-01-01"

    # Use provided company_name or fallback to tenant_id
    if not company_name:
        company_name = tenant_id.replace("-", " ").title()

    conn = None
    try:
        conn = await get_db_connection()
        await conn.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")

        pdf_service = get_pdf_service()

        if report == "laba_rugi":
            data = await generate_income_statement(conn, tenant_id, period_start, as_of, basis=basis)
            basis_label = "Akrual" if basis == "accrual" else "Kas"
            pdf_bytes = pdf_service.generate_income_statement_pdf(data, company_name, basis=basis_label)
            filename = f"Laporan-Laba-Rugi-{period_start}-{as_of}.pdf"
        elif report == "neraca":
            data = await generate_balance_sheet(conn, tenant_id, as_of, period_start, basis=basis)
            basis_label = "Akrual" if basis == "accrual" else "Kas"
            pdf_bytes = pdf_service.generate_balance_sheet_pdf(data, company_name, basis=basis_label)
            filename = f"Laporan-Posisi-Keuangan-{as_of}.pdf"
        elif report == "arus_kas":
            data = await generate_cash_flow(conn, tenant_id, period_start, as_of)
            pdf_bytes = pdf_service.generate_cash_flow_pdf(data, company_name, basis="Kas")
            filename = f"Laporan-Arus-Kas-{period_start}-{as_of}.pdf"
        else:
            raise HTTPException(status_code=400, detail=f"Unknown report type: {report}")

        return StreamingResponse(
            BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PDF generation error for {report}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()
