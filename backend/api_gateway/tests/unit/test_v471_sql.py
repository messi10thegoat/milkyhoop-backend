"""V471 (2 Nov 2026, peran EXPENSE_OTHER untuk catat biaya Milkybook v2): teks migrasi utuh + pola anchor (pelajaran V450: ssh membuang
kutip tunggal diam-diam). Bukti DB = gerbang Postgres sekali-pakai dari cadangan (terapkan, ulang, pembalik, terapkan lagi)."""
from pathlib import Path

M = Path(__file__).resolve().parents[3] / "migrations"
MAJU, BALIK = "V471__peran_beban_lain.sql", "V471__peran_beban_lain_ROLLBACK.sql"


def _teks(nama):
    return (M / nama).read_text()


def test_kutip_genap_tanpa_transaksi_eksplisit_tanpa_ganti_fungsi():
    for nama in (MAJU, BALIK):
        s = _teks(nama)
        assert s.count(chr(39)) % 2 == 0, nama
        assert "\nBEGIN;" not in s and "\nCOMMIT;" not in s, nama  # apply_mig.sh: -1 + tolak BEGIN/COMMIT
        assert "CREATE OR REPLACE" not in s and "VNNN" not in s, nama  # fungsi ditambal dari badan SEKARANG


def test_anchor_semai_dan_penjaga_gagal_keras():
    s = _teks(MAJU)
    anchor = "(''BANK_ADJUSTMENT_LOSS'',       ''5-20900'', false, ''V444 Selisih kurang penyesuaian saldo bank.''),"
    assert s.count(anchor) == 2  # dicari + dipertahankan di pengganti
    assert "(''EXPENSE_OTHER'',              ''5-20900'', false, ''V471 Beban lain-lain bawaan catat biaya.'')," in s
    for pesan in ("anchor BANK_ADJUSTMENT_LOSS tak ditemukan", "CHECK role_key tidak memuat nilai lama + baru",
                  "baris peran baru % <> celah", "chart_of_accounts BERUBAH", "account_roles selain EXPENSE_OTHER BERUBAH",
                  "pemetaan BANK_ADJUSTMENT_LOSS BERUBAH", "tidak memuat baris lama + baru", "jumlah jurnal berubah", "DILEWATI"):
        assert pesan in s, pesan
    assert "INSERT INTO chart_of_accounts" not in s  # MASTER: tenant tanpa akun dilewati, TIDAK dibuatkan akun


def test_pembalik_hanya_baris_v471():
    s = _teks(BALIK)
    assert "DELETE FROM account_roles WHERE role_key = 'EXPENSE_OTHER' AND notes LIKE 'V471%';" in s
    assert "chart_of_accounts" not in s and "journal" not in s

