"""V453 (flag dashboard_v2_insight kaos): teks migrasi utuh (pelajaran V450) + hanya kaos."""
from pathlib import Path

M = Path(__file__).resolve().parents[3] / "migrations"


def test_flag_kaos_saja_dan_kutip_utuh():
    s = (M / "V453__flag_dashboard_v2_insight_kaos.sql").read_text()
    rb = (M / "V453__flag_dashboard_v2_insight_kaos_ROLLBACK.sql").read_text()
    for t in (s, rb):
        assert t.count(chr(39)) % 2 == 0
        assert "'dashboard_v2_insight'" in t and "'kaos-biru-konveksi'" in t
    assert "bocor ke tenant lain" in s and "RAISE EXCEPTION" in s
