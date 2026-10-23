"""V454 (flag penerimaan_barang_v1 kaos): teks migrasi utuh (pelajaran V450) + hanya kaos."""
from pathlib import Path

M = Path(__file__).resolve().parents[3] / "migrations"


def test_flag_kaos_saja_dan_kutip_utuh():
    s = (M / "V454__flag_penerimaan_barang_kaos.sql").read_text()
    rb = (M / "V454__flag_penerimaan_barang_kaos_ROLLBACK.sql").read_text()
    for t in (s, rb):
        assert t.count(chr(39)) % 2 == 0
        assert "'penerimaan_barang_v1'" in t and "'kaos-biru-konveksi'" in t
    assert "bocor ke tenant lain" in s and "RAISE EXCEPTION" in s


def test_gerbang_grn_tercatat():
    s = (M / "V454__flag_penerimaan_barang_kaos.sql").read_text()
    assert "GERBANG" in s and "feat/tagihan-grn" in s
