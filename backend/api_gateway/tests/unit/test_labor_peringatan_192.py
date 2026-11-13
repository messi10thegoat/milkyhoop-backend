"""BUG-192: /labor memberi tahu bila tarif standar (labor DAN overhead) 0; perilaku terap tak berubah. Paritas nyata = uji salinan."""
import pathlib
from decimal import Decimal

import pytest

from app.services import labor_peringatan as LP

APP = pathlib.Path(LP.__file__).resolve().parents[1]


@pytest.mark.parametrize("labor,oh,ada", [(0, 0, False), (Decimal("0"), Decimal("0.00"), False), (None, None, False), ("0", "0", False),
                                          (20000, 0, True), (0, 20000, True), (Decimal("15000.5"), Decimal("1"), True)])
def test_tarif_ditemukan_dan_peringatan(labor, oh, ada):
    assert LP.tarif_ditemukan(labor, oh) is ada
    assert (LP.peringatan(labor, oh) is None) is ada


def test_teks_peringatan_indonesia_dan_memberi_jalan_keluar():
    t = LP.peringatan(0, 0)
    assert t.startswith("Tarif standar 0: tidak ada biaya diterapkan.") and "Pilih operasi atau Work Center bertarif" in t and "biaya standar BOM tetap diterapkan saat output" in t


def test_respons_labor_memuat_kedua_medan_dari_penentu_bersama():
    s = (APP / "routers/production.py").read_text()
    assert '"tarif_ditemukan": LP.tarif_ditemukan(labor_rate_std, oh_rate_std)' in s and '"peringatan": LP.peringatan(labor_rate_std, oh_rate_std)' in s
    assert "Labor recorded (standard-cost applied)" in s          # kalimat sukses lama tak berubah
