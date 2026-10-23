"""V455 (kolom kind tautan GRN, aturan D): teks migrasi utuh (pelajaran V450) + aditif ber-DEFAULT + rollback berpenjaga."""
from pathlib import Path

M = Path(__file__).resolve().parents[3] / "migrations"


def test_v455_aditif_default_dan_check():
    s = (M / "V455__grn_tautan_kind.sql").read_text()
    rb = (M / "V455__grn_tautan_kind_ROLLBACK.sql").read_text()
    for t in (s, rb):
        assert t.count(chr(39)) % 2 == 0
    assert "DEFAULT 'terima_dulu'" in s and "NOT NULL" in s
    assert "'terima_dulu', 'tagih_dulu', 'legacy'" in s and "RAISE EXCEPTION" in s
    assert "ROLLBACK V455 ditolak" in rb
