"""MN-1 (26 Nov 2026): stok minimum/maksimum per gudang. SQL diuji di Postgres nyata: scripts/uji-mn1-pg.sh."""
import asyncio
import os
import pathlib
import re
from decimal import Decimal as D

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.services import stok_minimum_gudang as S  # noqa: E402

MIG = pathlib.Path(__file__).resolve().parents[3] / "migrations"


def test_periksa_cermin_check_v511():
    assert S.periksa(0, None) is None and S.periksa(5, 5) is None and S.periksa("2.5", None) is None
    assert S.periksa(-1, None) == "Minimum tidak boleh negatif."
    assert S.periksa(5, 3) == "Maksimum tidak boleh lebih kecil dari minimum."
    up = (MIG / "V511__stok_minimum_gudang.sql").read_text()
    assert "CHECK (minimum >= 0)" in up and "CHECK (maksimum IS NULL OR maksimum >= minimum)" in up and "ENABLE ROW LEVEL SECURITY" in up
    assert "INSERT INTO" not in up and "UPDATE " not in up and "warehouse_stock" not in re.sub(r"--.*", "", up)


def test_usulan():
    assert S.usulan(3, 10) == D(7) and S.usulan(12, 10) == 0 and S.usulan(-2, 5) == D(7) and S.usulan(0, 0) == 0


def test_flag_hanya_true_asli():
    class C:
        def __init__(self, v): self.v = v
        async def fetchval(self, sql, *a): assert a == ("t", "stok_minimum_gudang_v1"); return self.v
    assert asyncio.run(S.flag_nyala(C(True), "t")) is True and asyncio.run(S.flag_nyala(C(1), "t")) is False
    with pytest.raises(Exception) as e:
        asyncio.run(S.wajib_flag(C(False), "t"))
    assert e.value.status_code == 404


def test_sql_tenant_ledger_tanpa_transit():
    for q in (S._SQL_BARIS, S._SQL_PESAN):
        assert "NOT w.is_system" in q and "inventory_ledger l" in q and "l.warehouse_id" in q and "warehouse_stock" not in q
    assert "w.tenant_id = $1" in S._SQL_BARIS and "m.tenant_id = $1" in S._SQL_PESAN


def test_rute_terpetakan_izin_item():
    from app.middleware import permission_middleware as P
    src = pathlib.Path(P.__file__).read_text()
    assert '(r"^/api/inventory/stok\\-minimum/[^/]+$", ["GET"], "item", "R")' in src
    assert '(r"^/api/inventory/stok\\-minimum/[^/]+$", ["PUT"], "item", "U")' in src
