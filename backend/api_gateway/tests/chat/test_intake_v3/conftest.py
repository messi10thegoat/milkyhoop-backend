"""Test fixtures for V3 parity suite."""

import asyncio
import os
import sys
from dataclasses import dataclass
from decimal import Decimal

import pytest
import pytest_asyncio

sys.path.insert(0, "/app/backend/api_gateway")

from app.services.db_pool import get_db_pool  # noqa: E402


# Tenant fixture. Dulu "grapgrap"; tenant itu HILANG saat pemulihan basis data
# (diukur 2026-09-03: compute_ar_outstanding('grapgrap') = 0 baris,
# compute_ap_outstanding('grapgrap') = 0 baris), sehingga kelima skenario
# parity gagal bukan karena V2/V3 berbeda, melainkan karena tak ada satu pun
# faktur/tagihan untuk disintesiskan jadi OCR.
#
# Dialihkan ke tenant uji yang HIDUP (1 AR + 15 AP saat diukur). Dibuat
# env-override supaya siapa pun bisa mengarahkannya ke tenant lain tanpa
# menyunting berkas -- tenant fixture berumur pendek, dan tes yang mati karena
# datanya pindah adalah tes yang melatih pembaca mengabaikan merah.
TENANT = os.environ.get("PARITY_TENANT", "kaos-biru-konveksi")


# pytest-asyncio 1.x: session-scoped loop so the singleton asyncpg pool binds once.
@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def pool():
    return await get_db_pool()


@dataclass
class ARFixture:
    invoice_id: str
    invoice_number: str
    customer_name: str
    outstanding: Decimal
    issue_date: str  # ISO
    bank_id: str
    bank_account_number: str


@dataclass
class APFixture:
    bill_id: str
    bill_number: str
    vendor_name: str
    outstanding: Decimal
    issue_date: str
    bank_id: str
    bank_account_number: str


async def _fetch_ar(pool) -> ARFixture:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", TENANT)
            row = await conn.fetchrow(
                """
                SELECT ar.invoice_id,
                       ar.invoice_number,
                       c.nama AS customer_name,
                       ar.outstanding,
                       ar.invoice_date::text AS issue_date
                FROM compute_ar_outstanding($1) ar
                JOIN sales_invoices si ON si.id = ar.invoice_id
                -- `::text` di sini adalah DRIFT: diukur 2026-09-03, `customers.id` DAN
                -- `sales_invoices.customer_id` SAMA-SAMA uuid, sehingga cast ke
                -- text menghasilkan `operator does not exist: text = uuid` dan
                -- MEMATIKAN seluruh fixture AR (skenario 1 & 2) sebelum satu
                -- assert pun berjalan.
                JOIN customers c ON c.id = si.customer_id
                WHERE ar.outstanding > 0
                  AND si.status NOT IN ('draft', 'void')
                ORDER BY ar.outstanding DESC
                LIMIT 1
                """,
                TENANT,
            )
            if not row:
                raise RuntimeError(
                    "grapgrap has no active AR invoice with outstanding > 0. "
                    "Seed test data or provide fixture manually."
                )

            bank = await conn.fetchrow(
                "SELECT id, account_number FROM bank_accounts "
                "WHERE tenant_id = $1 AND is_active = true LIMIT 1",
                TENANT,
            )
            if not bank:
                raise RuntimeError("grapgrap has no active bank accounts")

    return ARFixture(
        invoice_id=str(row["invoice_id"]),
        invoice_number=row["invoice_number"],
        customer_name=row["customer_name"],
        outstanding=row["outstanding"],
        issue_date=row["issue_date"],
        bank_id=str(bank["id"]),
        bank_account_number=bank["account_number"] or "",
    )


async def _fetch_ap(pool) -> APFixture:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", TENANT)
            row = await conn.fetchrow(
                """
                SELECT ap.bill_id,
                       ap.bill_number,
                       v.name AS vendor_name,
                       ap.outstanding,
                       ap.bill_date::text AS issue_date
                FROM compute_ap_outstanding($1) ap
                JOIN bills b ON b.id = ap.bill_id
                JOIN vendors v ON v.id = b.vendor_id
                WHERE ap.outstanding > 0
                  AND b.status_v2 NOT IN ('draft', 'void')
                ORDER BY ap.outstanding DESC
                LIMIT 1
                """,
                TENANT,
            )
            if not row:
                raise RuntimeError(
                    "grapgrap has no active AP bill with outstanding > 0. "
                    "Seed test data or provide fixture manually."
                )
            bank = await conn.fetchrow(
                "SELECT id, account_number FROM bank_accounts "
                "WHERE tenant_id = $1 AND is_active = true LIMIT 1",
                TENANT,
            )
            if not bank:
                raise RuntimeError("grapgrap has no active bank accounts")

    return APFixture(
        bill_id=str(row["bill_id"]),
        bill_number=row["bill_number"],
        vendor_name=row["vendor_name"],
        outstanding=row["outstanding"],
        issue_date=row["issue_date"],
        bank_id=str(bank["id"]),
        bank_account_number=bank["account_number"] or "",
    )


def synthesize_ocr_from_ar(ar: ARFixture) -> dict:
    """Build OCR payload matching GPT-4o vision schema for incoming payment."""
    return {
        "doc_type": "bank_transfer",
        "total_amount": float(ar.outstanding),
        "amount": float(ar.outstanding),
        "counterparty_name": ar.customer_name,
        "customer_name": ar.customer_name,
        "document_date": ar.issue_date,
        "date": ar.issue_date,
        "transfer_direction": "in",
        "destination_account_number": ar.bank_account_number,
    }


def synthesize_ocr_from_ap(ap: APFixture) -> dict:
    """Build OCR payload for outgoing payment to vendor."""
    return {
        "doc_type": "bank_transfer",
        "total_amount": float(ap.outstanding),
        "amount": float(ap.outstanding),
        "counterparty_name": ap.vendor_name,
        "vendor_name": ap.vendor_name,
        "document_date": ap.issue_date,
        "date": ap.issue_date,
        "transfer_direction": "out",
        "source_account_number": ap.bank_account_number,
    }
