"""SQL_OMZET (alat sales_summary, tiket CH05 2 Nov) terhadap Postgres SUNGGUHAN, tabel TEMP berkolom sama dengan produksi.
Lewati bila MB2_PG_DSN tak diset. Kontainer sekali-pakai mb2-pg-sekali (pagar sama dengan test_v2_draf_pg)."""
import uuid
from datetime import date

import pytest

from app.milkybook.v2 import tafsir as T

from .test_v2_draf_pg import DSN, _dsn_aman

pytestmark = pytest.mark.skipif(not DSN, reason="MB2_PG_DSN tak diset (butuh Postgres sekali-pakai)")

SKEMA = """
CREATE TEMP TABLE chart_of_accounts (id uuid PRIMARY KEY, tenant_id text NOT NULL, account_type text NOT NULL);
CREATE TEMP TABLE journal_entries (id uuid PRIMARY KEY, tenant_id text NOT NULL, status text NOT NULL, journal_date date NOT NULL);
CREATE TEMP TABLE journal_lines (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), journal_id uuid NOT NULL, account_id uuid NOT NULL,
  debit numeric(18,2) NOT NULL DEFAULT 0, credit numeric(18,2) NOT NULL DEFAULT 0);
"""


@pytest.fixture
async def conn():
    import asyncpg
    _dsn_aman(DSN)
    c = await asyncpg.connect(DSN)
    if await c.fetchval("SELECT current_database()") == "milkydb" or await c.fetchval("SELECT to_regclass('journal_entries')"):
        await c.close()
        raise RuntimeError("DITOLAK: basis data ini berisi tabel produksi")
    await c.execute(SKEMA)
    yield c
    await c.close()  # TEMP hilang bersama sesi


async def _akun(c, tenant, jenis):
    i = uuid.uuid4()
    await c.execute("INSERT INTO chart_of_accounts VALUES ($1, $2, $3)", i, tenant, jenis)
    return i


async def _jurnal(c, tenant, tgl, baris, status="POSTED"):
    j = uuid.uuid4()
    await c.execute("INSERT INTO journal_entries VALUES ($1, $2, $3, $4)", j, tenant, status, tgl)
    for akun, d, k in baris:
        await c.execute("INSERT INTO journal_lines (journal_id, account_id, debit, credit) VALUES ($1, $2, $3, $4)", j, akun, d, k)


async def test_omzet_bersih_posted_dalam_periode_tenant_sendiri(conn):
    jual, retur, lain = await _akun(conn, "kaos", "REVENUE"), await _akun(conn, "kaos", "REVENUE"), await _akun(conn, "kaos", "OTHER_INCOME")
    kas, jual_lain_tenant = await _akun(conn, "kaos", "ASSET"), await _akun(conn, "grap", "REVENUE")
    await _jurnal(conn, "kaos", date(2026, 11, 1), [(kas, 1000000, 0), (jual, 0, 1000000)])          # masuk
    await _jurnal(conn, "kaos", date(2026, 11, 2), [(retur, 100000, 0), (kas, 0, 100000)])          # retur (REVENUE debit)
    await _jurnal(conn, "kaos", date(2026, 11, 2), [(kas, 50000, 0), (lain, 0, 50000)])            # pendapatan lain: bukan omzet
    await _jurnal(conn, "kaos", date(2026, 11, 2), [(kas, 7000, 0), (jual, 0, 7000)], status="DRAFT")  # draf: bukan
    await _jurnal(conn, "kaos", date(2026, 10, 31), [(kas, 9000, 0), (jual, 0, 9000)])             # di luar periode
    await _jurnal(conn, "grap", date(2026, 11, 1), [(kas, 3000, 0), (jual_lain_tenant, 0, 3000)])  # tenant lain
    await _jurnal(conn, "grap", date(2026, 11, 1), [(jual, 0, 4000)])  # baris akun kaos di jurnal tenant lain: tak dihitung
    await _jurnal(conn, "kaos", date(2026, 11, 1), [(kas, 2000, 0), (jual_lain_tenant, 0, 2000)])  # akun tenant lain di jurnal kaos
    nilai = await conn.fetchval(T.SQL_OMZET, "kaos", date(2026, 11, 1), date(2026, 11, 2))
    assert nilai == 900000
    assert await conn.fetchval(T.SQL_OMZET, "kaos", date(2026, 12, 1), date(2026, 12, 31)) == 0
