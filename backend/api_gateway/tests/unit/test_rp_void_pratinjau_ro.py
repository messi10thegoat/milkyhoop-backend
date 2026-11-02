"""U-P4a (2 Nov 2026; disetujui MASTER): pratinjau void penerimaan READ ONLY TANPA kunci, penulis tak dijalankan; efek dari
fungsi MURNI yang SAMA dengan penulis (_baris_balik, status_bayar_faktur); kunci respons LAMA tetap + kunci pola Pembayaran
Keluar (aditif)."""
import inspect
from decimal import Decimal

from app.routers import receive_payments as RP
from app.services import pihak_helpers as PH


def test_baris_balik_menukar_debit_kredit_dan_memo():
    asli = [{"account_id": "a", "debit": Decimal("100"), "credit": Decimal("0"), "memo": "Kas"},
            {"account_id": "b", "debit": Decimal("0"), "credit": Decimal("100"), "memo": None}]
    assert RP._baris_balik(asli) == [{"account_id": "a", "debit": Decimal("0"), "credit": Decimal("100"), "memo": "Reversal - Kas"},
                                     {"account_id": "b", "debit": Decimal("100"), "credit": Decimal("0"), "memo": "Reversal - "}]


def test_status_bayar_faktur_satu_aturan():
    assert PH.status_bayar_faktur(Decimal("1000"), Decimal("0")) == (Decimal("1000"), "paid")
    assert PH.status_bayar_faktur(Decimal("1000"), Decimal("600")) == (Decimal("400"), "partial")
    assert PH.status_bayar_faktur(Decimal("1000"), Decimal("1000")) == (Decimal("0"), "posted")
    assert PH.status_bayar_faktur(Decimal("1000"), Decimal("-5"))[1] == "paid"
    assert "status_bayar_faktur(f[\"total_amount\"], out)" in inspect.getsource(PH.segarkan_cache_piutang_faktur)


def test_penulis_memakai_fungsi_murni_yang_sama():
    s = inspect.getsource(RP._tulis_void_pembayaran)
    assert s.count("_baris_balik(") == 2  # jurnal pembayaran + jurnal lepas LPS


def test_pratinjau_baca_saja_tanpa_kunci_tanpa_penulis():
    s = inspect.getsource(RP.preview_void_receive_payment)
    assert "conn.transaction(readonly=True)" in s and "kunci=False" in s
    for dilarang in ("pg_advisory", "FOR UPDATE", "_tulis_void_pembayaran(", "tr.rollback"):
        assert dilarang not in s, dilarang
    assert "_baris_balik(asli)" in s and "status_bayar_faktur(" in s
    r = inspect.getsource(RP._rencana_void_pembayaran)
    assert '" FOR UPDATE" if kunci else ""' in r and "dep_ids if kunci else []" in r


def test_kunci_respons_lama_tetap_dan_kunci_baru():
    s = inspect.getsource(RP.preview_void_receive_payment)
    for k in ("can_void", "journal_lines", "invoices_reopened", "bank_reversal", "overpay_deposit", "unapply_deposits",
              "source_deposit", "reversal_date", "payment_number", "customer_name", "preview",
              "action", "can_proceed", "before", "after", "journals", "bank_transactions", "invoices", "payload"):
        assert f'"{k}":' in s, k


def test_penulis_void_tanpa_raise_sendiri_semua_penghalang_di_penentu():
    """VOID_REJECTED lama (galat dari penulis) hilang di pratinjau READ ONLY: buktikan penulis TAK punya raise sendiri --
    setiap penolakan ada di penentu _rencana_void_pembayaran (blok berkode). Pemanggil penulis: _baris_balik (murni),
    create_reversal_bank_transaction (raise hanya bila reversal_journal_id kosong -- selalu diisi), segarkan_cache_piutang_faktur
    (tanpa raise)."""
    import ast
    src = inspect.getsource(RP._tulis_void_pembayaran)
    pohon = ast.parse(src.replace("\n    ", "\n", 0))
    assert [n for n in ast.walk(pohon) if isinstance(n, ast.Raise)] == []
    assert "reversal_journal_id=void_journal_id" in src
    import app.services.pihak_helpers as PHx
    assert "raise" not in inspect.getsource(PHx.segarkan_cache_piutang_faktur)
