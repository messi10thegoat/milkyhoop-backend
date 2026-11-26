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


# --- MN-1 *_text + pratinjau (BE-BUTUH MASTER 26 Nov) ---------------------------------------------------------------
from decimal import Decimal as _D  # noqa: E402
SMG, Path = S, pathlib.Path


def _g(gid="g1", mi="10", mk=None, stok="3"):
    return {"gudang_id": gid, "gudang": "Gudang A", "minimum": _D(mi), "maksimum": _D(mk) if mk else None, "stok": _D(stok),
            "usulan_beli": SMG.usulan(_D(stok), _D(mi))}


def test_teks_baris_format_indonesia_dan_status():
    b = SMG.beri_teks(_g(mi="1500", stok="2.5"), "kg")
    assert (b["minimum_text"], b["maksimum_text"], b["stok_text"], b["usulan_beli_text"]) == ("1.500 kg", None, "2,5 kg", "1.497,5 kg")
    assert b["status_text"] == "Di bawah minimum" and b["di_bawah_minimum"]
    c = SMG.beri_teks(_g(mi="0", stok="0"), "pcs")
    assert c["status_text"] is None and c["minimum_text"] is None


def test_rencana_simpan_dari_ke_dan_blok():
    lama = [_g()]
    r = SMG.rencana_simpan(lama, [{"gudang_id": "g1", "minimum": 20, "maksimum": 50}], "pcs")
    assert r["ok"] and r["baris"] == [{"gudang_id": "g1", "gudang": "Gudang A", "dari_text": "minimum 10 pcs", "ke_text": "minimum 20 pcs, maksimum 50 pcs"}]
    assert SMG.rencana_simpan(lama, [{"gudang_id": "g1", "minimum": 0, "maksimum": None}], "pcs")["baris"][0]["ke_text"] == "(tidak diatur)"
    assert SMG.rencana_simpan(lama, [{"gudang_id": "g1", "minimum": 10, "maksimum": None}], "pcs")["teks"] == "Tidak ada perubahan."
    b = SMG.rencana_simpan(lama, [{"gudang_id": "g1", "minimum": 5, "maksimum": 2}, {"gudang_id": "gX", "minimum": 1}], "pcs")
    assert not b["ok"] and [x["code"] for x in b["blocks"]] == ["MINIMUM_TAK_SAH", "GUDANG_TAK_SAH"] and b["blocks"][0]["field"] == "minimum"
    assert SMG.rencana_simpan([], [], None)["blocks"][0]["code"] == "BARANG_TAK_ADA"


def test_put_dan_pratinjau_penentu_sama():
    src = (Path(__file__).parents[2] / "app" / "services" / "stok_minimum_gudang.py").read_text()
    assert "tolak(await ukur_rencana(" in src
    inv = (Path(__file__).parents[2] / "app" / "routers" / "inventory.py").read_text()
    assert "SMG.ukur_rencana(conn, tid, item_id" in inv and '"/stok-minimum/{item_id}/preview"' in inv
