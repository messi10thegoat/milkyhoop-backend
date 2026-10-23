"""Teks Posisi SO anggota menunggu_pembayaran (23 Okt 2026, putusan MASTER): kolom menyebut APA yang ditagih --
DP -> "Menunggu DP", TERMIN -> "Menunggu termin N", PELUNASAN -> "Menunggu pelunasan" (dulu "Menunggu tagihan")."""
from decimal import Decimal as D

import pytest

from app.services import so_posisi as SP

PEL, DP, TER = {"purpose": "PELUNASAN", "termin_ke": None}, {"purpose": "DP", "termin_ke": None}, {"purpose": "TERMIN", "termin_ke": 2}
TEKS_BAYAR = ("Menunggu DP", "Menunggu pelunasan")


def _p(bb, faktur=False, fase=None):
    return SP.posisi("confirmed", D("10"), bb, faktur, D("0"), False, D("0"), fase)


@pytest.mark.parametrize("bb,harap", [([PEL], "Menunggu pelunasan"), ([DP], "Menunggu DP"), ([TER], "Menunggu termin 2"),
                                      ([DP, PEL], "Menunggu DP"), ([PEL, DP], "Menunggu pelunasan")])
def test_teks_menyebut_apa_yang_ditagih(bb, harap):
    assert _p(bb) == (harap, False)


@pytest.mark.parametrize("bb", [[PEL], [DP], [TER], [DP, PEL], [PEL, DP]])
def test_anggota_menunggu_pembayaran_tak_pernah_menunggu_tagihan(bb):
    assert SP.menunggu_pembayaran("confirmed", D("10"), bb, False)
    t, _ = _p(bb)
    assert t in TEKS_BAYAR or t.startswith("Menunggu termin")


def test_fase_lokasi_tetap_menang_dan_tanpa_proforma_belum_ditagih():
    assert _p([PEL], fase="Tersedia di Toko") == ("Tersedia di Toko", False)
    assert _p([]) == ("Belum ditagih", False)
