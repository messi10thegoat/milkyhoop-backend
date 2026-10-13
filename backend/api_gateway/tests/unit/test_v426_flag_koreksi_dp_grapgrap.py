"""V426 (13 Okt 2026): koreksi_dp_proforma dinyalakan di grapgrap; pemindahan_barang TIDAK (putusan pemilik, Law 34)."""
from pathlib import Path

MIG = Path(__file__).resolve().parents[3] / "migrations"


def test_v426_hanya_koreksi_dp_proforma_dan_tak_bocor():
    s = (MIG / "V426__flag_koreksi_dp_proforma_grapgrap.sql").read_text()
    assert "'grapgrap-manado', 'koreksi_dp_proforma'" in s
    assert "'grapgrap-manado', 'pemindahan_barang'" not in s            # tak ada INSERT flag PB
    assert "feature = 'pemindahan_barang'" in s and "TIDAK boleh ada" in s   # guard: PB tak boleh ada di grapgrap
    assert "bocor ke tenant lain" in s and "'kaos-biru-konveksi', 'grapgrap-manado'" in s


def test_v426_rollback_hanya_grapgrap():
    r = (MIG / "V426__flag_koreksi_dp_proforma_grapgrap_ROLLBACK.sql").read_text()
    assert "DELETE FROM tenant_features" in r and "tenant_id = 'grapgrap-manado'" in r
    assert "koreksi_dp_proforma" in r and "pemindahan_barang" not in r.split("\n", 1)[1]
