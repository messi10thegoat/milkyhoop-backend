"""U-V1 Varian (26 Nov 2026, flag gelap varian_v1; DESAIN-varian §1 A1-A3 + B7): GET /api/matrix-items/{id}/variants
stok/teks/aksi/ringkasan/filter_atribut dari server; GET /api/items/{id} memberi is_matrix_parent/matrix_parent/atribut."""
import asyncio
import json
import uuid

import pytest
from fastapi import HTTPException

from app.middleware.permission_middleware import izin_untuk_jalur
from app.services import barang_varian as BV

T = "t-uji"
INDUK = uuid.uuid4()


def _anak(ukuran, warna, stok, status="active", jejak=0, harga=75000):
    return {"id": uuid.uuid4(), "nama_produk": f"Kaos {ukuran} {warna}", "item_code": f"BRG-{ukuran}{warna[0]}", "sku": None,
            "matrix_attributes": json.dumps({"ukuran": ukuran, "warna": warna}), "status": status, "item_type": "goods",
            "track_inventory": True, "base_unit": "pcs", "sales_price": harga, "purchase_price": 40000, "stok": stok,
            "transaction_count": jejak}


ANAK = [_anak("S", "Hitam", 10), _anak("S", "Putih", 0), _anak("M", "Hitam", -2, jejak=3), _anak("M", "Putih", 5, status="inactive")]


class Conn:
    def __init__(self, induk=True, anak=ANAK):
        self.induk, self.anak, self.sql = induk, anak, []

    async def fetchrow(self, sql, *a):
        assert "tenant_id = $1" in sql and a[0] == T
        return {"id": INDUK, "nama_produk": "Kaos", "base_unit": "pcs"} if self.induk else None

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        assert "tenant_id = $1" in sql and "inventory_ledger" in sql and a == (T, INDUK)
        return self.anak


def _jalan(**kw):
    a = dict(search=None, atribut=[], skip=0, limit=50)
    a.update(kw)
    return asyncio.run(BV.daftar_varian(Conn(), T, INDUK, a["search"], a["atribut"], a["skip"], a["limit"]))


def test_baris_teks_status_aksi_dari_server():
    r = _jalan()
    b = {x["nama"]: x for x in r["data"]}
    assert b["Kaos S Hitam"]["atribut"] == [{"nama": "ukuran", "nilai": "S"}, {"nama": "warna", "nilai": "Hitam"}]
    assert b["Kaos S Hitam"]["atribut_text"] == "S · Hitam"
    assert (b["Kaos S Hitam"]["stok_status"], b["Kaos S Hitam"]["stok_text"]) == ("ada", "10 pcs")
    assert b["Kaos S Putih"]["stok_status"] == "habis"
    assert (b["Kaos M Hitam"]["stok_status"], b["Kaos M Hitam"]["stok_text"]) == ("minus", "-2 pcs")
    assert b["Kaos M Hitam"]["aksi_tersedia"]["hapus"]["tersedia"] is False      # berjejak -> nonaktifkan, bukan hapus
    assert b["Kaos S Putih"]["aksi_tersedia"]["hapus"]["tersedia"] is True
    assert (b["Kaos M Putih"]["status"], b["Kaos M Putih"]["status_text"], b["Kaos M Putih"]["stok_status"]) == ("nonaktif", "Nonaktif", None)
    assert b["Kaos S Hitam"]["harga_jual_text"] == "Rp 75.000"


def test_ringkasan_server_dari_semua_tersaring_bukan_halaman():
    r = _jalan(limit=1)
    assert len(r["data"]) == 1 and r["total"] == 4 and r["has_more"] is True
    g = r["ringkasan"]
    assert g["jumlah_varian_text"] == "4 varian" and g["total_stok_text"] == "8 pcs"   # 10 + 0 - 2 (nonaktif tak dilacak)
    assert (g["jumlah_ada_stok_text"], g["jumlah_habis_text"]) == ("1 ada stok", "2 habis")


def test_saring_atribut_dan_cari():
    assert {x["nama"] for x in _jalan(atribut=["ukuran:M"])["data"]} == {"Kaos M Hitam", "Kaos M Putih"}
    assert {x["nama"] for x in _jalan(atribut=["ukuran:M", "warna:Hitam"])["data"]} == {"Kaos M Hitam"}
    assert {x["nama"] for x in _jalan(atribut=["warna:Hitam", "warna:Putih", "ukuran:S"])["data"]} == {"Kaos S Hitam", "Kaos S Putih"}
    assert {x["nama"] for x in _jalan(search="putih")["data"]} == {"Kaos S Putih", "Kaos M Putih"}
    assert _jalan(search="brg-sh")["total"] == 1


def test_filter_atribut_dari_semua_varian_tak_tersaring():
    f = _jalan(atribut=["ukuran:S"])["filter_atribut"]
    assert f == [{"nama": "ukuran", "nilai": [{"nilai": "S", "jumlah": 2}, {"nilai": "M", "jumlah": 2}]},
                 {"nama": "warna", "nilai": [{"nilai": "Hitam", "jumlah": 2}, {"nilai": "Putih", "jumlah": 2}]}]


def test_saringan_tak_berformat_422_dan_induk_tak_ada_404():
    with pytest.raises(HTTPException) as e:
        _jalan(atribut=["ukuranXL"])
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e:
        asyncio.run(BV.daftar_varian(Conn(induk=False), T, INDUK, None, [], 0, 50))
    assert e.value.status_code == 404


def test_rute_varian_flag_gelap_dan_izin_baca_barang():
    import inspect
    from app.routers import matrix_items as MR
    src = inspect.getsource(MR.daftar_varian)
    assert "FLAG_V1" in src and "FITUR_BELUM_AKTIF" in src
    assert BV.FLAG_V1 == "varian_v1"
    assert izin_untuk_jalur(f"/api/matrix-items/{uuid.uuid4()}/variants", "GET") == ("item", "R")


def test_info_varian_detail_barang():
    class C:
        async def fetchrow(self, sql, *a):
            assert "tenant_id = $1" in sql
            return {"id": INDUK, "nama_produk": "Kaos"}
    anak = {"matrix_parent_id": INDUK, "matrix_attributes": json.dumps({"ukuran": "S"}), "is_matrix_parent": False}
    assert asyncio.run(BV.info_varian_barang(C(), T, anak)) == {
        "is_matrix_parent": False, "matrix_parent": {"id": str(INDUK), "nama": "Kaos"}, "atribut": [{"nama": "ukuran", "nilai": "S"}]}
    assert asyncio.run(BV.info_varian_barang(C(), T, {"is_matrix_parent": True})) == {"is_matrix_parent": True, "matrix_parent": None, "atribut": []}
