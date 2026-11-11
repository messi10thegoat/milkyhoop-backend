"""B1 VARIAN (BUG-159; putusan MASTER 12 Nov 2026: dua dimensi ukuran x warna dalam SATU POST, batas wajar, tanpa migrasi).
Model: barang INDUK (is_matrix_parent, tanpa stok) + ANAK per kombinasi (baris products sendiri: stok/HPP/harga/SKU sendiri), jadi dokumen
tak berubah (baris menunjuk id anak). Penulisan memakai jalur resmi tulis_barang_baru (kode barang, akun bawaan, nama unik) lalu menandai
kolom matriks (V090). Kunci atribut `ukuran`/`size` dibaca cari_barang Milkybook."""
import asyncio
import inspect
import json
import re
import uuid

import pytest
from fastapi import HTTPException

from app.middleware.permission_middleware import izin_untuk_jalur
from app.routers import matrix_items as MR
from app.schemas.barang_varian import BuatMatriksRequest
from app.services import barang_varian as BV

T = "t-uji"


def _body(**kw):
    d = dict(name="Kaos Polos Cotton 30s", base_unit="Pcs", kategori="Kaos", sales_price=75000, purchase_price=40000,
             dimensi=[{"nama": "ukuran", "nilai": ["S", "M", "L"]}])
    d.update(kw)
    return BuatMatriksRequest(**d)


def _422(fn, *a):
    with pytest.raises(HTTPException) as e:
        fn(*a)
    assert e.value.status_code == 422 and e.value.detail["code"] == "VARIAN_TAK_VALID", e.value.detail
    return e.value.detail["message"]


# ---------------------------------------------------------------- rakit (murni)
def test_rakit_satu_dimensi():
    r = BV.rakit("Kaos Polos Cotton 30s", [{"nama": "ukuran", "nilai": ["S", "M", "L"]}])
    assert [x["name"] for x in r] == ["Kaos Polos Cotton 30s S", "Kaos Polos Cotton 30s M", "Kaos Polos Cotton 30s L"]
    assert r[1]["atribut"] == {"ukuran": "M"}


def test_rakit_dua_dimensi_urutan_dimensi_pertama_paling_luar():
    r = BV.rakit("Kaos", [{"nama": "ukuran", "nilai": ["S", "M"]}, {"nama": "warna", "nilai": ["Hitam", "Putih"]}])
    assert [x["name"] for x in r] == ["Kaos S Hitam", "Kaos S Putih", "Kaos M Hitam", "Kaos M Putih"]
    assert r[3]["atribut"] == {"ukuran": "M", "warna": "Putih"}


def test_kunci_dan_nilai_dirapikan():
    r = BV.rakit("Kaos", [{"nama": "  Ukuran ", "nilai": [" S ", "M"]}])
    assert r[0]["atribut"] == {"ukuran": "S"} and r[0]["name"] == "Kaos S"


@pytest.mark.parametrize("dimensi,kata", [
    ([{"nama": "a", "nilai": ["1"]}, {"nama": "b", "nilai": ["1"]}, {"nama": "c", "nilai": ["1"]}], "paling banyak 2 dimensi"),
    ([{"nama": "ukuran", "nilai": [str(i) for i in range(21)]}], "paling banyak 20 nilai"),
    ([{"nama": "ukuran", "nilai": [str(i) for i in range(11)]}, {"nama": "warna", "nilai": [str(i) for i in range(10)]}], "paling banyak 100 varian"),
    ([{"nama": "ukuran", "nilai": ["M", "m"]}], "kembar"),
    ([{"nama": "ukuran", "nilai": ["S", "  "]}], "kosong"),
    ([{"nama": "ukuran", "nilai": ["S"]}, {"nama": " UKURAN", "nilai": ["Hitam"]}], "kembar"),
    ([], "paling sedikit satu dimensi"),
    ([{"nama": "ukuran", "nilai": []}], "paling sedikit satu nilai"),
])
def test_rakit_menolak_batas_dan_duplikat(dimensi, kata):
    assert kata in _422(BV.rakit, "Kaos", dimensi)


def test_nama_anak_tak_boleh_lebih_dari_100_karakter():
    assert "100 karakter" in _422(BV.rakit, "K" * 98, [{"nama": "ukuran", "nilai": ["XL"]}])


