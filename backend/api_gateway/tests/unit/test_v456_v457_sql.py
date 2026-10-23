"""V456 (AR refund NK) & V457 (AP saldo awal + bayar yatim): teks utuh (pelajaran V450), tambalan dari badan SEKARANG (bukan CREATE OR REPLACE
badan lama, pelajaran V452), anchor gagal-keras, idempoten, rollback kebalikan persis."""
from pathlib import Path

import pytest

M = Path(__file__).resolve().parents[3] / "migrations"
BERKAS = ["V456__ar_refund_nota_kredit", "V457__ap_saldo_awal_dan_bayar_yatim"]


@pytest.mark.parametrize("nama", BERKAS)
def test_teks_utuh_dan_pola_tambal(nama):
    s = (M / f"{nama}.sql").read_text()
    rb = (M / f"{nama}_ROLLBACK.sql").read_text()
    for t in (s, rb):
        assert t.count(chr(39)) % 2 == 0
        assert "pg_get_functiondef(" in t and "EXECUTE v_baru" in t
    assert "CREATE OR REPLACE FUNCTION" not in s  # badan lama tak pernah ditimpa
    assert "RAISE EXCEPTION" in s and "sudah terpasang, lewati" in s and "RAISE EXCEPTION" in rb


def test_v456_refund_masuk_cabang_nk_tak_tertaut():
    s = (M / "V456__ar_refund_nota_kredit.sql").read_text()
    assert "''CREDIT_NOTE'', ''CREDIT_NOTE_REFUND''" in s and "cn.original_invoice_id IS NULL" in s


def test_v457_dua_baris_sintetis():
    s = (M / "V457__ap_saldo_awal_dan_bayar_yatim.sql").read_text()
    assert "''OPENING-BALANCE''" in s and "''VENDOR-PAYMENT''" in s and "VENDOR_OPENING_BALANCE" in s
    assert "x.bill_id NOT IN (SELECT ab.id FROM active_bills ab)" in s  # atribusi pd_bagian yang sama
