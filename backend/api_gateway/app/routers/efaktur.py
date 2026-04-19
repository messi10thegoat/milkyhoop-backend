"""
E-Faktur XML Export Engine.
Pre-validate, export XML, and track export batches.
"""
import io
import zipfile
import logging
from decimal import Decimal
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..schemas.efaktur import EfakturPeriodRequest
from ..services.xml_generator import load_xml_config, generate_xml

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Helpers (per-router pool pattern) ─────────────────────
import asyncpg


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    if not user.get("tenant_id"):
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": user.get("tenant_id"), "user_id": user.get("user_id")}


async def validate_invoices(conn, invoices, tenant_id) -> list:
    """Shared validation logic for both /validate and /export."""

    # Fetch PKP info
    pkp = await conn.fetchrow(
        "SELECT is_pkp, npwp_pkp FROM tax_info WHERE tenant_id = $1", tenant_id
    )

    # Fetch kode_transaksi requirements
    kode_reqs = {}
    rows = await conn.fetch(
        "SELECT kode, requires_keterangan, requires_cap_fasilitas FROM djp_kode_transaksi"
    )
    for r in rows:
        kode_reqs[r["kode"]] = {
            "keterangan": r["requires_keterangan"],
            "cap": r["requires_cap_fasilitas"],
        }

    errors = []

    for inv in invoices:
        issues = []
        inv_id = str(inv["id"])

        # PKP check
        if not pkp or not pkp["is_pkp"]:
            issues.append("Tenant bukan PKP")

        # NPWP penjual
        npwp_p = inv.get("npwp_penjual") or ""
        if len(npwp_p) not in (15, 16):
            issues.append("NPWP penjual kosong atau tidak valid")

        # NSFP
        if not inv.get("faktur_number"):
            issues.append("NSFP belum di-assign")

        # Kode transaksi
        kt = inv.get("kode_transaksi") or ""
        valid_kodes = [f"{i:02d}" for i in range(1, 10)]
        if kt not in valid_kodes:
            issues.append("Kode transaksi tidak valid")
        else:
            req = kode_reqs.get(kt, {})
            if req.get("keterangan") and not inv.get("keterangan_tambahan"):
                issues.append(f"Keterangan tambahan wajib untuk kode {kt}")
            if req.get("cap") and not inv.get("cap_fasilitas"):
                issues.append(f"Cap fasilitas wajib untuk kode {kt}")

        # Items
        items = await conn.fetch(
            "SELECT * FROM tax_invoice_items WHERE tax_invoice_id = $1 ORDER BY line_number",
            inv["id"],
        )

        if not items:
            issues.append("Faktur tidak punya item")
        else:
            for idx, item in enumerate(items, 1):
                if not item.get("kode_barang_jasa"):
                    issues.append(f"Item baris {idx}: kode barang/jasa kosong")
                if not item.get("satuan_ukur"):
                    issues.append(f"Item baris {idx}: satuan ukur kosong")

            # DPP match
            sum_dpp = sum(Decimal(str(item.get("dpp") or 0)) for item in items)
            header_dpp = Decimal(str(inv.get("dpp") or inv.get("total_dpp") or 0))
            if abs(sum_dpp - header_dpp) > Decimal("0.01"):
                issues.append(
                    f"Total DPP header ({header_dpp}) tidak cocok dengan detail ({sum_dpp})"
                )

            # PPN match
            sum_ppn = sum(Decimal(str(item.get("ppn") or 0)) for item in items)
            header_ppn = Decimal(str(inv.get("ppn") or inv.get("total_ppn") or 0))
            if abs(sum_ppn - header_ppn) > Decimal("0.01"):
                issues.append(
                    f"Total PPN header ({header_ppn}) tidak cocok dengan detail ({sum_ppn})"
                )

        # NPWP/NIK pembeli
        jenis_id = inv.get("jenis_id_pembeli") or ""
        if jenis_id == "TIN":
            npwp_b = inv.get("npwp_pembeli") or ""
            if not npwp_b or len(npwp_b) < 15:
                issues.append("NPWP pembeli tidak valid")
        elif jenis_id == "NIK":
            nik = inv.get("nik_pembeli") or ""
            if not nik or len(nik) != 16:
                issues.append("NIK pembeli tidak valid")

        if issues:
            errors.append(
                {
                    "tax_invoice_id": inv_id,
                    "faktur_number": inv.get("faktur_number"),
                    "referensi": inv.get("referensi"),
                    "issues": issues,
                }
            )

    return errors


# ── Endpoints ────────────────────────────────────────────


