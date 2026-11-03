"""NAMA-NONAKTIF (2 Nov 2026; putusan MASTER, pola NetSuite: nama entitas unik TERMASUK nonaktif).

SPEK: pelanggan == vendor -- nama persis sama dengan pelanggan/vendor NONAKTIF ditolak 409 berkalimat "… nonaktif — aktifkan
lagi dari Kontak atau gunakan nama lain."; aktif = kalimat lama; pelanggan terhapus lunak (deleted_at) boleh dipakai ulang;
kecuali_id (ubah diri sendiri) lolos. Kode/medan galat tetap.
"""
import asyncio
import inspect
import uuid

import pytest

from app.services import pelanggan_unik as PU
from app.services import vendor_unik as VU
from app.services.galat_berkode import GalatBerkode

T = "tenant-uji"


def _j(c):
    return asyncio.run(c)


class Conn:
    def __init__(self, baris):
        self.baris, self.q = baris, []

    async def fetchrow(self, sql, *a):
        self.q.append((" ".join(sql.split()), a))
        return self.baris


def test_pelanggan_nonaktif_ditolak_berkalimat_aktif_kalimat_lama():
    with pytest.raises(GalatBerkode) as e:
        _j(PU.tolak_nama_dipakai(Conn({"id": uuid.uuid4(), "is_active": False}), T, "Toko A"))
    assert e.value.status_code == 409 and e.value.code == "NAMA_PELANGGAN_DIPAKAI" and e.value.field == "name"
    assert e.value.detail == 'Nama pelanggan "Toko A" sudah dipakai pelanggan nonaktif — aktifkan lagi dari Kontak atau gunakan nama lain.'
    with pytest.raises(GalatBerkode) as e:
        _j(PU.tolak_nama_dipakai(Conn({"id": uuid.uuid4(), "is_active": True}), T, "Toko A"))
    assert e.value.detail == 'Nama pelanggan "Toko A" sudah dipakai pelanggan lain. Gunakan nama lain.'
    assert _j(PU.tolak_nama_dipakai(Conn(None), T, "Toko A")) is None


def test_predikat_pelanggan_semua_belum_terhapus_dan_kecuali_diri():
    c = Conn(None)
    kid = uuid.uuid4()
    _j(PU.tolak_nama_dipakai(c, T, "Toko A", kecuali_id=kid))
    sql, a = c.q[0]
    assert "is_active = true" not in sql and "deleted_at IS NULL" in sql and "id <> $3::uuid" in sql
    assert "ORDER BY is_active DESC" in sql and a == (T, "Toko A", str(kid))


def test_vendor_nonaktif_kalimat_sama_dengan_pelanggan():
    with pytest.raises(GalatBerkode) as e:
        _j(VU.tolak_nama_dipakai(Conn({"id": uuid.uuid4(), "is_active": False}), T, "PT B"))
    assert e.value.detail == 'Nama vendor "PT B" sudah dipakai vendor nonaktif — aktifkan lagi dari Kontak atau gunakan nama lain.'
    with pytest.raises(GalatBerkode) as e:
        _j(VU.tolak_nama_dipakai(Conn({"id": uuid.uuid4(), "is_active": True}), T, "PT B"))
    assert e.value.detail == 'Nama vendor "PT B" sudah dipakai vendor lain. Gunakan nama lain.'


def test_pemindai_tanpa_saring_aktif_di_kedua_penentu_nama():
    for f in (PU.tolak_nama_dipakai, VU.tolak_nama_dipakai):
        assert "is_active = true" not in inspect.getsource(f), f.__module__