# ---------------------------------------------------------------- koneksi tiruan
class _K:
    def __init__(self, terpakai=(), induk=None, anak_atribut=None, jumlah_anak=0):
        self.terpakai, self.induk, self.anak_atribut, self.jumlah_anak = set(terpakai), induk, anak_atribut, jumlah_anak
        self.tulis = []

    async def fetch(self, sql, *a):
        assert "tenant_id = $1" in sql
        return [{"nama_produk": n} for n in a[1] if n in self.terpakai]

    async def fetchrow(self, sql, *a):
        if "is_matrix_parent" in sql:
            return self.induk
        if "matrix_attributes" in sql:
            return {"matrix_attributes": json.dumps(self.anak_atribut)} if self.anak_atribut is not None else None
        raise AssertionError(sql)

    async def fetchval(self, sql, *a):
        return self.jumlah_anak

    async def execute(self, sql, *a):
        self.tulis.append((" ".join(sql.split()), a))
        return "UPDATE 1"


CTX = {"tenant_id": T, "user_id": str(uuid.uuid4())}


def _pasang_tulis(monkeypatch):
    dibuat = []

    async def palsu(conn, ctx, body, user_id, user_name):
        i = uuid.uuid4()
        dibuat.append((i, body))
        return i
    from app.routers import items
    monkeypatch.setattr(items, "tulis_barang_baru", palsu)
    return dibuat


# ---------------------------------------------------------------- rencana + tulis
def test_rencana_baca_saja_melaporkan_nama_bentrok_induk_dan_anak():
    k = _K(terpakai={"Kaos Polos Cotton 30s M", "Kaos Polos Cotton 30s"})
    r = asyncio.run(BV.rencana(k, T, _body()))
    assert r["jumlah"] == 3 and not k.tulis
    assert sorted(b["name"] for b in r["blocks"]) == ["Kaos Polos Cotton 30s", "Kaos Polos Cotton 30s M"]
    assert all(b["code"] == "NAMA_BARANG_DIPAKAI" for b in r["blocks"])


def test_tulis_induk_tanpa_stok_anak_berstok_lewat_jalur_resmi(monkeypatch):
    dibuat = _pasang_tulis(monkeypatch)
    k = _K()
    h = asyncio.run(BV.tulis(k, CTX, _body(dimensi=[{"nama": "ukuran", "nilai": ["S", "M"]}, {"nama": "warna", "nilai": ["Hitam"]}]), "u", "n"))
    assert len(dibuat) == 3 and len(h["varian"]) == 2
    induk, anak = dibuat[0][1], [b for _, b in dibuat[1:]]
    assert induk.name == "Kaos Polos Cotton 30s" and induk.track_inventory is False and induk.item_type == "goods"
    assert [b.name for b in anak] == ["Kaos Polos Cotton 30s S Hitam", "Kaos Polos Cotton 30s M Hitam"]
    assert all(b.track_inventory is True and b.base_unit == "Pcs" and b.kategori == "Kaos" for b in anak)
    assert all(b.sales_price == 75000 and b.purchase_price == 40000 for b in anak)
    sql_induk = [q for q, a in k.tulis if "is_matrix_parent" in q]
    assert len(sql_induk) == 1 and "tenant_id" in sql_induk[0]
    sql_anak = [(q, a) for q, a in k.tulis if "matrix_parent_id" in q]
    assert len(sql_anak) == 2 and all("tenant_id" in q for q, _ in sql_anak)
    assert json.loads(sql_anak[0][1][2]) == {"ukuran": "S", "warna": "Hitam"} and sql_anak[0][1][1] == dibuat[0][0]


def test_tulis_konflik_nama_409_sebelum_menulis_apa_pun(monkeypatch):
    dibuat = _pasang_tulis(monkeypatch)
    k = _K(terpakai={"Kaos Polos Cotton 30s L"})
    with pytest.raises(HTTPException) as e:
        asyncio.run(BV.tulis(k, CTX, _body(), "u", "n"))
    assert e.value.status_code == 409 and e.value.detail["code"] == "VARIAN_DITOLAK"
    assert e.value.detail["blocks"][0]["name"] == "Kaos Polos Cotton 30s L"
    assert not dibuat and not k.tulis


# ---------------------------------------------------------------- tambah varian
INDUK = {"id": uuid.uuid4(), "nama_produk": "Kaos", "base_unit": "Pcs", "kategori": "Kaos", "deskripsi": None,
         "sales_price": 75000, "purchase_price": 40000}