@router.post("/validate")
async def validate_efaktur(request: Request, payload: EfakturPeriodRequest):
    """Pre-export validation — check all faktur are ready for XML export."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        tid = ctx["tenant_id"]
        await conn.execute(f"SET LOCAL app.tenant_id = '{tid}'")

        invoices = await conn.fetch(
            """
            SELECT * FROM tax_invoices
            WHERE tenant_id = $1
              AND direction = $2
              AND masa_pajak = $3
              AND tahun_pajak = $4
              AND status = 'nsfp_assigned'
            ORDER BY faktur_number
        """,
            tid,
            payload.export_type,
            payload.masa_pajak,
            payload.tahun_pajak,
        )

        total = len(invoices)
        if total == 0:
            return {
                "valid": True,
                "total_invoices": 0,
                "valid_count": 0,
                "invalid_count": 0,
                "errors": [],
                "ready_for_export": False,
            }

        errors = await validate_invoices(conn, invoices, tid)
        invalid = len(errors)
        valid = total - invalid

        return {
            "valid": invalid == 0,
            "total_invoices": total,
            "valid_count": valid,
            "invalid_count": invalid,
            "errors": errors,
            "ready_for_export": invalid == 0 and total > 0,
        }


@router.post("/export")
async def export_efaktur(request: Request, payload: EfakturPeriodRequest):
    """Generate XML file(s) and download. Updates invoice status to 'exported'."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            tid = ctx["tenant_id"]
            uid = ctx["user_id"]
            await conn.execute(f"SET LOCAL app.tenant_id = '{tid}'")

            # PKP check
            pkp = await conn.fetchrow(
                "SELECT is_pkp FROM tax_info WHERE tenant_id = $1", tid
            )
            if not pkp or not pkp["is_pkp"]:
                raise HTTPException(400, "Tenant bukan PKP")

            # Query eligible invoices
            invoices = await conn.fetch(
                """
                SELECT * FROM tax_invoices
                WHERE tenant_id = $1
                  AND direction = $2
                  AND masa_pajak = $3
                  AND tahun_pajak = $4
                  AND status = 'nsfp_assigned'
                ORDER BY faktur_number
            """,
                tid,
                payload.export_type,
                payload.masa_pajak,
                payload.tahun_pajak,
            )

            if not invoices:
                raise HTTPException(
                    404, "Tidak ada faktur siap export untuk periode ini"
                )

            # Validate
            errors = await validate_invoices(conn, invoices, tid)
            if errors:
                raise HTTPException(
                    400,
                    detail={
                        "message": "Validasi gagal, perbaiki faktur bermasalah terlebih dahulu",
                        "errors": errors,
                    },
                )

            # Fetch items for each invoice
            invoice_data = []
            for inv in invoices:
                items = await conn.fetch(
                    "SELECT * FROM tax_invoice_items WHERE tax_invoice_id = $1 ORDER BY line_number",
                    inv["id"],
                )
                invoice_data.append(
                    {
                        "header": dict(inv),
                        "items": [dict(i) for i in items],
                    }
                )

            # Load config
            config = load_xml_config()

            # Split into chunks of max 200
            chunks = [
                invoice_data[i : i + 200] for i in range(0, len(invoice_data), 200)
            ]

            # Generate XML per chunk
            xml_files = []
            for idx, chunk in enumerate(chunks):
                xml_content = generate_xml(chunk, config)
                part = f"_part{idx+1}" if len(chunks) > 1 else ""
                filename = f"efaktur_{payload.export_type}_{payload.tahun_pajak}_{payload.masa_pajak}{part}.xml"
                xml_files.append({"filename": filename, "content": xml_content})

            primary_filename = xml_files[0]["filename"]

            # Record export batch
            export_id = await conn.fetchval(
                """
                INSERT INTO efaktur_exports (
                    id, tenant_id, export_type, masa_pajak, tahun_pajak,
                    total_faktur, file_name, exported_by
                ) VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $6, $7)
                RETURNING id
            """,
                tid,
                payload.export_type,
                payload.masa_pajak,
                payload.tahun_pajak,
                len(invoices),
                primary_filename,
                uid,
            )

            # Update invoice status
            for inv in invoices:
                await conn.execute(
                    "UPDATE tax_invoices SET status = 'exported', updated_at = now() WHERE id = $1",
                    inv["id"],
                )

            logger.info(
                f"E-Faktur exported: {len(invoices)} invoices, export_id={export_id}"
            )

    # Return file
    if len(xml_files) == 1:
        return Response(
            content=xml_files[0]["content"],
            media_type="application/xml",
            headers={
                "Content-Disposition": f'attachment; filename="{xml_files[0]["filename"]}"'
            },
        )
    else:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in xml_files:
                zf.writestr(f["filename"], f["content"])
        zip_buffer.seek(0)
        zip_name = f"efaktur_{payload.export_type}_{payload.tahun_pajak}_{payload.masa_pajak}.zip"
        return Response(
            content=zip_buffer.read(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
        )


@router.get("/exports")
async def list_exports(
    request: Request, export_type: str = None, tahun_pajak: int = None
):
    """List export batches."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        tid = ctx["tenant_id"]
        await conn.execute(f"SET LOCAL app.tenant_id = '{tid}'")

        query = "SELECT * FROM efaktur_exports WHERE tenant_id = $1"
        params = [tid]
        idx = 2

        if export_type:
            query += f" AND export_type = ${idx}"
            params.append(export_type)
            idx += 1

        if tahun_pajak:
            query += f" AND tahun_pajak = ${idx}"
            params.append(tahun_pajak)
            idx += 1

        query += " ORDER BY exported_at DESC"

        rows = await conn.fetch(query, *params)

        data = []
        for r in rows:
            data.append(
                {
                    "id": str(r["id"]),
                    "export_type": r["export_type"],
                    "masa_pajak": r["masa_pajak"],
                    "tahun_pajak": r["tahun_pajak"],
                    "total_faktur": r["total_faktur"],
                    "file_name": r["file_name"],
                    "exported_at": r["exported_at"].isoformat()
                    if r["exported_at"]
                    else None,
                    "exported_by": str(r["exported_by"]) if r["exported_by"] else None,
                }
            )

        return {"data": data, "total": len(data)}
