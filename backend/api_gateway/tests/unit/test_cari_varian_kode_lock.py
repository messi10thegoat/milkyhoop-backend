"""(a) cari_barang (Milkybook v2) menambah `varian:[{id,nama,atribut,harga}]` ADITIF (untuk barang >1 dimensi ukuran x warna `ukuran` memuat ukuran
ganda; FE/parser memilih lewat `varian`); `ukuran` TIDAK berubah (kompatibel). Tool LLM search_items tak memuat `varian` (token).
(b) Race kode barang otomatis (BRG-NNNN = MAX+1) di tulis_barang_baru: kunci per tenant SEBELUM menghitung kode, di dalam transaksi; bentrok
indeks kode otomatis -> 409 KODE_BARANG_BENTROK (bukan pesan 'Kode item  sudah digunakan' berkode kosong / 500)."""
import inspect
import uuid
from decimal import Decimal
from types import SimpleNamespace

import asyncpg

from app.milkybook.v2 import cari_barang as CB
from app.milkybook.v2 import tafsir
from app.routers import items as I


# ---------------------------------------------------------------- (a)
V2 = [
    {"id": "i1", "nama": "Kaos S Hitam", "atribut": {"ukuran": "S", "warna": "Hitam"}, "harga": 75000},
    {"id": "i2", "nama": "Kaos S Putih", "atribut": '{"ukuran": "S", "warna": "Putih"}', "harga": Decimal("75000.50")},
    {"id": "i3", "nama": "Kaos Merah", "atribut": {"warna": "Merah"}, "harga": None},
]


def test_varian_lengkap_semua_anak_dengan_atribut_dan_harga():
    assert CB.varian_lengkap(V2) == [
        {"id": "i1", "nama": "Kaos S Hitam", "atribut": {"ukuran": "S", "warna": "Hitam"}, "harga": 75000},
        {"id": "i2", "nama": "Kaos S Putih", "atribut": {"ukuran": "S", "warna": "Putih"}, "harga": "75000.5"},
        {"id": "i3", "nama": "Kaos Merah", "atribut": {"warna": "Merah"}, "harga": None},
    ]


def test_varian_lengkap_teks_json_dan_kosong():
    import json
    assert CB.varian_lengkap(None) == [] and CB.varian_lengkap("[]") == [] and CB.varian_lengkap([]) == []
    assert CB.varian_lengkap(json.dumps([{"id": "x", "nama": "N", "atribut": {"ukuran": "M"}, "harga": 1000}]))[0]["atribut"] == {"ukuran": "M"}


def _baris(varian):
    return {"id": uuid.uuid4(), "nama": "Kaos", "kode": "BRG-0001", "satuan": "Pcs", "jenis": "goods", "harga": 75000, "kelas": 0, "skor": 0,
            "varian": varian}


def test_hasil_memuat_varian_dan_ukuran_lama_tak_berubah():
    h = CB.baris_ke_hasil(_baris(V2[:2]))
    assert [u["ukuran"] for u in h["ukuran"]] == ["S", "S"]            # perilaku lama (dua dimensi -> ganda) tetap, demi kompatibilitas
    assert [v["id"] for v in h["varian"]] == ["i1", "i2"] and h["varian"][0]["atribut"]["warna"] == "Hitam"
    kosong = CB.baris_ke_hasil(_baris(None))
    assert kosong["varian"] == [] and kosong["ukuran"] == []


def test_dengan_varian_dimatikan_untuk_tool_llm():
    assert "varian" not in CB.baris_ke_hasil(_baris(V2), dengan_varian=False)
    assert "dengan_varian=False" in inspect.getsource(tafsir.jalankan_tool)
    assert "dengan_varian" in inspect.signature(CB.cari).parameters


# ---------------------------------------------------------------- (b)
def test_tulis_barang_baru_mengunci_per_tenant_sebelum_menghitung_kode_dalam_transaksi():
    s = inspect.getsource(I.tulis_barang_baru)
    v, i, j, k = s.index("_siapkan_buat_barang("), s.index("async with conn.transaction():"), s.index("pg_advisory_xact_lock"), s.index("_kode_otomatis(")
    assert v < i < j < k and "ITEM_KODE:" in s    # validasi TETAP sebelum transaksi; kunci + hitung ulang kode otomatis di dalam
    assert "if not body.item_code:" in s and "_kode_otomatis(" in inspect.getsource(I._siapkan_buat_barang)


def _uv(nama_indeks):
    e = asyncpg.exceptions.UniqueViolationError("duplicate key")
    e.constraint_name = nama_indeks
    return e


def test_bentrok_kode_otomatis_409_berkode_coba_lagi():
    for indeks in ("idx_products_tenant_item_code", "idx_products_tenant_sku"):
        g = I.galat_bentrok_unik(_uv(indeks), SimpleNamespace(item_code=None, sku=None, name="X"))
        assert g.status_code == 409 and g.detail["code"] == "KODE_BARANG_BENTROK" and "Coba lagi" in g.detail["message"]


def test_bentrok_kode_eksplisit_tetap_pesan_lama():
    g = I.galat_bentrok_unik(_uv("idx_products_tenant_item_code"), SimpleNamespace(item_code="BRG-0001", sku=None, name="X"))
    assert g.status_code == 409 and g.detail["code"] == I.KODE_KODE_BARANG and "BRG-0001" in g.detail["message"]
    assert I.galat_bentrok_unik(ValueError("x"), SimpleNamespace(item_code=None, sku=None, name="X")) is None


def test_pemeriksaan_ganda_kode_hanya_untuk_nilai_dari_pengguna():
    """Kode/SKU OTOMATIS dihitung ulang di bawah kunci -> pemeriksaan ganda sebelum transaksi hanya bila pengguna mengisi kode/SKU."""
    s = inspect.getsource(I._siapkan_buat_barang)
    assert "if existing_sku and (body.sku or body.item_code):" in s
    assert "if existing_item_code and body.item_code:" in s
