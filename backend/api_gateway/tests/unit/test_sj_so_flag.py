"""R1 irisan 1: flag pengiriman_sebelum_faktur HANYA kaos (V416)."""
from pathlib import Path

APP = Path(__file__).resolve().parents[2]


def test_flag_hanya_kaos():
    s = next((APP.parent / "migrations").glob("V416__flag_pengiriman_sebelum_faktur_kaos.sql")).read_text()
    assert "'kaos-biru-konveksi'" in s and "bocor ke tenant lain" in s


def test_v425_flag_grapgrap_satu_flag_non_stok_saja():
    """13 Okt 2026: grapgrap = SATU flag (non-stok); flag stok TIDAK ada; guard peran + tak bocor ke tenant lain."""
    m = APP.parent / "migrations"
    s = next(m.glob("V425__flag_pengiriman_sebelum_faktur_grapgrap.sql")).read_text()
    assert "'grapgrap-manado', 'pengiriman_sebelum_faktur'" in s
    assert "feature = 'pengiriman_stok_sebelum_faktur'" in s and "TIDAK boleh ada" in s   # guard: flag stok tak boleh ada
    assert "GOODS_DELIVERED_NOT_INVOICED" in s and "bocor ke tenant lain" in s
    assert "pengiriman_stok_sebelum_faktur'\nWHERE" not in s   # tak ada INSERT flag stok
    r = next(m.glob("V425__flag_pengiriman_sebelum_faktur_grapgrap_ROLLBACK.sql")).read_text()
    assert "tenant_id = 'grapgrap-manado'" in r and "DELETE FROM tenant_features" in r   # rollback hanya grapgrap
