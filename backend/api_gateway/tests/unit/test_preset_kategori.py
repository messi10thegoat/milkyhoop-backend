"""Preset flag kategori usaha (4 Nov 2026, desain disetujui MASTER): satu sumber, hanya MENAMBAH, idempoten."""
import asyncio
from pathlib import Path

import pytest

from app.services import preset_kategori as PK

APP = Path(PK.__file__).resolve().parents[1]


def test_isi_preset_grosir_distribusi():
    assert PK.kunci_preset("grosir_distribusi") == (
        "kategori_grosir_distribusi", "modul_izinperan_mati", "modul_varian_mati", "modul_manufaktur_mati", "modul_rekonsiliasi_mati",
        "pengiriman_sebelum_faktur", "pengiriman_stok_sebelum_faktur", "pemindahan_barang", "listpage_daftar_harga_v1")
    # khas permintaan satu tenant, BUKAN bawaan grosir
    for k in ("modul_pajak_mati", "modul_batch_mati", "modul_gaji_mati", "modul_conversational_mati"):
        assert k not in PK.PRESET["grosir_distribusi"]


def test_kategori_tak_dikenal_galat():
    with pytest.raises(ValueError, match="tidak dikenal"):
        PK.kunci_preset("toko_kue")


def test_kunci_lolos_cek_v304():
    import re
    for kat in PK.PRESET:
        assert all(re.fullmatch(r"[a-z][a-z0-9_]{2,63}", k) for k in PK.kunci_preset(kat))


class Conn:
    def __init__(self, ada):
        self.ada, self.exec = dict(ada), []

    async def fetch(self, sql, tid, kunci):
        assert "FROM tenant_features WHERE tenant_id = $1" in sql
        return [{"feature": k, "enabled": v} for k, v in self.ada.items() if k in kunci]

    async def execute(self, sql, tid, ubah):
        self.exec.append((sql, list(ubah)))
        for k in ubah:
            self.ada[k] = True


def test_rencana_hanya_kunci_preset_dan_terapkan_idempoten():
    c = Conn({"modul_varian_mati": False, "modul_manufaktur_mati": True, "modul_pajak_mati": True, "lain_x": False})
    r = asyncio.run(PK.rencana_preset(c, "t", "grosir_distribusi"))
    assert r == {"tambah": ["kategori_grosir_distribusi", "modul_izinperan_mati", "modul_rekonsiliasi_mati", "pengiriman_sebelum_faktur",
                             "pengiriman_stok_sebelum_faktur", "pemindahan_barang", "listpage_daftar_harga_v1"],
                 "nyalakan": ["modul_varian_mati"], "sudah": ["modul_manufaktur_mati"]}
    asyncio.run(PK.terapkan_preset(c, "t", "grosir_distribusi"))
    assert c.exec[0][1] == ["kategori_grosir_distribusi", "modul_izinperan_mati", "modul_rekonsiliasi_mati", "pengiriman_sebelum_faktur",
                            "pengiriman_stok_sebelum_faktur", "pemindahan_barang", "listpage_daftar_harga_v1", "modul_varian_mati"]
    assert "DELETE" not in c.exec[0][0] and "enabled = false" not in c.exec[0][0]
    assert c.ada["lain_x"] is False and c.ada["modul_pajak_mati"] is True  # flag lain tak tersentuh
    asyncio.run(PK.terapkan_preset(c, "t", "grosir_distribusi"))
    assert len(c.exec) == 1  # kedua kali: nol tulis


def test_satu_sumber_kunci_preset():
    pemakai = [p for p in APP.rglob("*.py") if "modul_izinperan_mati" in p.read_text(encoding="utf-8", errors="ignore")]
    assert pemakai == [Path(PK.__file__).resolve()]


def test_kunci_alur_kirim_sama_dengan_konstanta_pembaca():
    # alur grosir SO -> Surat Jalan (stok keluar) -> Faktur + pindah gudang (pemilik 5 Nov): nama kunci preset HARUS sama dengan flag
    # yang dibaca penentunya -- ganti nama di pembaca tanpa preset (atau sebaliknya) = preset diam tak berefek.
    from app.services import pemindahan_barang as PB, sj_so as SJ
    assert {SJ.FLAG, SJ.FLAG_STOK, PB.FLAG} <= set(PK.PRESET["grosir_distribusi"])
