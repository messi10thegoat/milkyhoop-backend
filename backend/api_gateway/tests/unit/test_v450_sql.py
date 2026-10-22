"""V450 (pengingat_segera_hari): teks SQL utuh -- literal ber-kutip (23 Okt: salinan pertama kehilangan kutip tunggal lewat ssh -> pasti gagal)."""
from pathlib import Path

M = Path(__file__).resolve().parents[3] / "migrations"


def test_v450_literal_berkutip_dan_pagar():
    s = (M / "V450__pengingat_segera_hari.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS pengingat_segera_hari integer NOT NULL DEFAULT 7" in s
    assert "conname = 'chk_pengingat_segera_hari'" in s
    assert "CHECK (pengingat_segera_hari BETWEEN 1 AND 60)" in s
    assert "RAISE EXCEPTION 'V450: nilai awal harus 7 semua'" in s
    assert s.count("'") % 2 == 0


def test_v450_rollback_membuang_keduanya():
    r = (M / "V450__pengingat_segera_hari_ROLLBACK.sql").read_text()
    assert "DROP CONSTRAINT IF EXISTS chk_pengingat_segera_hari" in r
    assert "DROP COLUMN IF EXISTS pengingat_segera_hari" in r