def test_tambah_induk_tak_ada_404():
    with pytest.raises(HTTPException) as e:
        asyncio.run(BV.tambah(_K(induk=None), CTX, INDUK["id"], [{"nama": "ukuran", "nilai": ["XL"]}], "u", "n"))
    assert e.value.status_code == 404 and e.value.detail["code"] == "BARANG_MATRIKS_TAK_ADA"


def test_tambah_dimensi_harus_sama_dengan_varian_yang_ada():
    k = _K(induk=INDUK, anak_atribut={"ukuran": "S", "warna": "Hitam"})
    msg = _422_async(BV.tambah, k, CTX, INDUK["id"], [{"nama": "ukuran", "nilai": ["XL"]}], "u", "n")
    assert "ukuran, warna" in msg


def _422_async(fn, *a):
    with pytest.raises(HTTPException) as e:
        asyncio.run(fn(*a))
    assert e.value.status_code == 422 and e.value.detail["code"] == "VARIAN_TAK_VALID"
    return e.value.detail["message"]


def test_tambah_batas_total_100_varian():
    k = _K(induk=INDUK, anak_atribut={"ukuran": "S"}, jumlah_anak=99)
    assert "paling banyak 100 varian" in _422_async(BV.tambah, k, CTX, INDUK["id"], [{"nama": "ukuran", "nilai": ["XL", "XXL"]}], "u", "n")


def test_tambah_membuat_kombinasi_baru_dengan_harga_induk(monkeypatch):
    dibuat = _pasang_tulis(monkeypatch)
    k = _K(induk=INDUK, anak_atribut={"ukuran": "S"}, jumlah_anak=3)
    h = asyncio.run(BV.tambah(k, CTX, INDUK["id"], [{"nama": "ukuran", "nilai": ["XL"]}], "u", "n"))
    assert [b.name for _, b in dibuat] == ["Kaos XL"] and dibuat[0][1].sales_price == 75000 and dibuat[0][1].track_inventory is True
    assert len(h["varian"]) == 1 and any("matrix_parent_id" in q for q, _ in k.tulis)


# ---------------------------------------------------------------- penjaga pasang, izin, kode lama
def test_router_dipasang_di_main_dan_izin_modul_barang():
    main = open(inspect.getsourcefile(MR).replace("routers/matrix_items.py", "main.py")).read()
    assert re.search(r'include_router\(matrix_items\.router, prefix="/api/matrix-items"', main)
    assert izin_untuk_jalur("/api/matrix-items", "GET") == ("item", "R")
    assert izin_untuk_jalur("/api/matrix-items/" + str(uuid.uuid4()), "GET") == ("item", "R")
    for p in ("/api/matrix-items", "/api/matrix-items/preview", f"/api/matrix-items/{uuid.uuid4()}/variants",
              f"/api/matrix-items/{uuid.uuid4()}/variants/preview"):
        assert izin_untuk_jalur(p, "POST") == ("item", "C"), p


def test_rute_tulis_terdaftar_di_inventaris():
    inv = open(inspect.getsourcefile(MR).replace("app/routers/matrix_items.py", "tests/unit/inventaris_rute_tulis.txt")).read()
    for baris in ("POST /api/matrix-items", "POST /api/matrix-items/preview", "POST /api/matrix-items/{item_id}/variants",
                  "POST /api/matrix-items/{item_id}/variants/preview"):
        assert re.search(rf"^{re.escape(baris)}$", inv, re.M), baris


def test_rute_tulis_berskema_dan_urutan_preview_sebelum_id():
    s = inspect.getsource(MR)
    for lama in ("request.json()", "set_config", "Matrix parent", "is required", "Created "):
        assert lama not in s, lama   # tanpa body mentah, set_config lepas (jebakan G3), pesan Inggris
    paths = [(r.path, tuple(sorted(r.methods))) for r in MR.router.routes]
    assert ("/preview", ("POST",)) in paths and ("", ("POST",)) in paths
    assert ("/{item_id}/variants", ("POST",)) in paths and ("/{item_id}/variants/preview", ("POST",)) in paths
    assert paths.index(("/preview", ("POST",))) < paths.index(("/{item_id}/variants", ("POST",)))
    for r in MR.router.routes:
        assert r.response_model is not None, r.path   # medan tak dideklarasikan dibuang diam-diam


def test_penulisan_memakai_jalur_resmi_bukan_insert_sendiri():
    s = inspect.getsource(BV)
    assert "tulis_barang_baru" in s and "INSERT INTO products" not in s
    assert "periksa_badan_item" in s and "tanpa_stok_bila_bukan_barang" in s   # prasyarat yang diwajibkan docstring tulis_barang_baru
