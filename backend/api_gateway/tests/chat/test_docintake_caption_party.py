"""fix/docintake-caption-party — unit murni (tanpa DB) untuk pemilih FIFO dan
ekstraksi nama pihak. Tiap aturan punya kasus merah (assert None) dan hijau."""
from datetime import date
from decimal import Decimal

import pytest

from app.services.unified_agent.document_intake import pilih_dokumen_fifo
from app.services.unified_agent.document_intake_v3.signals import extract_party_name
from app.services.unified_agent.document_matcher import MatchCandidate


def _c(label, outstanding, due):
    return MatchCandidate(
        source_type="bill", source_id=label, label=label, counterparty="X",
        amount=Decimal(outstanding), outstanding=Decimal(outstanding), due_date=due,
    )


def test_kosong():
    assert pilih_dokumen_fifo([], 1000) == (None, "kosong")


def test_tunggal_parsial_sah():
    c = _c("INV-1", "4290000", date(2026, 9, 2))
    assert pilih_dokumen_fifo([c], 165000) == (c, "tunggal")


def test_tunggal_tanpa_nominal_tetap_dipilih():
    c = _c("INV-1", "4290000", date(2026, 9, 2))
    assert pilih_dokumen_fifo([c], None) == (c, "tunggal")


def test_tunggal_melebihi_tidak_dipasang():
    c = _c("INV-1", "4290000", date(2026, 9, 2))
    assert pilih_dokumen_fifo([c], 9000000) == (None, "melebihi")


def test_fifo_pilih_paling_tua_yang_cukup():
    a = _c("PB-0015", "1235000", date(2026, 9, 14))
    b = _c("PB-0001", "3125000", date(2026, 10, 2))
    d = _c("PB-0011", "6250000", date(2026, 10, 2))
    best, status = pilih_dokumen_fifo([d, b, a], 100000)  # urutan acak masuk
    assert (best.label, status) == ("PB-0015", "fifo")


def test_fifo_lewati_yang_tak_cukup():
    a = _c("PB-0015", "1235000", date(2026, 9, 14))
    b = _c("PB-0001", "3125000", date(2026, 10, 2))
    best, status = pilih_dokumen_fifo([a, b], 2000000)
    assert (best.label, status) == ("PB-0001", "fifo")


def test_seri_due_date_ambigu_tidak_dipasang():
    a = _c("PB-0015", "1235000", date(2026, 9, 14))
    b = _c("PB-0001", "3125000", date(2026, 10, 2))
    d = _c("PB-0011", "6250000", date(2026, 10, 2))
    assert pilih_dokumen_fifo([a, b, d], 2000000) == (None, "ambigu_seri")


def test_banyak_tanpa_nominal_ambigu():
    a = _c("A", "1", date(2026, 1, 1)); b = _c("B", "1", date(2026, 2, 1))
    assert pilih_dokumen_fifo([a, b], None) == (None, "tanpa_nominal")


def test_banyak_semua_kurang_melebihi():
    a = _c("A", "100", date(2026, 1, 1)); b = _c("B", "200", date(2026, 2, 1))
    assert pilih_dokumen_fifo([a, b], 500) == (None, "melebihi")


@pytest.mark.parametrize("caption,direction,expect", [
    ("ada pembayaran dari Ferrenlita Pesan Pisang, tolong catat.", "in", "Ferrenlita Pesan Pisang"),
    ("pembayaran dari Pelanggan Tidak Ada", "in", "Tidak Ada"),  # kata pelanggan sengaja ditelan (FIX_PARTY_KEYWORD_OPTIONAL)
    ("bayar ke PT Grosir Kaos 100rb", "out", "PT Grosir Kaos"),
    ("bayar ke PT Grosir Kaos Rp 100.000", "out", "PT Grosir Kaos"),
    ("bayar ke PT Grosir Kaos, cicilan", "out", "PT Grosir Kaos"),
    ("tolong catat ya", "in", None),
])
def test_extract_party_name(caption, direction, expect):
    assert extract_party_name(caption, direction) == expect
