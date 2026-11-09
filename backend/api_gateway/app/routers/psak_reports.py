"""
PSAK Financial Statements API — Journal-Derived (Law 1).
Generates Balance Sheet, Income Statement, Cash Flow + auto-validation.

Gelombang 4 L1 (10 Nov 2026): pool bersama (Law 32; dulu asyncpg.connect per permintaan) + transaksi READ ONLY; SET LOCAL f-string
DIBUANG (dulu di luar transaksi = tak berefek; tiap kueri report_engine menyaring tenant_id eksplisit, Law 24); 500 tanpa str(e) (dulu
membocorkan galat internal); tiap laporan + baris[] tampil server dengan nilai_tampil dari SATU pemformat teks_galat.angka_laporan
(presisi tenant: services/uang_presisi).
"""
from io import BytesIO
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
from datetime import datetime
import logging
import asyncpg

from ..services.pdf_service import get_pdf_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["psak-reports"])


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def _tampil(laporan: dict, presisi: int) -> dict:
    """nilai_tampil per baris (SATU pemformat; gaya teks_galat.GAYA_ANGKA_LAPORAN diputus belakangan)."""
    from ..services import teks_galat as tg
    for b in laporan.get("baris", []):
        b["nilai_tampil"] = None if b["nilai"] is None else tg.angka_laporan(b["nilai"], presisi)
    return laporan


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Silakan masuk terlebih dahulu.")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Sesi tidak valid, silakan masuk lagi.")
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

    from ..services.uang_presisi import presisi_tenant
    try:
        pool = await get_pool()
        async with pool.acquire() as conn, conn.transaction(readonly=True):
            bs = await generate_balance_sheet(conn, tenant_id, as_of, period_start, basis=basis)
            pl = await generate_income_statement(conn, tenant_id, period_start, as_of, basis=basis)
            cf = await generate_cash_flow(conn, tenant_id, period_start, as_of)
            errors = await validate_reports(bs, pl, cf)
            presisi = await presisi_tenant(conn, tenant_id)

        return {
            "balance_sheet": _tampil(bs, presisi),
            "income_statement": _tampil(pl, presisi),
            "cash_flow": _tampil(cf, presisi),
            "validation_errors": errors,
            "generated_at": datetime.utcnow().isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Financial statements error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal menyusun laporan keuangan.")


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

    if report not in ("laba_rugi", "neraca", "arus_kas"):
        raise HTTPException(status_code=400, detail=f"Unknown report type: {report}")
    try:
        from ..utils.content_disposition import pdf_content_disposition, sanitize_filename
        pool = await get_pool()
        async with pool.acquire() as conn, conn.transaction(readonly=True):
            # Nama perusahaan di laporan resmi SELALU dari Tenant.display_name saat render;
            # param query `company_name` DIABAIKAN — nama resmi tak boleh diubah lewat URL.
            _dn = await conn.fetchval('SELECT display_name FROM "Tenant" WHERE id = $1', tenant_id)
            if report == "laba_rugi":
                data = await generate_income_statement(conn, tenant_id, period_start, as_of, basis=basis)
            elif report == "neraca":
                data = await generate_balance_sheet(conn, tenant_id, as_of, period_start, basis=basis)
            else:
                data = await generate_cash_flow(conn, tenant_id, period_start, as_of)
        company_name = _dn or tenant_id.replace("-", " ").title()
        pdf_service = get_pdf_service()  # render DI LUAR koneksi (tak menahan koneksi pool selama WeasyPrint)
        basis_label = "Akrual" if basis == "accrual" else "Kas"
        if report == "laba_rugi":
            pdf_bytes = pdf_service.generate_income_statement_pdf(data, company_name, basis=basis_label)
            report_base = sanitize_filename(f"Laba-Rugi_{company_name}_{period_start}-{as_of}")
        elif report == "neraca":
            pdf_bytes = pdf_service.generate_balance_sheet_pdf(data, company_name, basis=basis_label)
            report_base = sanitize_filename(f"Posisi-Keuangan_{company_name}_{as_of}")
        else:
            pdf_bytes = pdf_service.generate_cash_flow_pdf(data, company_name, basis="Kas")
            report_base = sanitize_filename(f"Arus-Kas_{company_name}_{period_start}-{as_of}")

        return StreamingResponse(
            BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": pdf_content_disposition(report_base)},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PDF generation error for {report}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal membuat PDF laporan.")
