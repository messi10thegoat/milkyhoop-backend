"""K-3b (17 Nov 2026, permintaan FE): aksi_tersedia.jadikan_bawaan {tersedia, alasan} di daftar+detail (aktif ∧ bukan bawaan; kalimat SATU sumber dgn rencana_bawaan); POST buat kategori mengembalikan id + baris daftar."""
import datetime
import pathlib
import uuid

from app.schemas.items import CreateCategoryResponse
from app.services import kategori_aksi as KA
from app.services import kategori_daftar as KD

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
ID = uuid.uuid4()


def r(aktif=True, bawaan=False, nama="Minyak"):
    return {"id": ID, "name": nama, "created_at": datetime.datetime(2026, 11, 17), "is_active": aktif, "is_default": bawaan, "parent_id": None, "parent_name": None,
            "description": None, "jumlah_anak": 0, "jumlah_barang": 0, "jumlah_aktif": 0}


def test_jadikan_bawaan_aturan_aktif_dan_bukan_bawaan():
    a = KD.baris(r())["aksi_tersedia"]["jadikan_bawaan"]
    assert a == {"tersedia": True, "alasan": None}
    n = KD.baris(r(aktif=False))["aksi_tersedia"]["jadikan_bawaan"]
    assert n["tersedia"] is False and n["alasan"] == "Kategori 'Minyak' nonaktif; aktifkan dulu sebelum dijadikan bawaan."
    b = KD.baris(r(bawaan=True))["aksi_tersedia"]["jadikan_bawaan"]
    assert b["tersedia"] is False and b["alasan"] == "Kategori 'Minyak' sudah menjadi kategori bawaan."


def test_nonaktif_dan_bawaan_nonaktif_alasan_nonaktif_lebih_dulu_sama_dengan_planner():
    ak = KD.aksi_tersedia(0, 0, "X", False, True)["jadikan_bawaan"]
    assert ak["alasan"] == KA.rencana_bawaan({"id": ID, "name": "X", "is_active": False, "is_default": True})["blocks"][0]["message"]
    for aktif, bawaan, kode in ((True, True, "KATEGORI_SUDAH_BAWAAN"), (False, False, "KATEGORI_NONAKTIF")):
        blok = KA.rencana_bawaan({"id": ID, "name": "X", "is_active": aktif, "is_default": bawaan})["blocks"][0]
        assert blok["code"] == kode and blok["message"] == KD.aksi_tersedia(0, 0, "X", aktif, bawaan)["jadikan_bawaan"]["alasan"]
    assert KA.rencana_bawaan({"id": ID, "name": "X", "is_active": True, "is_default": False})["blocks"] == []


def test_aksi_lain_tak_berubah():
    t = KD.baris(r())["aksi_tersedia"]
    assert {"ubah", "nonaktifkan", "aktifkan", "atur_induk", "hapus", "jadikan_bawaan"} <= set(t) and t["nonaktifkan"]["tersedia"] and not t["aktifkan"]["tersedia"]


def test_skema_buat_membawa_id_dan_data_default_kosong():
    f = CreateCategoryResponse.model_fields
    assert "id" in f and "data" in f and CreateCategoryResponse(category="x").id is None
    x = CreateCategoryResponse(category="Minyak", id=str(ID), data=KD.baris(r()))
    assert x.id == str(ID) and x.data["name"] == "Minyak" and x.data["jumlah_barang"] == 0 and x.data["aksi_tersedia"]["jadikan_bawaan"]["tersedia"] is True


def test_rute_buat_mengembalikan_id_dan_baris_dari_baris_baru():
    src = (APP / "routers" / "items.py").read_text().split("async def create_category")[1].split("@router.get")[0]
    assert "RETURNING id, name, created_at, is_active, is_default, parent_id, description" in src
    assert '"id": baru["id"], "data": baru' in src and "KD.baris({**dict(baris_baru)" in src and '"parent_name": induk["name"] if induk else None' in src
