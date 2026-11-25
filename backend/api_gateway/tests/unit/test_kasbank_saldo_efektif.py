"""BUG-383 (putusan A pemilik, 25 Nov 2026): saldo Kas&Bank = jurnal EFEKTIF (is_effective_journal), SATU definisi dengan dashboard.
Kasus kaos: beban VOID + pembalik EXPENSE_REVERSAL POSTED -> neto 0 (dulu Kas&Bank menghitung pembalik tanpa aslinya: +65.218)."""
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
SALDO_SQL = {
    "services/kasbank_ringkas.py": 1, "services/kasbank_daftar.py": 1, "services/kasbank_tulis.py": 3,
    "routers/bank_accounts.py": 4, "routers/kasbank.py": 4,
}


def test_saldo_pakai_penentu_efektif_bukan_status_posted_saja():
    for rel, n in SALDO_SQL.items():
        s = (APP / rel).read_text()
        assert len(re.findall(r"is_effective_journal\(je\.id\)", s)) >= n, rel


def test_tak_ada_saldo_ringkasan_status_posted_saja():
    from app.services import kasbank_ringkas as KR, kasbank_daftar as KD, kasbank_tulis as KT
    for sql in (KR.SQL_RINGKASAN, KD.sql_saldo("ba"), KT.SQL_AKUN):
        assert "is_effective_journal(je.id)" in sql and "je.status = 'POSTED'" not in sql


def test_daftar_mutasi_lintas_akun_efektif_sama_dengan_saldo():
    """A1 (pemilik 25 Nov): daftar mutasi == saldo == dashboard. Kasus kaos: 16 beban VOID + pembalik POSTED tak tampil di daftar."""
    from app.services import kasbank_ringkas as KR
    for sql in (KR.SQL_MUTASI, KR.SQL_MUTASI_TANPA_BATAL, KR.SQL_RINGKASAN):
        assert "is_effective_journal(je.id)" in sql and "je.status = 'POSTED'" not in sql


def test_register_per_akun_saldo_dan_laporan_efektif():
    """-r4 (MASTER 26 Nov, pola ACCURATE/QuickBooks): register/saldo-by-id/statement/arus per akun KONSISTEN dengan saldo akun (efektif)."""
    for rel, n in (("routers/bank_accounts.py", 4), ("routers/kasbank.py", 8)):
        s = (APP / rel).read_text()
        assert len(re.findall(r"is_effective_journal\(je\.id\)", s)) >= n, rel
