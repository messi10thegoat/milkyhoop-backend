"""BUG-179: tarif Work Center (labor/overhead per jam) di-snapshot ke operasi BOM di SEMUA jalur tulis (buat, tambah operasi, ubah) + disegarkan saat recalculate BOM DRAFT
(pratinjau lama -> baru, ROLLBACK); BOM non-draf tak berubah diam-diam; pembacaan work_centers WAJIB bersaring tenant (dulu jalur ubah `WHERE id = $1` membaca tarif tenant lain).
Tarif eksplisit > 0 dipertahankan (override). Angka 52.300 (paritas Accurate) = uji salinan."""
import asyncio
import inspect
import re
import uuid
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from starlette.requests import Request

import app.routers.bom as B

T = "pt-busana-indah-semesta"
WC, BID, OP = (uuid.uuid4() for _ in range(3))


def jalan(a):
    return asyncio.run(a)


def test_tarif_efektif():
    assert B.tarif_efektif(D(0), D(0), (D(1000), D(20000))) == (D(1000), D(20000))      # 0 -> dari WC
    assert B.tarif_efektif(None, None, (D(1000), D(20000))) == (D(1000), D(20000))
    assert B.tarif_efektif(D(500), D(5000), (D(1000), D(20000))) == (D(500), D(5000))   # eksplisit > 0 dipertahankan
    assert B.tarif_efektif(D(0), D(5000), (D(1000), D(20000))) == (D(1000), D(5000))    # per kolom
    assert B.tarif_efektif(D(0), D(0), None) == (0, 0)                                  # WC bukan milik tenant -> tak ada tarif


def test_tak_ada_baca_work_centers_tanpa_tenant():
    src = inspect.getsource(B)
    assert not re.search(r"FROM work_centers WHERE id = \$1\"", src), "baca tarif WC tanpa saring tenant"
    assert "FROM work_centers WHERE id = $1 AND tenant_id = $2" in inspect.getsource(B.tarif_work_center)


def test_semua_jalur_tulis_snapshot_dari_wc_tenant_ini():
    for fn in (B.create_bom, B.add_operation, B.update_bom):
        s = inspect.getsource(fn)
        assert 'tarif_work_center(conn, ctx["tenant_id"]' in s and "tarif_efektif(" in s, fn.__name__


class Kon:
    def __init__(self, status="draft", ops=None, tarif=(D(0), D(20000))):
        self.status, self.ops, self.tarif, self.q, self.rollback = status, ops if ops is not None else [], tarif, [], False

    def transaction(self):
        kon = self

        class Tx:
            async def __aenter__(self):
                return kon

            async def __aexit__(self, tipe, *e):
                kon.rollback = tipe is not None
                return False if tipe is None else tipe is B._Pratinjau
        return Tx()

    async def fetchrow(self, sql, *a):
        self.q.append((sql, a))
        if "FROM work_centers" in sql:
            assert a[1] == T                                   # tenant eksplisit
            return None if self.tarif is None else {"labor_rate_per_hour": self.tarif[0], "overhead_rate_per_hour": self.tarif[1]}
        if "SELECT status, standard_cost" in sql:
            return {"status": self.status, "standard_cost": 40800, "labor_cost": 9500, "overhead_cost": 0, "total_cost": 50300}
        if "SELECT standard_cost" in sql:
            return {"standard_cost": 40800, "labor_cost": 9500, "overhead_cost": 2000, "total_cost": 52300}
        raise AssertionError(sql)

    async def fetch(self, sql, *a):
        self.q.append((sql, a))
        assert "bom_operations o JOIN bill_of_materials b ON b.id = o.bom_id AND b.tenant_id = $2" in " ".join(sql.split()) and a == (BID, T)
        return self.ops

    async def execute(self, sql, *a):
        self.q.append((sql, a))

    async def fetchval(self, sql, *a):
        self.q.append((sql, a))
        if "SELECT status FROM bill_of_materials" in sql:
            assert a == (T, BID)
            return self.status
        if "calculate_bom_cost" in sql:
            return 52300
        return 1


def op(lab=0, ovh=0, wc=WC):
    return {"id": OP, "operation_number": 4, "operation_name": "Overhead Jahit", "work_center_id": wc, "labor_rate_per_hour": D(lab), "overhead_rate_per_hour": D(ovh)}


def test_segarkan_isi_nol_dari_wc_dan_catat_lama_baru():
    k = Kon(ops=[op()])
    p = jalan(B.segarkan_tarif_operasi(k, T, BID))
    assert p == [{"operation_number": 4, "operation_name": "Overhead Jahit", "labor_rate_per_hour": {"lama": 0.0, "baru": 0.0},
                  "overhead_rate_per_hour": {"lama": 0.0, "baru": 20000.0}}]
    upd = [(s, a) for s, a in k.q if s.lstrip().startswith("UPDATE bom_operations")]
    assert len(upd) == 1 and upd[0][1] == (D(0), D(20000), OP, BID, T) and "tenant_id = $5" in upd[0][0]


def test_segarkan_tak_menyentuh_tarif_eksplisit_atau_tanpa_wc():
    k = Kon(ops=[op(0, 5000)], tarif=(D(0), D(5000)))        # sudah sama -> tak ada perubahan
    assert jalan(B.segarkan_tarif_operasi(k, T, BID)) == []
    k = Kon(ops=[op(0, 7000)])                                # eksplisit 7.000 (WC 20.000) dipertahankan
    assert jalan(B.segarkan_tarif_operasi(k, T, BID)) == [] and not [1 for s, _ in k.q if s.lstrip().startswith("UPDATE")]
    k = Kon(ops=[op()], tarif=None)                           # WC tak ada di tenant ini
    assert jalan(B.segarkan_tarif_operasi(k, T, BID)) == []


def _req():
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    r.state.user = {"tenant_id": T, "user_id": str(uuid.uuid4())}
    return r


def _pasang(monkeypatch, kon):
    async def gp():
        return SimpleNamespace(acquire=lambda: _Acq(kon))
    monkeypatch.setattr(B, "get_pool", gp)


class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


def test_recalculate_draf_menyegarkan_aktif_tidak(monkeypatch):
    k = Kon(status="draft", ops=[op()])
    _pasang(monkeypatch, k)
    r = jalan(B.recalculate_cost(_req(), BID))
    assert r["data"]["total_cost"] == 52300 and len(r["data"]["tarif_diperbarui"]) == 1 and not k.rollback
    k = Kon(status="active", ops=[op()])
    _pasang(monkeypatch, k)
    r = jalan(B.recalculate_cost(_req(), BID))
    assert r["data"]["tarif_diperbarui"] == [] and not [1 for s, _ in k.q if s.lstrip().startswith("UPDATE bom_operations")]   # BOM aktif tak berubah diam-diam


def test_pratinjau_selalu_rollback_dan_lama_baru(monkeypatch):
    k = Kon(status="draft", ops=[op()])
    _pasang(monkeypatch, k)
    r = jalan(B.recalculate_cost_preview(_req(), BID))["data"]
    assert k.rollback is True and r["preview"] is True and r["bisa_segarkan_tarif"] is True
    assert r["sebelum"]["total_cost"] == 50300.0 and r["sesudah"]["total_cost"] == 52300.0 and r["tarif"][0]["overhead_rate_per_hour"] == {"lama": 0.0, "baru": 20000.0}
    k = Kon(status="active", ops=[op()])
    _pasang(monkeypatch, k)
    r = jalan(B.recalculate_cost_preview(_req(), BID))["data"]
    assert r["bisa_segarkan_tarif"] is False and r["tarif"] == [] and k.rollback is True
