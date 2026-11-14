"""S-1 (14 Nov 2026): SATU penentu pemakai satuan + ringkasan SATU angka + aksi_tersedia seragam."""
import asyncio
import pytest
from app.services import satuan_pakai as SP

def _u(i, nama, abbr=None, sistem=False, bawaan=False):
    return {"id": i, "name": nama, "abbreviation": abbr or nama, "is_system": sistem, "bawaan": bawaan}

class FakeConn:
    def __init__(self, produk, konv, alias_rows):
        self.produk, self.konv, self.alias_rows = produk, konv, alias_rows
    async def fetch(self, sql, *a):
        if "FROM products" in sql:
            return [{"u": u, "n": n} for u, n in self.produk]
        if "FROM unit_conversions" in sql:
            return [{"u": u, "n": n} for u, n in self.konv]
        return []

def test_kunci_pakai_alias_nama_dan_singkatan():
    daftar = [_u("1", "Kilogram", "Kg")]
    pakai = SP.tetapkan_pemakai(daftar, produk=[("kg", 2), ("KILOGRAM", 1)], konv=[("Kg ", 3)])
    assert pakai["1"] == {"jumlah_barang": 3, "jumlah_konversi": 3}

def test_tak_terpakai_nol_nol():
    daftar = [_u("1", "Pcs"), _u("2", "Box")]
    pakai = SP.tetapkan_pemakai(daftar, produk=[("pcs", 4)], konv=[])
    assert pakai["2"] == {"jumlah_barang": 0, "jumlah_konversi": 0}

def test_ringkasan_satu_angka_dan_konsisten_dengan_filter():
    daftar = [_u("1", "Pcs"), _u("2", "Box"), _u("3", "pcs.", "pcs.")]
    pakai = {"1": {"jumlah_barang": 4, "jumlah_konversi": 0}, "2": {"jumlah_barang": 0, "jumlah_konversi": 0}, "3": {"jumlah_barang": 0, "jumlah_konversi": 0}}
    baris = SP.susun(daftar, pakai)
    r = SP.ringkasan(baris)
    assert r["semua"] == 3 and r["tak_terpakai"] == 2 and r["terpakai"] == 1
    for f, kunci in (("tak_terpakai", "tak_terpakai"), ("duplikat", "duplikat")):
        assert len(SP.saring(baris, f)) == r[kunci]

def test_duplikat_normalisasi_huruf_spasi_titik():
    daftar = [_u("1", "Pcs"), _u("2", "pcs."), _u("3", " PCS "), _u("4", "Box")]
    baris = SP.susun(daftar, {})
    dup = {b["id"] for b in baris if b["duplikat"]}
    assert dup == {"1", "2", "3"}
    assert len({b["kelompok_duplikat"] for b in baris if b["duplikat"]}) == 1

def test_aksi_tersedia_bentuk_seragam_dan_alasan():
    daftar = [_u("bawaan:Pcs", "Pcs", sistem=True, bawaan=True), _u("9", "Dus")]
    baris = SP.susun(daftar, {"9": {"jumlah_barang": 2, "jumlah_konversi": 0}})
    for b in baris:
        assert set(b["aksi_tersedia"]) == set(SP.AKSI)
        for v in b["aksi_tersedia"].values():
            assert isinstance(v["tersedia"], bool) and (v["tersedia"] or v["alasan"])
        assert b["aksi_semua"] == [a for a in SP.AKSI]
    pcs, dus = baris
    assert pcs["aksi_tersedia"]["nonaktifkan"]["tersedia"] is False
    assert dus["aksi_tersedia"]["nonaktifkan"]["tersedia"] is False  # terpakai 2 barang
    assert "2 barang" in dus["aksi_tersedia"]["nonaktifkan"]["alasan"]

def test_satuan_tak_terpakai_boleh_nonaktif():
    baris = SP.susun([_u("5", "Dus")], {"5": {"jumlah_barang": 0, "jumlah_konversi": 0}})
    assert baris[0]["aksi_tersedia"]["nonaktifkan"]["tersedia"] is True

def test_filter_tak_dikenal_ditolak():
    with pytest.raises(ValueError):
        SP.saring([], "ngawur")

def test_pemakai_dari_db_satu_penentu():
    c = FakeConn([("kg", 2)], [("Kilogram", 1)], [])
    daftar = [_u("1", "Kilogram", "Kg")]
    pakai = asyncio.run(SP.muat_pemakai(c, "t", daftar))
    assert pakai["1"] == {"jumlah_barang": 2, "jumlah_konversi": 1}
