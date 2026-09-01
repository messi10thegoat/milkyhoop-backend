"""T205 (kelas T182) — `create_item.items` harus ARRAY OBJEK, bukan string.

Terukur di produksi sebelum perbaikan:
    [EXTRACT_S2] n_items=-3 tipe=str intent=create_item
    [T144_BULK] items string gagal di-parse: head='Jersey A, Jersey B'
Harga per baris hilang -> `items` di-pop -> jalur skalar tanpa harga ->
at_least_one_groups gagal -> bot balik bertanya harga.

create_bill / create_sales_invoice / create_sales_order / create_quote sudah
punya item_schema; create_item satu-satunya yang tertinggal.

Tiap tes di berkas ini SUDAH DIBUKTIKAN BISA MERAH lewat sabotase pada KODE
PRODUKSI (bukan pada tes), lalu dipulihkan.
"""
import sys

import pytest

sys.path.insert(0, "/app/backend/api_gateway")

from app.services.unified_agent.direct_action_registry import (  # noqa: E402
    DIRECT_ACTIONS,
    T144_FIELD_BARIS,
    t144_baris_bisa_dibuat,
)
from app.services.unified_agent.entity_extractor import (  # noqa: E402
    build_intent_schema,
)


def _fieldspec(action_key: str, name: str):
    cfg = DIRECT_ACTIONS[action_key]
    for f in cfg.fields:
        if f.name == name:
            return f
    raise AssertionError(f"{action_key} tak punya FieldSpec {name!r}")


def _properti(intent: str) -> dict:
    s = build_intent_schema(intent)
    assert s, f"skema {intent} kosong — tes tidak menguji apa pun"
    assert "json_schema" in s, "bentuk amplop berubah — perbarui helper"
    return s["json_schema"]["schema"]["properties"]


def test_create_item_items_punya_item_schema():
    f = _fieldspec("create_item", "items")
    assert f.item_schema, (
        "create_item.items TANPA item_schema -> build_intent_schema "
        "mendeklarasikannya sebagai string ke model (akar T205)"
    )
    assert f.item_schema.get("type") == "object"
    assert isinstance(f.item_schema.get("properties"), dict)


def test_items_sampai_ke_model_sebagai_array():
    """Yang menentukan bukan FieldSpec, tapi apa yang Gemini terima."""
    prop = _properti("create_item")["items"]
    assert prop.get("type") == "array", (
        f"model diberi tahu items bertipe {prop.get('type')!r} — "
        "inilah yang memproduksi [EXTRACT_S2] tipe=str"
    )
    assert prop["items"].get("type") == "object"


def test_kunci_baris_sama_dengan_yang_dibaca_hilir():
    """Kesamaan HIMPUNAN terhadap T144_FIELD_BARIS — bukan hardcode ulang.

    Kalau item_schema memakai nama lain, parse berhasil tapi hilir tetap
    gagal (baris dibuang oleh filter nama_produk / at_least_one).
    """
    props = set(_properti("create_item")["items"]["items"]["properties"])
    hilir = set(T144_FIELD_BARIS)
    assert hilir <= props, (
        "kunci yang dibaca hilir tidak dideklarasikan ke model: "
        f"{sorted(hilir - props)}"
    )


def test_nama_produk_wajib_di_skema_baris():
    baris_schema = _properti("create_item")["items"]["items"]
    assert "nama_produk" in (baris_schema.get("required") or []), (
        "orchestrator._t144_normalisasi_items MEMBUANG baris tanpa "
        "nama_produk; ia wajib di required"
    )


@pytest.mark.parametrize("kunci", ["sales_price", "purchase_price"])
def test_harga_baris_bertipe_number_bukan_string(kunci):
    tipe = _properti("create_item")["items"]["items"]["properties"][kunci]["type"]
    tipe_set = set(tipe) if isinstance(tipe, list) else {tipe}
    assert "number" in tipe_set, f"{kunci} bertipe {tipe!r}, harga harus number"
    assert "string" not in tipe_set, (
        f"{kunci} boleh string -> model kirim 'Rp 65.000' -> hilir "
        "t144_baris_bisa_dibuat/POST gagal"
    )


def test_baris_hasil_bentuk_skema_diterima_hilir():
    """Kontrol END-TO-END kecil: baris yang PATUH pada item_schema harus
    lolos t144_baris_bisa_dibuat (dengan base_unit yang diisi-turun)."""
    baris = {
        "nama_produk": "Jersey Uji T205 Reguler",
        "purchase_price": 65000,
        "sales_price": 155000,
        "base_unit": "pcs",
        "item_type": "persediaan",
    }
    assert t144_baris_bisa_dibuat(baris)


def test_kontrol_positif_aksi_lain_tidak_rusak():
    """create_quote (diperbaiki lebih dulu) tetap array — bukti perubahan
    ini tidak menyentuh aksi lain."""
    for intent, kunci in (
        ("create_quote", "description"),
        ("create_bill", "product_name"),
    ):
        prop = _properti(intent)["items"]
        assert prop.get("type") == "array", f"{intent} rusak"
        assert kunci in prop["items"]["properties"], f"{intent} kehilangan {kunci}"
