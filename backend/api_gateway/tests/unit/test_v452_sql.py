"""V452 (GRN, dulu V439 -- dinomori ulang 23 Okt, isi sama): teks migrasi utuh (pelajaran V450: ssh membuang kutip tunggal diam-diam)."""
from pathlib import Path

M = Path(__file__).resolve().parents[3] / "migrations"


def _teks(nama):
    return (M / nama).read_text()


def test_v452_kutip_genap_dan_literal_berkutip():
    for nama in ("V452__penerimaan_barang_grni.sql", "V452__penerimaan_barang_grni_ROLLBACK.sql"):
        s = _teks(nama)
        assert s.count(chr(39)) % 2 == 0, nama
    s = _teks("V452__penerimaan_barang_grni.sql")
    for lit in ("2-10150", "5-10400", "Barang Diterima Belum Ditagih", "GOODS_RECEIPT", "GOODS_RECEIPT_REVERSAL",
                "account_roles_role_key_check", "V452: anchor AP_TRADE tak ditemukan di seed_default_account_roles"):
        assert lit in s, lit


def test_v452_tanpa_sisa_nomor_lama_dan_anchor_gagal_keras():
    s = _teks("V452__penerimaan_barang_grni.sql")
    assert "V439" not in s
    assert s.count("RAISE EXCEPTION") >= 8  # tiap anchor/verifikasi gagal-keras
    assert "CREATE OR REPLACE" not in s  # fungsi ditambal dari badan SEKARANG (pg_get_functiondef), tak menimpa badan lama
