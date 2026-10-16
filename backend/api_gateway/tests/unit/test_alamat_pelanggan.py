"""V428 alamat pelanggan terstruktur (kontrak backend/docs/KONTRAK-alamat-terstruktur-20261016.md).
Uji murni + kabel. Perilaku DB (migrasi, cermin, indeks unik, trigger tenant, rollback) = scripts/gate_v428_alamat.sh."""
import inspect
import re

import pytest
from fastapi import HTTPException

from app.middleware import permission_middleware as PM
from app.routers import alamat_pelanggan as R
from app.routers import customers as CU
from app.services import alamat_pelanggan as AP
from app.services.penawaran_cetak import alamat_pelanggan as alamat_penawaran


@pytest.mark.parametrize("alamat,kota", [("Jl. A 1", None), ("Jl. A 1", "Manado"), ("Jl. A, Manado", "manado"),
                                         (None, "Tomohon"), ("  Jl. Tepi  ", None), ("Jl. B", "  ")])
def test_format_sama_dengan_aturan_penawaran(alamat, kota):
    a = {"addr1": alamat, "city": kota}
    assert AP.format_alamat(a) == alamat_penawaran(alamat, None, kota)


def test_format_lengkap_dan_negara():
    a = {"addr1": "Jl. A", "addr2": "Blok B", "city": "Bitung", "state": "Sulut", "zip": "95511", "country": "IDN"}
    assert AP.format_alamat(a) == "Jl. A, Blok B, Bitung, Sulut, 95511"
    assert AP.format_alamat({**a, "country": "SGP"}).endswith(", SGP")
    assert AP.format_alamat({}) is None and AP.format_alamat(None) is None


def test_jalan_cermin_apa_adanya():
    assert AP.jalan({"addr1": "  Jl. Tepi  "}) == "  Jl. Tepi  "          # byte: cermin kolom datar
    assert AP.jalan({"addr1": "A", "addr2": " ", "addr3": "C"}) == "A, C"
    assert AP.jalan({"addr1": None}) is None


def test_bersihkan_validasi():
    assert AP.bersihkan({"addr1": "  ", "country": "idn", "asing": 1}) == {"addr1": None, "country": "IDN"}
    with pytest.raises(HTTPException) as e:
        AP.bersihkan({"country": "ID"})
    assert e.value.status_code == 422
    with pytest.raises(HTTPException):
        AP.bersihkan({"zip": "1" * 21})



def test_izin_rute_alamat():
    src = inspect.getsource(PM)
    for pola, aksi in ((r'"\^/api/customers/\[\^/\]\+/addresses\$", \["GET"\], "customer", "R"', "R"),
                       (r'"\^/api/customers/\[\^/\]\+/addresses\$", \["POST"\], "customer", "U"', "U"),
                       (r'"\^/api/customers/\[\^/\]\+/addresses/\[\^/\]\+\$", \["PATCH", "DELETE"\], "customer", "U"', "U"),
                       (r'"\^/api/customers/\[\^/\]\+/addresses/\[\^/\]\+/default\$", \["POST"\], "customer", "U"', "U")):
        assert re.search(pola, src), pola
    # pola alamat WAJIB di atas pola umum /api/customers/[^/]+ (urutan pencocokan)
    assert src.index("/addresses$\", [\"GET\"]") < src.index("(r\"^/api/customers/[^/]+$\", [\"GET\"]")


def test_rute_tulis_di_inventaris():
    from pathlib import Path
    inv = (Path(__file__).parent / "inventaris_rute_tulis.txt").read_text()
    for r in ("DELETE /api/customers/{customer_id}/addresses/{address_id}",
              "PATCH /api/customers/{customer_id}/addresses/{address_id}",
              "POST /api/customers/{customer_id}/addresses",
              "POST /api/customers/{customer_id}/addresses/{address_id}/default"):
        assert r in inv.splitlines(), r


def test_kabel_cermin_di_tulis_lama_dan_gabung():
    assert "_alamat.cermin_dari_datar(conn, ctx[\"tenant_id\"], new_id" in inspect.getsource(CU.create_customer)
    up = inspect.getsource(CU.update_customer)
    assert '{"address", "city", "province", "postal_code"} & set(update_data)' in up
    assert up.index("await conn.execute(query, *params)") < up.index("cermin_dari_datar")   # dalam tx yang sama, sesudah UPDATE
    assert 'pindah["customer_addresses"] = await _alamat.pindah_saat_gabung' in inspect.getsource(CU.merge_customers)
    assert 'cacah["customer_addresses"]' in inspect.getsource(CU._gabung_rincian)


def test_rute_per_baris_tanpa_ganti_seluruh():
    jalur = {(r.path, tuple(sorted(r.methods))) for r in R.router.routes}
    assert jalur == {("/{customer_id}/addresses", ("GET",)), ("/{customer_id}/addresses", ("POST",)),
                     ("/{customer_id}/addresses/{address_id}", ("PATCH",)),
                     ("/{customer_id}/addresses/{address_id}", ("DELETE",)),
                     ("/{customer_id}/addresses/{address_id}/default", ("POST",))}
    assert "PUT" not in str(jalur)


def test_penulis_mengunci_pelanggan_dan_menormalkan_pelaku():
    for fn in (AP.cermin_dari_datar, AP.tambah, AP.ubah, AP.jadikan_bawaan, AP.nonaktifkan):
        s = inspect.getsource(fn)
        assert "await _kunci_pelanggan(conn, tenant_id, customer_id)" in s and "oleh = _teks(oleh)" in s, fn.__name__
    assert "CUSTOMER_ADDRESS:" in inspect.getsource(AP._kunci_pelanggan)
