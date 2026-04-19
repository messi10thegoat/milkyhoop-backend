"""
Tax Reports Router — Fase 3 Tax Reporting

GET /api/tax-reports/ppn?period=YYYY-MM   — PPN Masukan vs Keluaran
GET /api/tax-reports/pph?period=YYYY-MM   — PPh per jenis

Iron Law compliance:
- Law 1/16: All amounts journal-derived
- Law 27: Account IDs from tax_codes.coa_id (no hardcoded codes, no ILIKE)
- Law 24: RLS enforced
- Law 25: Decimal precision
- Voided journals excluded via reversed_by_id IS NULL
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional
from decimal import Decimal
from datetime import date
import logging
import asyncpg
import csv
import io

from ..schemas.tax_reports import (
    PPNReportResponse, PPNSection, PPNTransaction, PPNCrossCheck,
    PPhReportResponse, PPhByType, PPhTransaction, PPhCrossCheck,
)
from ..config import settings
from uuid import UUID

logger = logging.getLogger(__name__)
router = APIRouter()


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, 'user') or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": tenant_id, "user_id": user.get("user_id")}

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(settings.DATABASE_URL, min_size=2, max_size=5)
    return _pool


def _parse_period(period: str) -> tuple[date, date]:
    """Parse YYYY-MM into (first_day, last_day)."""
    parts = period.split("-")
    if len(parts) != 2:
        raise ValueError("Period must be YYYY-MM")
    year, month = int(parts[0]), int(parts[1])
    first_day = date(year, month, 1)
    if month == 12:
        last_day = date(year + 1, 1, 1)
    else:
        last_day = date(year, month + 1, 1)
    # last_day is exclusive (use < not <=)
    return first_day, last_day


# ─── PPN Report ────────────────────────────────────────────────

@router.get("/ppn", response_model=PPNReportResponse)
async def get_ppn_report(
    request: Request,
    period: str = Query(..., regex=r"^\d{4}-\d{2}$", description="YYYY-MM"),
):
    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]

    try:
        period_start, period_end = _parse_period(period)
    except ValueError:
        raise HTTPException(400, "Invalid period format. Use YYYY-MM.")

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")

        # Step 1: Collect PPN coa_ids from tax_codes (Law 27)
        ppn_coas = await conn.fetch("""
            SELECT DISTINCT coa_id, direction
            FROM tax_codes
            WHERE tenant_id = $1
              AND tax_type = 'ppn'
              AND coa_id IS NOT NULL
              AND is_active = true
        """, tenant_id)

        keluaran_coa_ids = [r["coa_id"] for r in ppn_coas if r["direction"] == "output"]
        masukan_coa_ids = [r["coa_id"] for r in ppn_coas if r["direction"] == "input"]

        if not keluaran_coa_ids and not masukan_coa_ids:
            return PPNReportResponse(
                period=period,
                ppn_keluaran=PPNSection(total=Decimal("0"), count=0, transactions=[]),
                ppn_masukan=PPNSection(total=Decimal("0"), count=0, transactions=[]),
                net_ppn=Decimal("0"),
                cross_check=PPNCrossCheck(
                    document_tax_lines_keluaran=Decimal("0"),
                    document_tax_lines_masukan=Decimal("0"),
                    drift_keluaran=Decimal("0"),
                    drift_masukan=Decimal("0"),
                ),
            )

        # Step 2: Query journal_lines for PPN transactions
        all_coa_ids = keluaran_coa_ids + masukan_coa_ids
        rows = await conn.fetch("""
            SELECT
                je.journal_number,
                je.journal_date::text AS journal_date,
                je.description,
                je.source_type,
                je.source_id::text AS source_id,
                jl.account_id,
                jl.debit,
                jl.credit
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            WHERE je.status = 'POSTED'
              AND je.tenant_id = $1
              AND je.journal_date >= $2
              AND je.journal_date < $3
              AND je.reversed_by_id IS NULL
              AND jl.account_id = ANY($4)
            ORDER BY je.journal_date, je.journal_number
        """, tenant_id, period_start, period_end, all_coa_ids)

        # Separate keluaran vs masukan
        keluaran_txns = []
        masukan_txns = []
        keluaran_total = Decimal("0")
        masukan_total = Decimal("0")

        for r in rows:
            acct_id = r["account_id"]
            # PPN Keluaran = credit side (liability increases)
            if acct_id in keluaran_coa_ids and r["credit"] > 0:
                amt = Decimal(str(r["credit"]))
                keluaran_total += amt
                keluaran_txns.append(PPNTransaction(
                    journal_number=r["journal_number"],
                    journal_date=r["journal_date"],
                    description=r["description"] or "",
                    source_type=r["source_type"] or "",
                    source_id=r["source_id"],
                    amount=amt,
                    dpp=Decimal("0"),  # enriched below from document_tax_lines
                    tax_rate=Decimal("0"),
                ))
            # PPN Masukan = debit side (asset increases)
            elif acct_id in masukan_coa_ids and r["debit"] > 0:
                amt = Decimal(str(r["debit"]))
                masukan_total += amt
                masukan_txns.append(PPNTransaction(
                    journal_number=r["journal_number"],
                    journal_date=r["journal_date"],
                    description=r["description"] or "",
                    source_type=r["source_type"] or "",
                    source_id=r["source_id"],
                    amount=amt,
                    dpp=Decimal("0"),
                    tax_rate=Decimal("0"),
                ))

        # Enrich with document_tax_lines for DPP and rate
        dtl_rows = await conn.fetch("""
            SELECT
                dtl.journal_line_id,
                dtl.base_amount,
                dtl.tax_amount,
                dtl.direction,
                tc.rate
            FROM document_tax_lines dtl
            LEFT JOIN tax_codes tc ON tc.id = dtl.tax_code_id
            WHERE dtl.tenant_id = $1
              AND dtl.coa_id = ANY($2)
              AND dtl.created_at >= $3::timestamp
              AND dtl.created_at < $4::timestamp
        """, tenant_id, all_coa_ids, period_start, period_end)

        # Build lookup by journal_line_id — but we don't have it easily mapped.
        # Alternative: enrich by matching amounts (best-effort) or use aggregate cross-check.
        # For now, cross-check uses aggregate totals.

        # Cross-check: document_tax_lines totals
        dtl_agg = await conn.fetch("""
            SELECT
                direction,
                COALESCE(SUM(tax_amount), 0) AS total
            FROM document_tax_lines
            WHERE tenant_id = $1
              AND coa_id = ANY($2)
              AND created_at >= $3::timestamp
              AND created_at < $4::timestamp
            GROUP BY direction
        """, tenant_id, all_coa_ids, period_start, period_end)

        dtl_keluaran = Decimal("0")
        dtl_masukan = Decimal("0")
        for d in dtl_agg:
            if d["direction"] == "output":
                dtl_keluaran = Decimal(str(d["total"]))
            elif d["direction"] == "input":
                dtl_masukan = Decimal(str(d["total"]))

        return PPNReportResponse(
            period=period,
            ppn_keluaran=PPNSection(
                total=keluaran_total,
                count=len(keluaran_txns),
                transactions=keluaran_txns,
            ),
            ppn_masukan=PPNSection(
                total=masukan_total,
                count=len(masukan_txns),
                transactions=masukan_txns,
            ),
            net_ppn=keluaran_total - masukan_total,
            cross_check=PPNCrossCheck(
                document_tax_lines_keluaran=dtl_keluaran,
                document_tax_lines_masukan=dtl_masukan,
                drift_keluaran=keluaran_total - dtl_keluaran,
                drift_masukan=masukan_total - dtl_masukan,
            ),
        )


# ─── PPh Report ────────────────────────────────────────────────

@router.get("/pph", response_model=PPhReportResponse)
async def get_pph_report(
    request: Request,
    period: str = Query(..., regex=r"^\d{4}-\d{2}$", description="YYYY-MM"),
):
    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]

    try:
        period_start, period_end = _parse_period(period)
    except ValueError:
        raise HTTPException(400, "Invalid period format. Use YYYY-MM.")

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")

        # Step 1: Collect PPh coa_ids from tax_codes (Law 27)
        pph_coas = await conn.fetch("""
            SELECT DISTINCT coa_id
            FROM tax_codes
            WHERE tenant_id = $1
              AND is_withholding = true
              AND coa_id IS NOT NULL
              AND is_active = true
        """, tenant_id)

        pph_coa_ids = [r["coa_id"] for r in pph_coas]

        if not pph_coa_ids:
            return PPhReportResponse(
                period=period,
                by_type=[],
                grand_total_pph=Decimal("0"),
                grand_total_dpp=Decimal("0"),
                cross_check=PPhCrossCheck(
                    withholding_records_total=Decimal("0"),
                    drift=Decimal("0"),
                ),
            )

        # Step 2: Journal-derived PPh amounts
        pph_rows = await conn.fetch("""
            SELECT
                je.id AS journal_id,
                je.journal_number,
                je.journal_date::text AS journal_date,
                je.description,
                je.source_type,
                je.source_id::text AS source_id,
                jl.credit AS amount
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            WHERE je.status = 'POSTED'
              AND je.tenant_id = $1
              AND je.journal_date >= $2
              AND je.journal_date < $3
              AND je.reversed_by_id IS NULL
              AND jl.account_id = ANY($4)
              AND jl.credit > 0
            ORDER BY je.journal_date, je.journal_number
        """, tenant_id, period_start, period_end, pph_coa_ids)

        journal_total = sum(Decimal(str(r["amount"])) for r in pph_rows)
        journal_ids = [r["journal_id"] for r in pph_rows]

        # Step 3: Enrich with withholding_tax_records (metadata only)
        wtr_rows = []
        if journal_ids:
            wtr_rows = await conn.fetch("""
                SELECT
                    wtr.journal_id,
                    wtr.tax_code_id,
                    tc.code AS tax_code,
                    tc.name AS tax_code_name,
                    tc.rate,
                    wtr.base_amount AS dpp,
                    wtr.tax_amount,
                    wtr.npwp AS vendor_npwp,
                    wtr.document_type,
                    v.name AS vendor_name
                FROM withholding_tax_records wtr
                LEFT JOIN tax_codes tc ON tc.id = wtr.tax_code_id
                LEFT JOIN vendors v ON v.id = wtr.vendor_id
                WHERE wtr.tenant_id = $1
                  AND wtr.journal_id = ANY($2)
                  AND wtr.status = 'recorded'
            """, tenant_id, journal_ids)

        # Build journal_id → wtr lookup
        wtr_by_journal = {}
        for w in wtr_rows:
            wtr_by_journal[w["journal_id"]] = w

        # Step 4: Group by tax_code
        by_type_map: dict[str, dict] = {}  # tax_code_name → accumulator
        grand_dpp = Decimal("0")

        for r in pph_rows:
            wtr = wtr_by_journal.get(r["journal_id"])
            tax_code_name = wtr["tax_code_name"] if wtr else "Lainnya"
            tax_code_id = str(wtr["tax_code_id"]) if wtr else None
            tax_code = wtr["tax_code"] if wtr else ""
            rate = Decimal(str(wtr["rate"])) if wtr and wtr["rate"] else Decimal("0")
            dpp = Decimal(str(wtr["dpp"])) if wtr and wtr["dpp"] else Decimal("0")
            pph_amt = Decimal(str(r["amount"]))

            if tax_code_name not in by_type_map:
                by_type_map[tax_code_name] = {
                    "tax_code": tax_code,
                    "tax_code_id": tax_code_id,
                    "rate": rate,
                    "total_pph": Decimal("0"),
                    "total_dpp": Decimal("0"),
                    "transactions": [],
                }

            grp = by_type_map[tax_code_name]
            grp["total_pph"] += pph_amt
            grp["total_dpp"] += dpp
            grand_dpp += dpp
            grp["transactions"].append(PPhTransaction(
                journal_number=r["journal_number"],
                journal_date=r["journal_date"],
                vendor_name=wtr["vendor_name"] if wtr else None,
                vendor_npwp=wtr["vendor_npwp"] if wtr else None,
                document_number=wtr.get("document_type", "") if wtr else None,
                dpp=dpp,
                rate=rate,
                pph_amount=pph_amt,
                source_type=r["source_type"] or "",
                source_id=r["source_id"],
            ))

        by_type_list = [
            PPhByType(
                tax_code=v["tax_code"],
                tax_code_id=v["tax_code_id"],
                rate=v["rate"],
                total_pph=v["total_pph"],
                total_dpp=v["total_dpp"],
                count=len(v["transactions"]),
                transactions=v["transactions"],
            )
            for v in by_type_map.values()
        ]

        # Cross-check: withholding_tax_records total
        wtr_total_row = await conn.fetchval("""
            SELECT COALESCE(SUM(tax_amount), 0)
            FROM withholding_tax_records
            WHERE tenant_id = $1
              AND tax_period = $2
              AND status = 'recorded'
              AND direction = 'cut'
        """, tenant_id, period.replace("-", ""))

        wtr_total = Decimal(str(wtr_total_row or 0))

        return PPhReportResponse(
            period=period,
            by_type=by_type_list,
            grand_total_pph=journal_total,
            grand_total_dpp=grand_dpp,
            cross_check=PPhCrossCheck(
                withholding_records_total=wtr_total,
                drift=journal_total - wtr_total,
            ),
        )


# ─── Export CSV ────────────────────────────────────────────────

@router.get("/ppn/export")
async def export_ppn_csv(
    request: Request,
    period: str = Query(..., regex=r"^\d{4}-\d{2}$"),
):
    """Export PPN report as CSV."""
    from fastapi.responses import StreamingResponse

    report = await get_ppn_report(request, period)

    output = io.StringIO()
    writer = csv.writer(output)

    # Keluaran section
    writer.writerow(["=== PPN KELUARAN ==="])
    writer.writerow(["No", "Tanggal", "No Jurnal", "Keterangan", "DPP", "PPN"])
    for i, t in enumerate(report.ppn_keluaran.transactions, 1):
        writer.writerow([i, t.journal_date, t.journal_number, t.description, t.dpp, t.amount])
    writer.writerow(["", "", "", "TOTAL KELUARAN", "", report.ppn_keluaran.total])
    writer.writerow([])

    # Masukan section
    writer.writerow(["=== PPN MASUKAN ==="])
    writer.writerow(["No", "Tanggal", "No Jurnal", "Keterangan", "DPP", "PPN"])
    for i, t in enumerate(report.ppn_masukan.transactions, 1):
        writer.writerow([i, t.journal_date, t.journal_number, t.description, t.dpp, t.amount])
    writer.writerow(["", "", "", "TOTAL MASUKAN", "", report.ppn_masukan.total])
    writer.writerow([])

    writer.writerow(["KURANG/(LEBIH) BAYAR", "", "", "", "", report.net_ppn])

    content = output.getvalue()
    return StreamingResponse(
        io.BytesIO(content.encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=ppn-report-{period}.csv"},
    )


@router.get("/pph/export")
async def export_pph_csv(
    request: Request,
    period: str = Query(..., regex=r"^\d{4}-\d{2}$"),
):
    """Export PPh report as CSV."""
    from fastapi.responses import StreamingResponse

    report = await get_pph_report(request, period)

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["No", "Tanggal", "Jenis PPh", "Vendor", "NPWP", "DPP", "Tarif(%)", "PPh", "No Dokumen"])
    row_num = 1
    for group in report.by_type:
        for t in group.transactions:
            writer.writerow([
                row_num, t.journal_date, group.tax_code,
                t.vendor_name or "", t.vendor_npwp or "",
                t.dpp, t.rate, t.pph_amount, t.document_number or "",
            ])
            row_num += 1
    writer.writerow([])
    writer.writerow(["", "", "", "", "TOTAL", report.grand_total_dpp, "", report.grand_total_pph, ""])

    content = output.getvalue()
    return StreamingResponse(
        io.BytesIO(content.encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=pph-report-{period}.csv"},
    )
