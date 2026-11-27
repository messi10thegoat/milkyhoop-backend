"""U-V3 varian di daftar barang (DESAIN-varian §1 baris 'daftar barang'): GET /api/items?varian=induk|anak|tanpa_anak +
kolom is_matrix_parent / matrix_parent_id / nama_induk_text / atribut_text per baris (teks server)."""
import inspect
import json
import uuid

from app.routers import items as I
from app.schemas.items import ItemListItem
from app.services import barang_varian as BV


def test_kolom_varian_anak_induk_dan_biasa():
    pid = uuid.uuid4()
    anak = {"matrix_parent_id": pid, "matrix_attributes": json.dumps({"ukuran": "M", "warna": "Hitam"}), "nama_induk": "Kaos"}
    assert BV.kolom_varian_daftar(anak) == {"is_matrix_parent": False, "matrix_parent_id": str(pid), "nama_induk_text": "Kaos",
                                            "atribut_text": "M · Hitam"}
    assert BV.kolom_varian_daftar({"is_matrix_parent": True}) == {"is_matrix_parent": True, "matrix_parent_id": None,
                                                                  "nama_induk_text": None, "atribut_text": None}
    assert BV.kolom_varian_daftar({})["is_matrix_parent"] is False


def test_saringan_varian_dan_kolom_di_skema():
    src = inspect.getsource(I.list_items)
    for frag in ('"induk": "AND COALESCE(p.is_matrix_parent, false)"', '"anak": "AND p.matrix_parent_id IS NOT NULL"',
                 '"tanpa_anak": "AND p.matrix_parent_id IS NULL"', "_BV.kolom_varian_daftar(row)", "ip.tenant_id = p.tenant_id"):
        assert frag in src, frag
    for f in ("is_matrix_parent", "matrix_parent_id", "nama_induk_text", "atribut_text"):
        assert f in ItemListItem.model_fields
