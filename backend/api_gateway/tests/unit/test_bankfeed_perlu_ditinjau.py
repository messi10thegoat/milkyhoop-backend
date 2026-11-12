"""BF-1e G6: perlu_ditinjau per rekening di daftar Kas & Bank — SQL satu GROUP BY, hanya untuk_ditinjau + mungkin_duplikat, bertenant; router membawanya per baris."""
import asyncio
import inspect
import uuid

from app.routers import bank_accounts as BAR
from app.services import bankfeed_daftar as BD

T = "t-uji"


class _K:
    def __init__(self, rows=()):
        self.rows, self.sql = list(rows), []

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        return self.rows


def test_perlu_ditinjau_satu_query_hanya_dua_status_bertenant_dan_per_rekening():
    a, b = uuid.uuid4(), uuid.uuid4()
    k = _K([{"bank_account_id": a, "n": 3}])
    hasil = asyncio.run(BD.perlu_ditinjau_per_rekening(k, T, [a, b]))
    assert hasil == {str(a): 3}                                                    # rekening tanpa baris tak ada di peta (router: default 0)
    assert len(k.sql) == 1                                                         # SATU query untuk seluruh halaman
    sql, param = k.sql[0]
    assert "tenant_id = $1" in sql and "GROUP BY bank_account_id" in sql and param[0] == T
    assert "status IN ('untuk_ditinjau', 'mungkin_duplikat')" in sql               # hanya dua status ini; dicatat/dikecualikan tak dihitung
    assert "dicatat" not in sql and "dikecualikan" not in sql
    assert asyncio.run(BD.perlu_ditinjau_per_rekening(_K(), T, [])) == {}          # halaman kosong: tanpa query


def test_router_daftar_kas_bank_membawa_perlu_ditinjau_per_baris_default_nol():
    src = inspect.getsource(BAR)
    assert "BFD.perlu_ditinjau_per_rekening(conn, ctx[\"tenant_id\"], [r[\"id\"] for r in rows])" in src
    assert '"perlu_ditinjau": perlu.get(str(row["id"]), 0),' in src
