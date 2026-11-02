"""U-P4b-2 (2 Nov 2026; disetujui MASTER): pratinjau POSTING / HAPUS draf penerimaan READ ONLY tanpa kunci; kalimat penolakan
SATU sumber dengan tulis; efek posting dari fungsi murni yang sama dengan _post_payment."""
import inspect

from app.routers import receive_payments as RP
from app.services import teks_galat as tg


def test_kalimat_satu_sumber_tulis_dan_pratinjau():
    assert RP.KALIMAT_HAPUS_BUKAN_DRAF == ("Hanya penerimaan berstatus Draf yang bisa dihapus. Penerimaan yang sudah diterima dibatalkan, "
                                           "bukan dihapus.")
    assert RP._kalimat_tak_bisa_posting("posted") == tg.tak_bisa_status("rp", "posted", "diposting")
    assert "detail=KALIMAT_HAPUS_BUKAN_DRAF" in inspect.getsource(RP.delete_receive_payment)
    assert 'detail=_kalimat_tak_bisa_posting(payment["status"])' in inspect.getsource(RP._post_payment)
    pv = inspect.getsource(RP._pratinjau_draf_penerimaan)
    assert '"message": KALIMAT_HAPUS_BUKAN_DRAF' in pv and '"message": _kalimat_tak_bisa_posting(p["status"])' in pv


def test_pratinjau_draf_baca_saja_tanpa_kunci_efek_dari_fungsi_murni():
    pv = inspect.getsource(RP._pratinjau_draf_penerimaan)
    for f in ("_baris_debit_penerimaan(", "_baris_diskon_penerimaan(", "_baris_piutang_penerimaan(", "_baris_kelebihan_penerimaan(",
              "_faktur_sesudah_penerimaan(alok, False)", "check_period_is_open(", "_perkiraan_nomor_ovp("):
        assert f in pv, f
    for dilarang in ("_post_payment(", "pg_advisory_xact_lock(", "FOR UPDATE", "DELETE FROM", "INSERT INTO", "UPDATE receive_payments"):
        assert dilarang not in pv, dilarang
    b = inspect.getsource(RP._baca_draf)
    assert "conn.transaction(readonly=True)" in b
    assert '"post")' in inspect.getsource(RP.preview_post_receive_payment) and '"delete")' in inspect.getsource(RP.preview_delete_receive_payment)


def test_perkiraan_ovp_satu_sumber():
    assert "await _perkiraan_nomor_ovp(conn, tid)" in inspect.getsource(RP.rencana_penerimaan_ro)
    assert "OVP-{bl['yymm']}-{((last or 0) + 1):04d}" in inspect.getsource(RP._perkiraan_nomor_ovp)
