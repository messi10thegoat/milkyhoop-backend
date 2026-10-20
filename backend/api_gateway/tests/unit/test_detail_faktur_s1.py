"""S1 detail faktur (20 Okt 2026, MASTER): detail faktur setara baris daftar -- teks Posisi dari SISA JURNAL.

posisi_faktur: Draf | Batal | Lunas | Sebagian | Belum lunas (kata == kolom Status daftar FE r288). Telat bukan posisi.
Detail & daftar memanggil fungsi YANG SAMA (pemindai sumber di bawah); harness nyata: scripts/ops/uji-nyata-detail-faktur.py.
"""
import inspect
from decimal import Decimal

import pytest

from app.routers import sales_invoices as SI
from app.services import faktur_daftar as FD


@pytest.mark.parametrize("status,total,sisa,harap", [
    ("draft", 100, None, "Draf"),
    ("draft", 100, 100, "Draf"),
    ("void", 100, None, "Batal"),
    ("void", 100, 50, "Batal"),
    ("paid", 100, 0, "Lunas"),
    ("posted", 100, 0, "Lunas"),          # cache basi 'posted' tapi jurnal lunas -> jurnal menang
    ("partial", 100, None, "Lunas"),      # tanpa baris AR = lunas (aturan daftar: COALESCE 0)
    ("paid", 100, Decimal("-0.01"), "Lunas"),
    ("partial", 100, 40, "Sebagian"),
    ("posted", 100, Decimal("99.99"), "Sebagian"),  # cache 'posted' tapi sudah dibayar sebagian -> jurnal menang
    ("posted", 100, 100, "Belum lunas"),
    ("partial", Decimal("100.00"), Decimal("100.00"), "Belum lunas"),
])
def test_posisi_faktur(status, total, sisa, harap):
    assert FD.posisi_faktur(status, total, sisa) == harap
    assert harap in FD.POSISI_FAKTUR


def test_posisi_bukan_telat():
    # Telat ada di jatuh_tempo_keadaan, bukan di posisi (putusan MASTER 20 Okt).
    assert not any("telat" in k.lower() or "tempo" in k.lower() for k in FD.POSISI_FAKTUR)


def _sumber(fn):
    return inspect.getsource(fn)


def test_detail_dan_daftar_satu_sumber():
    d, l = _sumber(SI.get_invoice), _sumber(SI.list_invoices)
    for f in ("_fd.posisi_faktur(", "_fd.jatuh_tempo_hari(", "_fd.jatuh_tempo_minggu_ini(", "_fd.jatuh_tempo_keadaan(",
              "_fa.aksi_faktur(", "_fd.dokumen_per_faktur("):
        assert f in d and f in l, f
    for k in ('"position_text"', '"jatuh_tempo_terbuka"', '"jatuh_tempo_hari"', '"jatuh_tempo_minggu_ini"',
              '"jatuh_tempo_keadaan"', '"aksi"', '"documents"'):
        assert k in d and k in l, k
    # jatuh_tempo_terbuka detail = predikat SQL yang sama dengan daftar
    assert "_syarat_terbuka_ar()" in d and "_syarat_terbuka_ar()" in l


def test_skema_daftar_punya_position_text():
    from app.schemas.sales_invoices import InvoiceListItem
    assert "position_text" in InvoiceListItem.model_fields
