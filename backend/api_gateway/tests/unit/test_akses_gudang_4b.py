"""PARITAS-4b (V516): akses gudang per pengguna -- penentu tunggal, API Pengguna -> Akses Gudang, daftar gudang disaring, peta izin = /overrides."""
import asyncio
import inspect
from pathlib import Path

import pytest

from app.services import akses_gudang as AG
from app.services.galat_berkode import GalatBerkode

MIG = Path(__file__).resolve().parents[3] / "migrations"


class Kon:
    def __init__(self, flag=True, akses=()):
        self.flag, self.akses = flag, list(akses)

    async def fetchval(self, sql, *a):
        if "tenant_features" in sql:
            return self.flag
        if "FROM warehouses" in sql:
            return "Gudang Cabang"

    async def fetch(self, sql, *a):
        return [{"warehouse_id": w} for w in self.akses]


CTX = {"tenant_id": "t", "user_id": "u", "business_role_code": "STAFF"}


def jalan(c):
    return asyncio.run(c)


def test_bawaan_aman_semua_gudang():
    assert jalan(AG.boleh(Kon(flag=False, akses=["g1"]), CTX)) is None          # flag mati
    assert jalan(AG.boleh(Kon(akses=[]), CTX)) is None                          # tanpa baris = semua
    for peran in ("OWNER", "ADMIN", "owner"):
        assert jalan(AG.boleh(Kon(akses=["g1"]), {**CTX, "business_role_code": peran})) is None
    assert jalan(AG.boleh(Kon(akses=["g1"]), CTX)) == {"g1"}


def test_wajib_tolak_gudang_luar_akses_berkalimat():
    jalan(AG.wajib(Kon(akses=["g1"]), CTX, ["g1", None]))
    with pytest.raises(GalatBerkode) as e:
        jalan(AG.wajib(Kon(akses=["g1"]), CTX, ["g2"]))
    assert (e.value.status_code, e.value.code) == (403, "GUDANG_TANPA_AKSES") and "Gudang Cabang" in e.value.detail


def test_saring_sql():
    assert AG.saring(None, "w.id", 3) == "" and AG.saring({"a"}, "w.id", 3) == " AND w.id::text = ANY($3::text[])"


def test_migrasi_v516_aditif_berpembalik():
    up = (MIG / "V516__akses_gudang_pengguna.sql").read_text()
    assert "CREATE TABLE user_warehouse_access" in up and "ROW LEVEL SECURITY" in up and "BEGIN" not in up.upper().split("--")[0]
    assert "DROP TABLE IF EXISTS user_warehouse_access" in (MIG / "V516__akses_gudang_pengguna_ROLLBACK.sql").read_text()


def test_peta_izin_sama_dengan_overrides():
    from app.middleware.permission_middleware import izin_untuk_jalur
    ovr = izin_untuk_jalur("/api/team-members/x/overrides", "PATCH")
    assert izin_untuk_jalur("/api/team-members/x/warehouse-access", "PUT") == ovr
    assert izin_untuk_jalur("/api/team-members/x/warehouse-access/preview", "POST") == ovr
    assert izin_untuk_jalur("/api/team-members/x/warehouse-access", "GET") == izin_untuk_jalur("/api/team-members/x", "GET")


def test_api_pengguna_owner_flag_audit():
    from app.routers import team_members as R
    src = inspect.getsource(R._akses_gudang_siap) + inspect.getsource(R.update_member_warehouse_access)
    assert "AG.flag_nyala" in src and '!= "OWNER"' in src and "WAREHOUSE_ACCESS_CHANGED" in src and "conn.transaction()" in src


def test_daftar_gudang_disaring():
    from app.routers import warehouses as W
    src = inspect.getsource(W.list_warehouses)
    assert "AG.boleh(" in src and "AG.saring(" in src
