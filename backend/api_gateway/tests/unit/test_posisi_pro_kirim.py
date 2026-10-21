"""position_text Proforma + Pengiriman (21 Okt 2026, putusan MASTER): SATU fungsi untuk baris daftar, detail, dan anchor bundel."""
import inspect
from decimal import Decimal

import pytest

from app.routers import deliveries as DL
from app.routers import dokumen as DK
from app.routers import proformas as PF
from app.services import pengiriman_daftar as PG
from app.services import proforma_daftar as PD


def _inline_lama(status, nominal, terbayar):
    """Teks anchor proforma SEBELUM unit ini (dokumen.py inline) -- fungsi baru wajib identik."""
    from app.services.proforma_aksi import proforma_lunas
    paid = Decimal(str(terbayar))
    return {"draft": "Draf", "cancelled": "Dibatalkan", "expired": "Kedaluwarsa"}.get(status) or (
        "Lunas" if proforma_lunas(nominal, paid) else ("Dibayar sebagian" if paid > 0 else "Menunggu bayar"))


@pytest.mark.parametrize("status", ["draft", "issued", "cancelled", "expired"])
@pytest.mark.parametrize("nominal,terbayar", [(1000, 0), (1000, 1), (1000, "999.995"), (1000, "999.99"), (1000, 1000), (1000, 1500), (0, 0)])
def test_proforma_position_text_identik_dengan_inline_lama(status, nominal, terbayar):
    assert PD.position_text(status, nominal, terbayar) == _inline_lama(status, nominal, terbayar)


def test_proforma_position_text_kata():
    assert PD.position_text("issued", Decimal("100"), None) == "Menunggu bayar"
    assert PD.position_text("issued", Decimal("100"), Decimal("40")) == "Dibayar sebagian"
    assert PD.position_text("issued", Decimal("100"), Decimal("100")) == "Lunas"
    assert PD.position_text("cancelled", Decimal("100"), Decimal("100")) == "Dibatalkan"


@pytest.mark.parametrize("status,serah,harap", [
    ("voided", "—", "Dibatalkan"), ("voided", "Belum diterima", "Dibatalkan"),
    ("posted", "Serah belum dicatat", "Serah belum dicatat"), ("posted", "Diterima 17 Okt", "Diterima 17 Okt"), ("posted", "—", "—")])
def test_pengiriman_position_text(status, serah, harap):
    assert PG.position_text(status, serah) == harap


def test_serah_teks_tetap_untuk_voided():
    # serah_teks = kolom Serah, TIDAK ikut berubah (putusan MASTER)
    assert PG.serah_fakta(True, "voided", None, None, 0)["serah_teks"] == "—"


def test_satu_fungsi_dipakai_baris_detail_anchor():
    src_pf, src_dl = inspect.getsource(PF), inspect.getsource(DL)
    assert src_pf.count("_pd.position_text(") == 2  # daftar + detail
    assert src_dl.count("_pg.position_text(") == 2  # daftar + detail
    assert "PD.position_text(" in inspect.getsource(DK.susun_dokumen_proforma)
    assert "PG.position_text(" in inspect.getsource(DK.susun_dokumen_pengiriman)
    # teks tak lagi ditulis inline di anchor
    assert '"Menunggu bayar"' not in inspect.getsource(DK.susun_dokumen_proforma)
    assert 'fakta["serah_teks"], "total"' not in inspect.getsource(DK.susun_dokumen_pengiriman)
