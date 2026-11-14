"""D2 follow-up (15 Nov 2026): pratinjau/penulis hapus daftar harga yang dipakai pelanggan MENYEBUT NAMA (maks 5 + "dan N lainnya") dan
jumlah baris harga yang ikut terhapus walau diblok (putusan pemilik: tolak dengan menyebut nama pelanggan)."""
import asyncio
import inspect

from app.routers import price_lists as PL
from app.services import harga_aksi as HA


def _r(**kw):
    return {"id": "pl1", "code": "GRS", "name": "Grosir", "is_active": True, "is_default": False, "start_date": None, "end_date": None,
            "priority": 0, "description": None, **kw}


def test_blok_menyebut_nama_pelanggan_dan_jumlah_baris():
    x = HA.rencana_hapus(_r(), 2, 9, ["Budi", "Sari"])
    b = x["blocks"][0]
    assert b["code"] == "DH_DIPAKAI_PELANGGAN"
    assert "Budi, Sari" in b["message"] and "2 pelanggan" in b["message"] and "dan " not in b["message"].split("pelanggan")[1].split(";")[0]
    assert b["jumlah_pelanggan"] == 2 and b["nama_pelanggan"] == ["Budi", "Sari"] and b["sisa_pelanggan"] == 0
    assert b["baris_terhapus"] == 9 and x["rencana"] is None


def test_lebih_dari_lima_nama_dipotong_dan_dan_n_lainnya():
    nama = [f"P{i}" for i in range(5)]
    x = HA.rencana_hapus(_r(), 8, 3, nama)
    b = x["blocks"][0]
    assert b["nama_pelanggan"] == nama and b["sisa_pelanggan"] == 3 and "dan 3 lainnya" in b["message"]
    assert "P0, P1, P2, P3, P4" in b["message"]


def test_lebih_dari_lima_nama_dikirim_dipotong_penentu():
    x = HA.rencana_hapus(_r(), 7, 0, [f"P{i}" for i in range(7)])
    b = x["blocks"][0]
    assert len(b["nama_pelanggan"]) == 5 and b["sisa_pelanggan"] == 2 and "dan 2 lainnya" in b["message"]


def test_baris_nol_tetap_terkirim_bukan_none():
    assert HA.rencana_hapus(_r(), 1, 0, ["A"])["blocks"][0]["baris_terhapus"] == 0


def test_tanpa_pelanggan_rencana_lama_tak_berubah():
    x = HA.rencana_hapus(_r(), 0, 4, [])
    assert x["ok"] and x["rencana"] == {"jenis": "hapus_permanen", "baris_terhapus": 4}


def test_kompatibel_tanpa_argumen_nama():
    b = HA.rencana_hapus(_r(), 4, 10)["blocks"][0]
    assert "4 pelanggan" in b["message"] and b["nama_pelanggan"] == []


def test_ukur_nama_pelanggan_satu_query_bersaring_tenant_urut_nama():
    sql = inspect.getsource(HA.ukur_nama_pelanggan)
    assert "tenant_id = $1" in sql and "price_list_id = $2" in sql and "ORDER BY" in sql and "LIMIT" in sql


def test_penulis_dan_pratinjau_memakai_penentu_nama_yang_sama():
    for f in (PL.delete_price_list, PL.preview_hapus_daftar):
        s = inspect.getsource(f)
        assert "HA.ukur_nama_pelanggan(" in s, f.__name__
