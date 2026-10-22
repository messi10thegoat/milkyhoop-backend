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


def test_pagar_ppn_dipanggil_di_posting_dan_terapkan():
    assert "ND.tolak_potongan_ber_ppn(" in inspect.getsource(VC.post_vendor_credit)
    src = inspect.getsource(ND.terapkan_nota_debit)
    assert src.index("tolak_potongan_ber_ppn(") < src.index("INSERT INTO vendor_credit_applications")


class _Conn:
    def __init__(self, pkp, ppn_tagihan=0):
        self.pkp, self.ppn = pkp, ppn_tagihan

    async def fetchval(self, q, *a):
        return self.pkp if "is_pkp" in q else self.ppn


@pytest.mark.parametrize("alasan,pkp,tax_nd,ppn_tagihan,tolak", [
    ("discount", False, 0, 0, False), ("discount", True, 0, 0, True), ("pricing_error", False, 1100, 0, True),
    ("discount", False, 0, 5000, True), ("return", True, 1100, 5000, False), ("discount", None, 0, 0, False)])
def test_pagar_ppn_kasus(alasan, pkp, tax_nd, ppn_tagihan, tolak):
    import asyncio
    from fastapi import HTTPException
    try:
        asyncio.run(ND.tolak_potongan_ber_ppn(_Conn(pkp, ppn_tagihan), "t", alasan, tax_nd, "b" if ppn_tagihan else None))
        kena = False
    except HTTPException as e:
        kena = e.status_code == 409 and e.detail["code"] == "ND_POTONGAN_PPN_BELUM"
    assert kena is tolak
