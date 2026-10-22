"""Nota Debit U3b (putusan pemilik 22 Okt): potongan/koreksi harga -> PURCHASE_DISCOUNT (5-10200), tanpa gerak stok; retur tetap. Nyata: uji-nyata-nota-debit-u3b.sh."""
import inspect

import pytest

from app.routers import credit_notes as CN
from app.routers import vendor_credits as VC
from app.services import nota_debit as ND


@pytest.mark.parametrize("alasan,peran", [("discount", "PURCHASE_DISCOUNT"), ("pricing_error", "PURCHASE_DISCOUNT"),
                                          ("return", "COGS_PURCHASE_RETURN"), ("damaged", "COGS_PURCHASE_RETURN"), ("other", "COGS_PURCHASE_RETURN")])
def test_peran_kredit_menurut_alasan(alasan, peran):
    assert ND.peran_kredit_nd(alasan) == peran


def test_alasan_potongan_simetris_nk():
    assert ND.ALASAN_POTONGAN == CN.ALASAN_POTONGAN


def test_posting_potongan_tanpa_stok_dan_akun_per_peran():
    src = inspect.getsource(VC.post_vendor_credit)
    assert 'potongan = vc["reason"] in ND.ALASAN_POTONGAN' in src
    assert "ND.peran_kredit_nd(vc[\"reason\"])" in src
    assert "for item in ([] if potongan else vc_items):" in src
