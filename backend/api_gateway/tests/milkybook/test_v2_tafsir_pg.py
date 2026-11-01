"""Log ketikan V470 terhadap Postgres SUNGGUHAN (kontainer sekali-pakai mb2-pg-sekali; pagar sama dengan test_v2_draf_pg).
Lewati bila MB2_PG_DSN tak diset."""
import os

import pytest

from app.milkybook.v2 import tafsir as T

from .test_v2_draf_pg import DSN, _dsn_aman

pytestmark = pytest.mark.skipif(not DSN, reason="MB2_PG_DSN tak diset (butuh Postgres sekali-pakai)")


@pytest.fixture
async def conn():
    import asyncpg
    _dsn_aman(DSN)
    c = await asyncpg.connect(DSN)
    if await c.fetchval("SELECT current_database()") == "milkydb" or await c.fetchval("SELECT to_regclass('journal_entries')"):
        await c.close()
        raise RuntimeError("DITOLAK: basis data ini berisi tabel produksi")
    mig = open("../migrations/V470__milkybook_v2_tafsir_log.sql").read()
    await c.execute("DROP TABLE IF EXISTS milkybook_v2_tafsir_log")
    await c.execute(mig)
    await c.execute(mig)  # idempoten
    yield c
    await c.execute(open("../migrations/V470__milkybook_v2_tafsir_log_ROLLBACK.sql").read())
    await c.close()


def _hasil(sumber="llm"):
    return {"intent": "clarify", "sumber": sumber}


async def test_tulis_dan_buang_basi_hanya_tenant_sendiri(conn):
    body = T.MintaTafsir(teks="rocky 0812 ada tagihan?", tahap="items")
    await T.tulis_log(conn, "kaos", "u1", body, _hasil(), 850, "claude-sonnet-5-5", None)
    await T.tulis_log(conn, "grap", "u7", body, _hasil(), 10, None, None)
    await conn.execute("UPDATE milkybook_v2_tafsir_log SET dibuat = NOW() - INTERVAL '31 days'")
    await T.tulis_log(conn, "kaos", "u1", body, _hasil("penjaga"), 5, None, None)   # tulis kaos -> buang basi KAOS saja
    baris = [tuple(r) for r in await conn.fetch(
        "SELECT tenant_id, user_id, ketikan, sumber, latensi_ms FROM milkybook_v2_tafsir_log ORDER BY id")]
    assert baris == [("grap", "u7", "rocky 0812 ada tagihan?", "llm", 10),
                     ("kaos", "u1", "rocky 0812 ada tagihan?", "penjaga", 5)]


async def test_check_menolak_sumber_asing(conn):
    import asyncpg
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute("INSERT INTO milkybook_v2_tafsir_log (tenant_id, user_id, ketikan, intent, sumber) "
                           "VALUES ('k','u','x','clarify','lain')")
