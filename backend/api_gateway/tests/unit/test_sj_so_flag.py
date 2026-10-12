"""R1 irisan 1: flag pengiriman_sebelum_faktur HANYA kaos (V416)."""
from pathlib import Path

APP = Path(__file__).resolve().parents[2]


def test_flag_hanya_kaos():
    s = next((APP.parent / "migrations").glob("V416__flag_pengiriman_sebelum_faktur_kaos.sql")).read_text()
    assert "'kaos-biru-konveksi'" in s and "bocor ke tenant lain" in s
