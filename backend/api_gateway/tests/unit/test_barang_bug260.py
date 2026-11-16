"""BUG-260 sisi BE (16 Nov 2026): GET /api/items kirim teks server per baris `stok_fisik_text` ("172 pcs") dan `bisa_dijual_text`
(selalu terisi untuk barang ber-stok) + sort_by=bisa_dijual (lintas halaman, dari SATU penentu stok_baris). Tanpa migrasi."""
import pathlib
from decimal import Decimal as D

from app.schemas import items as S
from app.services import barang_daftar as BD

RUTE = pathlib.Path(__file__).resolve().parents[2] / "app" / "routers" / "items.py"
LACAK = {"item_type": "goods", "track_inventory": True, "status": "active"}


def test_stok_fisik_text_barang_dilacak():
    assert BD.stok_fisik_text(LACAK, D("172"), "pcs") == "172 pcs"
    assert BD.stok_fisik_text(LACAK, D("1500"), "kg") == "1.500 kg"
    assert BD.stok_fisik_text(LACAK, D("1.5"), "kg") == "1,5 kg"
    assert BD.stok_fisik_text(LACAK, D("0"), "pcs") == "0 pcs"
    assert BD.stok_fisik_text(LACAK, D("-3"), "pcs") == "-3 pcs"
    assert BD.stok_fisik_text(LACAK, D("5"), None) == "5"


def test_bisa_dijual_text_selalu_terisi_untuk_barang_dilacak():
    assert BD.bisa_dijual_text(LACAK, D("172"), D("160"), "pcs") == "160 pcs"
    assert BD.bisa_dijual_text(LACAK, D("172"), D("172"), "pcs") == "172 pcs"  # sama dgn stok: tetap terisi (beda dgn position_text_ket)
    assert BD.position_text_ket(LACAK, D("172"), D("172"), "pcs") is None
    assert BD.bisa_dijual_text(LACAK, D("0"), D("0"), "pcs") == "0 pcs"
    assert BD.bisa_dijual_text(LACAK, D("172"), None, "pcs") == "172 pcs"  # penentu tak tersedia -> jatuh ke stok fisik


def test_teks_stok_kosong_untuk_barang_tak_dilacak():
    jasa = {"item_type": "service", "track_inventory": False, "status": "active"}
    tanpa_stok = {"item_type": "goods", "track_inventory": False, "status": "active"}
    nonaktif = {"item_type": "goods", "track_inventory": True, "status": "inactive"}
    for b in (jasa, tanpa_stok, nonaktif):
        assert BD.stok_fisik_text(b, D("5"), "pcs") is None
        assert BD.bisa_dijual_text(b, D("5"), D("4"), "pcs") is None


def test_skema_baris_punya_dua_teks_baru_default_none():
    f = S.ItemListItem.model_fields
    assert "stok_fisik_text" in f and "bisa_dijual_text" in f
    assert f["stok_fisik_text"].default is None and f["bisa_dijual_text"].default is None


def test_rute_sort_bisa_dijual_dan_teks_baris_terpasang():
    src = RUTE.read_text()
    assert 'bisa_dijual"' in src and 'sort_by' in src
    assert "== \"bisa_dijual\"" in src            # cabang urut khusus
    assert "SB.hitung(conn, tenant_id, dil_semua" in src  # SATU panggilan untuk semua baris tersaring, bukan N+1
    assert "array_position(" in src               # halaman = potongan id yang sudah terurut
    assert "BD.stok_fisik_text(" in src and "BD.bisa_dijual_text(" in src
    assert "params.extend([limit, offset_sql])" in src and "has_more=(offset + limit)" in src  # has_more tetap pakai offset asli
