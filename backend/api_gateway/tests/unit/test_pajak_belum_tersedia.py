"""PAJAK-BELUM-TERSEDIA (15 Okt 2026): modul e-Faktur/Coretax belum dibangun (8 dari 10 tabel tak ada di prod).
Semua rute yang menyentuh tabel yang tak ada dijawab 409 PAJAK_BELUM_TERSEDIA SEBELUM badan handler, bukan 500.
nsfp.py SENGAJA tak diguard (efaktur_sequences ada, hidup)."""
import asyncio
import inspect

import pytest
from fastapi import HTTPException

from app.routers import efaktur as EF
from app.routers import nsfp as NS
from app.routers import pkp_settings as PK
from app.routers import tax_invoices as TI
from app.services import efaktur_module as M


def _guard_names(route):
    return [d.dependency.__name__ for d in route.dependencies]


def _routes(router):
    return {(r.path, tuple(sorted(r.methods))): r for r in router.routes}


@pytest.mark.parametrize("path,method", [
    ("/{tax_invoice_id}", "GET"), ("", "POST"), ("/bulk", "POST"), ("/{tax_invoice_id}/assign-nsfp", "POST"),
    ("/bulk-assign-nsfp", "POST"), ("/{tax_invoice_id}/status", "PATCH"), ("/{tax_invoice_id}/cancel", "POST"),
    ("/{tax_invoice_id}/replace", "POST"),
])
def test_rute_tax_invoices_yang_menyentuh_tabel_hantu_diguard(path, method):
    r = _routes(TI.router)[(path, (method,))]
    assert "require_efaktur_module" in _guard_names(r)


def test_list_dan_by_source_tetap_hidup_tanpa_guard():
    rt = _routes(TI.router)
    for path in ("", "/by-source"):
        assert "require_efaktur_module" not in _guard_names(rt[(path, ("GET",))])


def test_efaktur_dan_pkp_guard_tingkat_router():
    for router in (EF.router, PK.router):
        assert "require_efaktur_module" in [d.dependency.__name__ for d in router.dependencies]


def test_nsfp_tidak_diguard():
    assert not NS.router.dependencies
    for r in NS.router.routes:
        assert "require_efaktur_module" not in _guard_names(r)


def test_kode_dan_pesan_baru_kode_lama_hilang():
    assert M.KODE == "PAJAK_BELUM_TERSEDIA"
    assert M.PESAN == "Faktur pajak (e-Faktur/Coretax) belum tersedia."
    assert "EFAKTUR_NOT_ENABLED" not in inspect.getsource(M)


def test_guard_menjawab_409_bukan_500(monkeypatch):
    class Conn:
        async def fetch(self, *a):
            return [{"t": "tax_invoice_items"}, {"t": "tax_info"}]

    class Acq:
        async def __aenter__(s): return Conn()
        async def __aexit__(s, *a): return False

    class Pool:
        def acquire(s): return Acq()

    async def gp():
        return Pool()

    import app.services.db_pool as dbp
    monkeypatch.setattr(dbp, "get_db_pool", gp)
    with pytest.raises(HTTPException) as e:
        asyncio.run(M.require_efaktur_module())
    assert e.value.status_code == 409
    assert e.value.detail["code"] == "PAJAK_BELUM_TERSEDIA"
    assert e.value.detail["missing_tables"] == ["tax_invoice_items", "tax_info"]


def test_guard_lolos_bila_modul_lengkap(monkeypatch):
    class Conn:
        async def fetch(self, *a):
            return []

    class Acq:
        async def __aenter__(s): return Conn()
        async def __aexit__(s, *a): return False

    class Pool:
        def acquire(s): return Acq()

    async def gp():
        return Pool()

    import app.services.db_pool as dbp
    monkeypatch.setattr(dbp, "get_db_pool", gp)
    assert asyncio.run(M.require_efaktur_module()) is None
