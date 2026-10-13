"""V420 (13 Okt 2026, W-20261013-IDPAJAK): identitas pajak pembeli di master pelanggan.

Latar: form Pelanggan mengirim nitku/jenis_id/negara/nomor_dokumen; skema membuangnya DIAM (kolom hilang bersama DB lama,
tak masuk resep pemulihan) dan detail selalu "—". Kini skema menerima + memvalidasi, INSERT/UPDATE menulis, GET
detail/daftar mengembalikan.
"""
import inspect
import re
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.routers import customers as cu
from app.schemas.customers import (
    CreateCustomerRequest, CustomerDetail, CustomerListItem, UpdateCustomerRequest,
)

KUNCI = ("nitku", "jenis_id", "negara", "nomor_dokumen")
NITKU = "0123456789012345000000"


def test_create_menerima_dan_menormalkan():
    b = CreateCustomerRequest(name="X", nitku="01.234.567.8-901.2345 000000", jenis_id="Passport",
                              negara=" sgp ", nomor_dokumen=" A123 ")
    assert (b.nitku, b.jenis_id, b.negara, b.nomor_dokumen) == (NITKU, "Passport", "SGP", "A123")
    assert all(k in b.model_dump() for k in KUNCI)


def test_string_kosong_jadi_none():
    b = CreateCustomerRequest(name="X", nitku="", jenis_id="", negara="", nomor_dokumen="  ")
    assert (b.nitku, b.jenis_id, b.negara, b.nomor_dokumen) == (None, None, None, None)


@pytest.mark.parametrize("medan,nilai", [
    ("nitku", "123"), ("nitku", "0123456789012345") , ("nitku", "01234567890123450000a0"),
    ("negara", "Indonesia"), ("negara", "ID"), ("negara", "1DN"), ("jenis_id", "KTP"),
])
def test_nilai_tak_sah_422(medan, nilai):
    with pytest.raises(ValidationError):
        CreateCustomerRequest(name="X", **{medan: nilai})
    with pytest.raises(ValidationError):
        UpdateCustomerRequest(**{medan: nilai})


@pytest.mark.parametrize("jenis", ["Passport", "Other"])
def test_passport_other_wajib_nomor_dokumen(jenis):
    with pytest.raises(ValidationError):
        CreateCustomerRequest(name="X", jenis_id=jenis)
    with pytest.raises(ValidationError):
        UpdateCustomerRequest(jenis_id=jenis, nomor_dokumen="")
    assert UpdateCustomerRequest(jenis_id=jenis, nomor_dokumen="P1").nomor_dokumen == "P1"


def test_update_hanya_nomor_dokumen_lolos_skema_dan_terkirim():
    u = UpdateCustomerRequest(nomor_dokumen="P9")
    assert u.model_dump(exclude_unset=True) == {"nomor_dokumen": "P9"}


def test_update_tak_mengirim_kunci_yang_tak_diset():
    assert UpdateCustomerRequest(name="Y").model_dump(exclude_unset=True) == {"name": "Y"}


@pytest.mark.parametrize("jenis,dok,tolak", [
    ("Passport", None, True), ("Other", "  ", True), ("Passport", "P1", False), ("TIN", None, False), (None, None, False),
])
def test_pagar_gabungan_patch(jenis, dok, tolak):
    if tolak:
        with pytest.raises(HTTPException) as e:
            cu.tolak_identitas_pajak_tak_lengkap(jenis, dok)
        assert e.value.status_code == 422 and e.value.detail["code"] == "IDENTITAS_PAJAK_TIDAK_LENGKAP"
        assert e.value.detail["field"] == "nomor_dokumen"
    else:
        cu.tolak_identitas_pajak_tak_lengkap(jenis, dok)


def test_patch_memanggil_pagar_gabungan_dengan_nilai_db():
    src = inspect.getsource(cu.update_customer)
    assert "tolak_identitas_pajak_tak_lengkap(" in src
    assert 'current["jenis_id"]' in src and 'current["nomor_dokumen"]' in src
    # nilai sekarang wajib membaca kolom V420
    assert re.search(r"phone2, community, nitku, jenis_id, negara, nomor_dokumen\s+FROM customers WHERE id = \$1", src)


def test_insert_create_kolom_placeholder_argumen_sejajar():
    src = inspect.getsource(cu.create_customer)
    m = re.search(r"INSERT INTO customers \((.*?)\)\s*VALUES \((.*?)\)", src, re.S)
    kolom = [k.strip() for k in m.group(1).split(",")]
    tempat = re.findall(r"\$(\d+)", m.group(2))
    assert len(kolom) == len(tempat) == 33 and tempat == [str(i) for i in range(1, 34)]
    assert kolom[29:] == list(KUNCI)
    for i, k in enumerate(KUNCI, start=30):
        assert f"body.{k},  # ${i}" in src


def test_respons_detail_dan_daftar_tak_membuang_kunci():
    # response_model memangkas kunci yang tak dideklarasikan -> wajib ada di model respons
    for model in (CustomerDetail, CustomerListItem):
        assert all(k in model.model_fields for k in KUNCI), model.__name__


def test_get_detail_dan_daftar_select_dan_memetakan():
    for fn in (cu.get_customer, cu.list_customers):
        src = inspect.getsource(fn)
        assert "community, nitku, jenis_id, negara, nomor_dokumen" in src, fn.__name__
        for k in KUNCI:
            assert f'"{k}": row["{k}"]' in src, (fn.__name__, k)


def test_migrasi_v420_aditif_nullable_dengan_check():
    mig = Path(__file__).resolve().parents[3] / "migrations" / "V420__identitas_pajak_pelanggan_pemasok.sql"
    sql = mig.read_text()
    for k in KUNCI:
        assert f"ALTER TABLE customers ADD COLUMN IF NOT EXISTS {k} " in sql
    for k in ("nitku", "negara"):
        assert f"ALTER TABLE vendors ADD COLUMN IF NOT EXISTS {k} " in sql
    tambah = [b for b in sql.splitlines() if "ADD COLUMN" in b]
    assert len(tambah) == 6 and not any("NOT NULL" in b or "DEFAULT" in b.upper() for b in tambah)
    for c in ("customers_nitku_22_digit", "customers_jenis_id_sah", "customers_negara_iso3",
              "customers_nomor_dokumen_wajib", "vendors_nitku_22_digit", "vendors_negara_iso3"):
        assert re.search(rf"ADD CONSTRAINT {c}\s+CHECK", sql), c
    assert "UPDATE " not in sql  # nol backfill


def test_mixin_dasar_lintas_modul_hanya_nitku_negara():
    # Dipakai ulang modul lain (pemasok, VENDORFIELD): hanya nitku + negara, tanpa aturan jenis_id/nomor_dokumen
    from app.schemas.identitas_pajak import IdentitasPajakDasar, IdentitasPajakPembeli

    class BadanPemasok(IdentitasPajakDasar):
        name: str

    b = BadanPemasok(name="V", nitku="0123.4567.8901.2345-000000", negara="idn")
    assert (b.nitku, b.negara) == (NITKU, "IDN")
    assert set(IdentitasPajakDasar.model_fields) == {"nitku", "negara"}
    assert set(IdentitasPajakPembeli.model_fields) == set(KUNCI)
    with pytest.raises(ValidationError):
        BadanPemasok(name="V", nitku="12")
    assert issubclass(CreateCustomerRequest, IdentitasPajakPembeli) and issubclass(UpdateCustomerRequest, IdentitasPajakPembeli)
