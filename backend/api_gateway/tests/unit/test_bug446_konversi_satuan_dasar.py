"""BUG-446: konversi ke satuan dasar (juga lewat alias) / satuan kembar ditolak 422 di POST/PUT /api/items (pratinjau == tulis)."""
import asyncio
from types import SimpleNamespace as NS

import pytest

from app.services import satuan_dasar as SD
from app.services.galat_berkode import GalatBerkode


class Kon:
    async def fetch(self, sql, *a):   # kamus satuan tenant: 'pcs' alias 'Pieces'
        return [{"nama": "Pcs", "singkatan": "pieces"}]


def k(unit, aktif=True):
    return NS(conversion_unit=unit, conversion_factor=24, is_active=aktif)


def jalan(base, conv):
    return asyncio.run(SD.cek_konversi_barang(Kon(), "t", base, conv))


@pytest.mark.parametrize("base,unit", [("pcs", "pcs"), ("Pcs", " PCS "), ("pcs", "Pieces")])
def test_satuan_dasar_ditolak(base, unit, monkeypatch):
    async def alias(conn, tid):
        return {"pcs": "pcs", "pieces": "pcs"}
    monkeypatch.setattr(SD, "_alias", alias)
    with pytest.raises(GalatBerkode) as e:
        jalan(base, [k(unit)])
    assert e.value.status_code == 422 and e.value.code == "KONVERSI_SATUAN_DASAR"


def test_kembar_dan_lolos(monkeypatch):
    async def alias(conn, tid):
        return {}
    monkeypatch.setattr(SD, "_alias", alias)
    with pytest.raises(GalatBerkode) as e:
        jalan("pcs", [k("dus"), k("Dus")])
    assert e.value.code == "KONVERSI_GANDA"
    jalan("pcs", [k("dus"), k("karton")])
    jalan("pcs", [k("pcs", aktif=False)])     # baris nonaktif tak dinilai
    jalan("pcs", [])


def test_faktor_nol_negatif_ditolak_skema():
    for f in (0, -1, "abc"):
        with pytest.raises(ValueError):
            SD.faktor_sah(f)
