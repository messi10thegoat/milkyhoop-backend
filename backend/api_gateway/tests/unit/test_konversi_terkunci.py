"""P1 konversi TERKUNCI sesudah dipakai (putusan pemilik LANGSUNG 5 Nov, pola NetSuite Units of Measure): faktor tak dibekukan di baris
dokumen -> konversi yang sudah muncul di transaksi (baris dokumen bersatuan atau kartu stok) tak boleh diubah faktornya / dihapus /
dinonaktifkan; harga konversi tetap boleh. SATU penentu satuan_dasar.tolak_ubah_konversi di ketiga penulis konversi."""
import asyncio
import inspect
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.routers import items as RI
from app.routers import units as RU
from app.services import satuan_dasar as SD

D = Decimal


class _K:
    def __init__(self, terpakai, alias=()):
        self.terpakai, self.alias, self.tanya = set(terpakai), list(alias), []

    async def fetch(self, sql, *a):
        return [{"name": n, "abbreviation": s} for n, s in self.alias] if "product_units" in sql else []

    async def fetchval(self, sql, *a):
        self.tanya.append((sql, a))
        return bool(self.terpakai & set(a[2]))


def _jalan(c, lama, baru):
    return asyncio.run(SD.tolak_ubah_konversi(c, "t", "p", lama, baru))


def test_sql_mencakup_semua_baris_dokumen_dan_kartu_stok():
    for t, k in SD.BARIS_BERSATUAN:
        assert f"FROM {t} WHERE {k} = $1" in SD.SQL_KONVERSI_TERPAKAI, t
    assert "FROM inventory_ledger WHERE tenant_id = $2 AND product_id = $1" in SD.SQL_KONVERSI_TERPAKAI
    assert "price_list_items" not in SD.SQL_KONVERSI_TERPAKAI and "item_pricing" not in SD.SQL_KONVERSI_TERPAKAI  # harga != transaksi


def test_faktor_berubah_atau_dihapus_ditolak_bila_terpakai():
    lama = [{"conversion_unit": "Dus", "conversion_factor": D("12")}]
    for baru in ([("Dus", D("10"))], [], None):
        with pytest.raises(HTTPException) as e:
            _jalan(_K({"dus"}), lama, baru)
        assert e.value.status_code == 409 and e.value.code == SD.KODE_KONVERSI_TERPAKAI and "Dus" in e.value.detail


def test_tak_berubah_atau_belum_terpakai_boleh():
    lama = [{"conversion_unit": "Dus", "conversion_factor": D("12")}]
    c = _K({"dus"})
    _jalan(c, lama, [("dus", 12)])  # sama (hanya harga berubah) -> tanpa baca transaksi
    assert c.tanya == []
    _jalan(_K(set()), lama, [("Dus", D("10"))])  # belum pernah dipakai -> faktor boleh diubah


def test_alias_kamus_tenant_ikut_dicari():
    c = _K({"ctn"}, alias=[("Karton", "ctn")])
    with pytest.raises(HTTPException):
        _jalan(c, [{"conversion_unit": "Karton", "conversion_factor": D("24")}], None)
    assert set(c.tanya[0][1][2]) == {"karton", "ctn"}


def test_ketiga_penulis_konversi_memakai_penentu():
    assert "SD.tolak_ubah_konversi(" in inspect.getsource(RU.set_conversion)
    assert "SD.tolak_ubah_konversi(" in inspect.getsource(RU.delete_conversion)
    s = inspect.getsource(RI.update_item)
    # P3: penentu dipindah ke _periksa_konversi (PUT + pratinjau /edit/preview memakai SATU fungsi); PUT tetap memanggilnya sebelum menonaktifkan konversi
    assert "_SDK.tolak_ubah_konversi(" in inspect.getsource(RI._periksa_konversi)
    assert "_periksa_konversi(" in s and s.index("_periksa_konversi(") < s.index("Deactivate all existing conversions")


class _KDasar(_K):
    def __init__(self, terpakai_barang, alias=()):
        super().__init__(set(), alias)
        self.barang = terpakai_barang

    async def fetchval(self, sql, *a):
        self.tanya.append((sql, a))
        return self.barang


def test_satuan_dasar_terkunci_bila_barang_punya_riwayat():
    with pytest.raises(HTTPException) as e:
        asyncio.run(SD.tolak_ubah_dasar(_KDasar(True), "t", "p", "Botol", "Dus"))
    assert e.value.status_code == 409 and e.value.code == SD.KODE_SATUAN_DASAR_TERPAKAI and "Botol" in e.value.detail
    asyncio.run(SD.tolak_ubah_dasar(_KDasar(False), "t", "p", "Botol", "Dus"))  # belum ada riwayat -> boleh
    c = _KDasar(True, alias=[("Botol", "btl")])
    asyncio.run(SD.tolak_ubah_dasar(c, "t", "p", "Botol", "btl"))  # alias = satuan yang sama -> boleh, tanpa baca riwayat
    assert c.tanya == []
    asyncio.run(SD.tolak_ubah_dasar(_KDasar(True), "t", "p", "Botol", None))  # tak diubah


def test_sql_barang_terpakai_semua_tabel_dan_update_item_memanggilnya_sebelum_update():
    for t, k in SD.BARIS_BERSATUAN:
        assert f"FROM {t} WHERE {k} = $1" in SD.SQL_BARANG_TERPAKAI, t
    s = inspect.getsource(RI.update_item)
    # P3: penentu dipindah ke _periksa_ubah (PUT + pratinjau /edit/preview); PUT memanggilnya sebelum UPDATE
    assert "_SDK.tolak_ubah_dasar(" in inspect.getsource(RI._periksa_ubah)
    assert "_periksa_ubah(" in s and s.index("_periksa_ubah(") < s.index("UPDATE products SET")
