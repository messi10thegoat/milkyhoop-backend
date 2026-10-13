"""Tipe pelanggan "Luar Negeri" dicabut (13 Okt 2026, keputusan pemilik; 0 baris di semua tenant).

customer_type hanya BADAN / ORANG_PRIBADI, di create DAN update -> LUAR_NEGERI = 422 (validasi pydantic).
Vendors tidak disentuh (vendor_type tetap tiga nilai). Tanpa migrasi."""
import pytest
from pydantic import ValidationError

from app.schemas.customers import CreateCustomerRequest, UpdateCustomerRequest
from app.schemas.vendors import CreateVendorRequest


@pytest.mark.parametrize("tipe", ["BADAN", "ORANG_PRIBADI"])
def test_create_menerima_dua_tipe(tipe):
    assert CreateCustomerRequest(name="PT Uji", customer_type=tipe).customer_type == tipe


def test_create_default_badan():
    assert CreateCustomerRequest(name="PT Uji").customer_type == "BADAN"


def test_create_luar_negeri_ditolak():
    with pytest.raises(ValidationError) as e:
        CreateCustomerRequest(name="PT Uji", customer_type="LUAR_NEGERI")
    assert "customer_type" in str(e.value)


@pytest.mark.parametrize("tipe", ["BADAN", "ORANG_PRIBADI", None])
def test_update_menerima_dua_tipe_atau_kosong(tipe):
    assert UpdateCustomerRequest(customer_type=tipe).customer_type == tipe


def test_update_luar_negeri_ditolak():
    with pytest.raises(ValidationError) as e:
        UpdateCustomerRequest(customer_type="LUAR_NEGERI")
    assert "customer_type" in str(e.value)


def test_vendor_tidak_tersentuh():
    assert CreateVendorRequest(name="V Uji", vendor_type="LUAR_NEGERI").vendor_type == "LUAR_NEGERI"
