"""Label baris perubahan pada rencana pratinjau (18 Nov 2026): Satuan (ubah/buat/gabung), Gudang (buat/ubah, negara -> nama), Kategori (nama/status/induk/bawaan/pindah/gabung/akun)
membawa {field, label, before, after, before_text, after_text} DARI SERVER (FE tak mengarang label dari kunci)."""
import pathlib
import re

from app.services import kategori_aksi as KA
from app.services import kategori_akun as KAK
from app.services import label_rencana as LR

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
KUNCI = {"field", "label", "before", "after", "before_text", "after_text"}


def lengkap(p):
    assert set(p) >= KUNCI, p
    assert p["label"] and isinstance(p["before_text"], str) and isinstance(p["after_text"], str), p
    return p


def k(nama="Minuman", jb=3, aktif=True, bawaan=False, parent=None):
    return {"id": "c-1", "name": nama, "is_active": aktif, "is_default": bawaan, "parent_id": "p" if parent else None, "parent_name": parent, "jumlah_barang": jb, "jumlah_aktif": jb,
            "jumlah_anak": 0, **{f"akun_{j}": None for j in KAK.JENIS}}


def test_baris_teks_kosong_dan_bool():
    assert LR.teks(None) == "—" and LR.teks("") == "—" and LR.teks("x") == "x" and LR.teks(True) == "Ya" and LR.teks(False, ("Aktif", "Nonaktif")) == "Nonaktif"
    b = LR.baris("name", "Nama", None, "A")
    assert b == {"field": "name", "label": "Nama", "before": None, "after": "A", "before_text": "—", "after_text": "A"}


def test_kategori_semua_rencana_berlabel():
    r = KA.rencana_buat("Snack", False)["rencana"]; assert [lengkap(p)["label"] for p in r["perubahan"]] == ["Nama"]
    r = KA.rencana_ubah_nama(k(), "Minuman Ringan", False)["rencana"]; p = lengkap(r["perubahan"][0]); assert (p["label"], p["before_text"], p["after_text"]) == ("Nama", "Minuman", "Minuman Ringan")
    r = KA.rencana_status(k(), False)["rencana"]; p = lengkap(r["perubahan"][0]); assert (p["label"], p["before_text"], p["after_text"]) == ("Status", "Aktif", "Nonaktif")
    r = KA.rencana_status(k(aktif=False), True)["rencana"]; p = r["perubahan"][0]; assert (p["before_text"], p["after_text"]) == ("Nonaktif", "Aktif")
    r = KA.rencana_bawaan(k())["rencana"]; assert lengkap(r["perubahan"][0])["after_text"] == "Kategori bawaan"
    r = KA.rencana_induk(k(), {"id": "p1", "name": "Makanan", "parent_id": None, "is_active": True, "jumlah_barang": 0, "jumlah_anak": 0}, True)["rencana"]; p = lengkap(r["perubahan"][0]); assert (p["label"], p["before_text"], p["after_text"]) == ("Induk", "Tingkat atas", "Makanan")
    r = KA.rencana_induk(k(parent="Makanan"), None, False)["rencana"]; p = lengkap(r["perubahan"][0]); assert (p["before_text"], p["after_text"]) == ("Makanan", "Tingkat atas")
    r = KA.rencana_pindah("Lama", {"id": "t", "name": "Baru", "is_active": True}, 3, 3, [], True)["rencana"]; p = lengkap(r["perubahan"][0]); assert (p["before_text"], p["after_text"]) == ("Lama", "Baru")
    r = KA.rencana_pindah(None, {"id": "t", "name": "Baru", "is_active": True}, 3, 3, [], False)["rencana"]; assert r["perubahan"][0]["before_text"] == "Tanpa kategori"
    r = KA.rencana_gabung(k("A"), {**k("B"), "id": "c-2"})["rencana"]; p = lengkap(r["perubahan"][0]); assert (p["label"], p["before_text"], p["after_text"]) == ("Gabung", "A", "B")


def test_kategori_akun_perubahan_berlabel_dan_kunci_lama_tetap():
    jual = {"id": "11111111-1111-1111-1111-111111111111", "account_code": "4-10100", "name": "Penjualan", "account_type": "REVENUE", "is_active": True, "is_header": False}
    r = KAK.rencana_akun(k(), {"penjualan": jual["id"]}, {jual["id"]: jual})["rencana"]["perubahan"][0]
    lengkap(r)
    assert r["label"] == "Akun penjualan" and r["before_text"] == "—" and r["after_text"] == "4-10100 Penjualan"
    assert r["jenis"] == "penjualan" and r["dari"] is None and r["ke"] == "4-10100 Penjualan" and r["ke_id"] == jual["id"]      # kunci lama (penulis) tetap


def test_gudang_dan_satuan_memakai_label_rencana():
    for berkas in ("gudang_rencana.py", "satuan_rencana.py", "kategori_aksi.py", "kategori_akun.py"):
        assert re.search(r"label_rencana as LR", (APP / "services" / berkas).read_text()), berkas
