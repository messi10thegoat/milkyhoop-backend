"""R1 irisan 2: flag pengiriman_stok_sebelum_faktur HANYA kaos (V418)."""
from pathlib import Path

APP = Path(__file__).resolve().parents[2]


def test_flag_stok_hanya_kaos():
    s = next((APP.parent / "migrations").glob("V418__flag_pengiriman_stok_sebelum_faktur_kaos.sql")).read_text()
    assert "'kaos-biru-konveksi'" in s and "bocor ke tenant lain" in s
