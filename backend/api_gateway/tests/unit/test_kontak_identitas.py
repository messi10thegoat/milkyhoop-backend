"""identitas_text detail pelanggan + vendor (MASTER 9 Nov, pemilik setuju): satu baris "<kode> · <HP> · <kota> · <termin>" dari SATU penentu
(kontak_daftar.identitas_text); bagian kosong dilewati; HP = penentu kontak_teks (vendor mobile_phone ?? phone); termin "Tunai" bila 0."""
from pathlib import Path

from app.schemas.customers import CustomerDetail
from app.schemas.vendors import VendorDetail
from app.services import kontak_daftar as KD

APP = Path(KD.__file__).resolve().parents[1]


def test_vendor_lengkap_hp_dulu():
    v = {"code": "V0003", "mobile_phone": "081243065632", "phone": "0431 862100", "city": "Manado", "payment_terms_days": 30}
    assert KD.identitas_text("vendor", v) == "V0003 · 0812 4306 5632 · Manado · Termin 30 hari"
    assert KD.identitas_text("vendor", {**v, "mobile_phone": None}) == "V0003 · 0431 862100 · Manado · Termin 30 hari"


def test_bagian_kosong_dilewati_dan_tunai():
    assert KD.identitas_text("vendor", {"code": None, "mobile_phone": " ", "phone": None, "city": "", "payment_terms_days": 0}) == "Tunai"
    assert KD.identitas_text("customer", {"nomor_member": "C0007", "telepon": None, "city": "Bitung", "payment_terms_days": None}) \
        == "C0007 · Bitung · Tunai"


def test_pelanggan_hp_sama_dengan_kontak_teks():
    c = {"nomor_member": "C0001", "telepon": "081299990000", "mobile_phone": "085211112222", "city": None, "payment_terms_days": 14}
    assert KD.identitas_text("customer", c) == "C0001 · 0812 9999 0000 · Termin 14 hari"
    assert KD.medan_baris("customer", {**c, "id": "x", "is_active": True}, {})["kontak_teks"].startswith("0812 9999 0000")


def test_detail_memuat_medan_aditif():
    assert "identitas_text" in CustomerDetail.model_fields and "identitas_text" in VendorDetail.model_fields
    for berkas, jenis in (("routers/customers.py", "customer"), ("routers/vendors.py", "vendor")):
        assert f"\"identitas_text\": KD.identitas_text(\"{jenis}\", row)" in (APP / berkas).read_text()
