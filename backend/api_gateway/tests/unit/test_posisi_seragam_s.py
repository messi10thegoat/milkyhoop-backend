"""Celah S (standar SO v3): position_text di baris daftar + detail Pembayaran Masuk, NK, ND, Pelanggan, Vendor -- DIDEKLARASIKAN di
response_model (pola BUG-097). NK/ND/Pelanggan/Vendor = alias status_text (nilai sama); Pembayaran Masuk = posisi_terima (cermin BP)."""
import pytest

from app.schemas import credit_notes as CN, customers as CU, receive_payments as RP, vendor_credits as VC, vendors as VE
from app.services import penerimaan_daftar as PD

KELAS_STATUS = [CN.CreditNoteListItem, CN.CreditNoteDetail, VC.VendorCreditListItem, VC.VendorCreditDetail,
                CU.CustomerListItem, CU.CustomerDetail, VE.VendorListItem, VE.VendorDetail]
KELAS_RP = [RP.ReceivePaymentListItem, RP.ReceivePaymentDetail]


@pytest.mark.parametrize("k", KELAS_STATUS + KELAS_RP, ids=lambda k: k.__name__)
def test_position_text_dideklarasikan(k):
    assert "position_text" in k.model_fields


def _isi_wajib(k, **lain):
    """Nilai minimal medan wajib (tanpa default) supaya model_construct+validate bisa dipakai."""
    d = {}
    for n, f in k.model_fields.items():
        if f.is_required():
            a = str(f.annotation)
            d[n] = False if "bool" in a else (0 if ("float" in a or "int" in a) and "str" not in a else ("2026-11-07" if "date" in a else "x"))
    d.update(lain)
    return d


@pytest.mark.parametrize("k", KELAS_STATUS, ids=lambda k: k.__name__)
def test_alias_status_text_nilai_sama(k):
    m = k.model_validate(_isi_wajib(k, status_text="Terpakai sebagian"))
    assert m.model_dump()["position_text"] == "Terpakai sebagian"


def test_posisi_terima_cermin_bp():
    assert PD.posisi_terima("draft", None) == "Draf"
    assert PD.posisi_terima("voided", "Belum dialokasikan") == "Batal"
    assert PD.posisi_terima("posted", "Sebagian dialokasikan") == "Sebagian dialokasikan"
    assert PD.posisi_terima("posted", None) == "—"


@pytest.mark.parametrize("k", KELAS_RP, ids=lambda k: k.__name__)
def test_pembayaran_masuk_position_text_di_respons(k):
    m = k.model_validate(_isi_wajib(k, status="posted", alokasi_teks="Teralokasi penuh", alokasi_keadaan="penuh"))
    assert m.model_dump()["position_text"] == "Teralokasi penuh"
