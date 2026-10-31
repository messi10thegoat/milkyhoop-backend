"""Spek _jam_beku.bekukan_hari (bom waktu 1 Nov 2026): "hari ini" beku di SEMUA jalur baca -- sumber, ikatan `from ... import`
tingkat modul, impor LOKAL di dalam fungsi, dan tanggal_dokumen_iso. Tanpa ini tes yang mematok tanggal kembali jadi bom waktu."""
from datetime import date

import pytest

from app.routers import quotes as Q
from app.utils import tanggal_tenant as TT

from ._jam_beku import bekukan_hari

BEKU = date(2020, 2, 29)  # jauh dari jam asli: tak mungkin kebetulan sama


@pytest.mark.asyncio
async def test_sumber_ikatan_modul_impor_lokal_dan_iso_beku(monkeypatch):
    asli = TT.tanggal_dokumen
    assert Q.tanggal_dokumen is asli, "prasyarat: quotes mengikat tanggal_dokumen tingkat modul"
    bekukan_hari(monkeypatch, BEKU)
    assert await TT.tanggal_dokumen(None, "t") == BEKU
    assert await Q.tanggal_dokumen(None, "t") == BEKU
    from app.utils.tanggal_tenant import tanggal_dokumen as lokal  # pola _perpanjang_penawaran
    assert await lokal(None, "t") == BEKU
    assert await TT.tanggal_dokumen_iso(None, "t") == BEKU.isoformat()


@pytest.mark.asyncio
async def test_tambalan_dilepas_sesudah_tes(monkeypatch):
    bekukan_hari(monkeypatch, BEKU)
    monkeypatch.undo()
    assert TT.tanggal_dokumen.__module__ == "app.utils.tanggal_tenant" and Q.tanggal_dokumen is TT.tanggal_dokumen
