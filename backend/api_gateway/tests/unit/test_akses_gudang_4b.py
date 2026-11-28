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


# ---------------------------------------------------------------- 4b-3: titik tulis dokumen persediaan
class KonSis(Kon):
    """warehouses dengan is_system (Transit) untuk tolak()."""
    async def fetch(self, sql, *a):
        if "is_system" in sql:
            return [{"id": w, "name": "Transit" if w == "tr" else "Gudang Cabang", "is_system": w == "tr"} for w in a[1]]
        return await super().fetch(sql, *a)


def _dgn_ctx(ctx, aw):
    async def f():
        tok = AG.pakai_ctx(ctx)
        try:
            return await aw()
        finally:
            AG._CTX.reset(tok)
    return jalan(f())


def test_tolak_tanpa_ctx_atau_tenant_lain_dilewati():
    assert jalan(AG.tolak(KonSis(akses=["g1"]), "t", ["g2"])) is None                       # latar/tes: tanpa ctx
    assert _dgn_ctx({**CTX, "tenant_id": "lain"}, lambda: AG.tolak(KonSis(akses=["g1"]), "t", ["g2"])) is None


def test_tolak_blok_403_dan_transit_bebas():
    b = _dgn_ctx(CTX, lambda: AG.tolak(KonSis(akses=["g1"]), "t", ["g1", "g2"], "to_warehouse_id"))
    assert b["code"] == "GUDANG_TANPA_AKSES" and b["status"] == 403 and b["field"] == "to_warehouse_id" and "Gudang Cabang" in b["message"]
    assert _dgn_ctx(CTX, lambda: AG.tolak(KonSis(akses=["g1"]), "t", ["g1", "tr"])) is None  # gudang sistem
    assert _dgn_ctx({**CTX, "business_role_code": "OWNER"}, lambda: AG.tolak(KonSis(akses=["g1"]), "t", ["g2"])) is None


# Daftar titik TETAP (DESAIN §7): situs baru yang memanggil penentu akses wajib didaftarkan di sini.
_APP = Path(__file__).resolve().parents[2] / "app"
TITIK_4B3 = {
    "services/stok_sesuai.py": 2,          # gudang() -> _akses (Penyesuaian, Opname, Batch adjust) + muat() dokumen
    "services/pemindahan_barang.py": 2,    # _rencana asal+tujuan + _header (kirim/terima/selesai/void/batal)
    "services/grn_rencana.py": 2,          # rencana_grn + rencana_void
    "services/sj_so.py": 1,                # Surat Jalan dari SO
    "services/so_pengiriman.py": 1,        # Kirim SO
    "services/faktur_kirim_pratinjau.py": 1,  # pratinjau Kirim faktur
    "services/ledger_kartu.py": 1,         # pengaman SATU jalur gerak stok (Faktur Beli, fulfill, semua penulis)
    "routers/stock_opnames.py": 1,         # _muat dokumen opname
}


def test_titik_tulis_4b3_terdaftar():
    for f, n in TITIK_4B3.items():
        s = (_APP / f).read_text()
        assert s.count("AG.tolak(") + s.count("AG.wajib_dok(") + s.count("await _akses(") - s.count("async def _akses(") >= n, f
    assert "AG.tolak(" in (_APP / "services/stok_sesuai.py").read_text()
    assert "_Depends(_ag_ctx)" in (_APP / "main.py").read_text()


def test_tulis_kartu_pengaman():
    src = inspect.getsource(__import__("app.services.ledger_kartu", fromlist=["x"]).tulis_kartu)
    assert src.index("AG.wajib_dok") < src.index("conn.execute(SQL")
