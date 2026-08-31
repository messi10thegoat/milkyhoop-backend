"""
Regresi T179-Q3: `items` harus dideklarasikan ke model sebagai ARRAY.

Kelas bug yang ditangkap: `build_intent_schema` hanya memetakan
number/percent/boolean; `field_type="json"` jatuh ke cabang else dan menjadi
`["string","null"]`. Model lalu berhak mengisi `items` dengan PROSA, parse
gagal diam-diam, dan enricher mengarang satu baris dari field skalar —
barang kedua menguap.

Terukur di produksi 2026-08-30 SEBELUM perbaikan:
    [EXTRACT_S2] n_items=-3 tipe=str intent=create_bill   (4/4)
SESUDAH:
    [EXTRACT_S2] n_items=2 tipe=list intent=create_bill
"""
import sys
import pytest

sys.path.insert(0, "/app/backend/api_gateway")

from app.services.unified_agent.entity_extractor import build_intent_schema
from app.services.llm.gemini_client import GeminiClient


def properti(intent: str) -> dict:
    """Buka amplop skema dan kembalikan `properties`.

    ⚠️ AMPLOP, bukan skema telanjang. `build_intent_schema` mengembalikan
    bentuk gaya OpenAI:
        {"type":"json_schema","json_schema":{"name":..,"schema":{"properties":..}}}
    Membaca `skema["properties"]` langsung menghasilkan NOL yang meyakinkan —
    kesalahan itu terjadi saat menulis tes ini, dan ia satu keluarga dengan
    empat kegagalan alat yang sudah tercatat di proyek ini (payload Bill vs
    Quote vs review_card, amplop `items` vs `data`).
    """
    s = build_intent_schema(intent)
    assert s, f"skema {intent} kosong — tes tidak menguji apa pun"
    assert "json_schema" in s, (
        "bentuk amplop berubah — perbarui helper ini, JANGAN membaca "
        "properties dari tingkat atas"
    )
    return s["json_schema"]["schema"]["properties"]


def test_amplop_skema_seperti_yang_diasumsikan():
    """PENJAGA AMPLOP. Kalau bentuknya berubah, tes lain di berkas ini akan
    membaca tempat yang salah dan hijau tanpa arti. Ini yang gagal duluan."""
    s = build_intent_schema("create_bill")
    assert s["type"] == "json_schema"
    assert set(["name", "schema"]).issubset(s["json_schema"].keys())
    assert "properties" in s["json_schema"]["schema"]


def test_create_bill_items_adalah_array():
    p = properti("create_bill").get("items")
    assert p is not None, "kunci items tidak ada di skema create_bill"
    assert p.get("type") == "array", (
        f"items dideklarasikan sebagai {p.get('type')!r}, bukan 'array' — "
        "model berhak mengirim prosa dan barang kedua akan menguap (T181)"
    )
    baris = (p.get("items") or {}).get("properties") or {}
    # nama field per-baris untuk BILL — bukan skema Quote/SO/SI
    for wajib in ("product_name", "qty", "price"):
        assert wajib in baris, f"field baris {wajib!r} hilang dari skema Bill"


def test_array_lolos_clean_schema_tanpa_diruntuhkan():
    """`_clean_schema` meruntuhkan union type-list (Gemini menolaknya — terbukti
    live `400 INVALID_ARGUMENT, Proto field is not repeating`). Array TIDAK
    boleh ikut diruntuhkan."""
    dalam = build_intent_schema("create_bill")["json_schema"]["schema"]
    bersih = GeminiClient._clean_schema(dalam)
    p = bersih["properties"]["items"]
    assert p["type"] == "array", "array runtuh saat lewat _clean_schema"
    assert p["items"]["type"] == "object"
    assert "product_name" in p["items"]["properties"]


def test_clean_schema_memang_meruntuhkan_union():
    """KONTROL POSITIF untuk tes di atas. Tanpa ini, 'array lolos' bisa hijau
    semata karena `_clean_schema` tidak melakukan apa-apa."""
    contoh = {"type": "object", "properties": {"x": {"type": ["string", "null"]}}}
    bersih = GeminiClient._clean_schema(contoh)
    assert bersih["properties"]["x"]["type"] == "string", (
        "_clean_schema tidak meruntuhkan union — kontrol ini tidak menguji apa pun"
    )


def test_create_sales_invoice_items_adalah_array():
    """T182-A. Sebelum ini `items` create_sales_invoice = ["string","null"] —
    terukur dari dump skema di worktree /root/mh-t182a sebelum patch."""
    p = properti("create_sales_invoice").get("items")
    assert p is not None, "kunci items tidak ada di skema create_sales_invoice"
    assert p.get("type") == "array", (
        f"items dideklarasikan sebagai {p.get('type')!r}, bukan 'array' — "
        "model berhak mengirim prosa, json.loads gagal diam-diam, dan "
        "scalar-fallback mengarang {description:'Item', quantity:1, unit_price:0}"
    )
    baris = (p.get("items") or {}).get("properties") or {}
    # nama field per-baris untuk SALES INVOICE — BUKAN skema Bill.
    # Diverifikasi dari 272 baris pending_actions.action_plan->items di
    # produksi: quantity/unit_price/description ada di 305 baris item.
    for wajib in ("description", "quantity", "unit_price"):
        assert wajib in baris, f"field baris {wajib!r} hilang dari skema SI"
    for asing in ("product_name", "qty", "price"):
        assert asing not in baris, (
            f"{asing!r} adalah nama field BILL — jalur hilir sales invoice "
            "(_enrich_items, scalar-fallback) tidak mengenalinya"
        )


def test_skema_bill_tidak_ikut_bergerak():
    """RADIUS. T182-A menyentuh SATU aksi. Kalau skema Bill ikut berubah,
    patch bocor keluar radius."""
    baris = (
        properti("create_bill")["items"]["items"]["properties"]
    )
    assert set(["product_name", "qty", "price"]).issubset(baris.keys())
    assert "unit_price" not in baris, "nama field SI bocor ke skema Bill"


def test_array_si_lolos_clean_schema_tanpa_diruntuhkan():
    dalam = build_intent_schema("create_sales_invoice")["json_schema"]["schema"]
    bersih = GeminiClient._clean_schema(dalam)
    p = bersih["properties"]["items"]
    assert p["type"] == "array", "array SI runtuh saat lewat _clean_schema"
    assert p["items"]["type"] == "object"
    assert "description" in p["items"]["properties"]
    # union per-baris ikut diruntuhkan (bukti rekursi benar-benar masuk)
    assert p["items"]["properties"]["unit"]["type"] == "string"


def test_create_sales_order_items_adalah_array():
    """T182-C. Sebelum ini `items` create_sales_order = ["string","null"] —
    terukur dari dump skema BASELINE seluruh 61 aksi di worktree ini."""
    p = properti("create_sales_order").get("items")
    assert p is not None, "kunci items tidak ada di skema create_sales_order"
    assert p.get("type") == "array", (
        f"items dideklarasikan sebagai {p.get('type')!r}, bukan 'array' — "
        "model berhak mengirim prosa, json.loads gagal diam-diam, dan "
        "scalar-fallback mengarang satu baris palsu"
    )
    baris = (p.get("items") or {}).get("properties") or {}
    # Nama field per-baris untuk SALES ORDER — BUKAN skema Bill.
    # Sensus produksi pending_actions.action_plan->items CREATE_SALES_ORDER:
    # item_id 255, description 255, quantity 255, unit 255, unit_price 172.
    for wajib in ("description", "quantity", "unit_price"):
        assert wajib in baris, f"field baris {wajib!r} hilang dari skema SO"
    for asing in ("product_name", "qty", "price"):
        assert asing not in baris, (
            f"{asing!r} adalah nama field BILL — jalur hilir sales order "
            "(_enrich_sales_order, _enrich_items) tidak mengenalinya"
        )
    # item_id sengaja TIDAK dideklarasikan ke model: UUID tak bisa dikarang.
    assert "item_id" not in baris, (
        "item_id dideklarasikan ke model — model akan mengarang UUID; "
        "resolusinya tugas _enrich_items lewat description"
    )


def test_array_so_lolos_clean_schema_tanpa_diruntuhkan():
    dalam = build_intent_schema("create_sales_order")["json_schema"]["schema"]
    bersih = GeminiClient._clean_schema(dalam)
    p = bersih["properties"]["items"]
    assert p["type"] == "array", "array SO runtuh saat lewat _clean_schema"
    assert p["items"]["type"] == "object"
    assert "description" in p["items"]["properties"]
    # union per-baris ikut diruntuhkan (bukti rekursi benar-benar masuk)
    assert p["items"]["properties"]["unit"]["type"] == "string"


def test_create_quote_items_adalah_array():
    """T182-C. Sebelum ini `items` create_quote = ["string","null"] — terukur
    dari dump skema BASELINE seluruh 61 aksi di worktree ini."""
    p = properti("create_quote").get("items")
    assert p is not None, "kunci items tidak ada di skema create_quote"
    assert p.get("type") == "array", (
        f"items dideklarasikan sebagai {p.get('type')!r}, bukan 'array' — "
        "model berhak mengirim prosa, json.loads gagal diam-diam, dan "
        "scalar-fallback mengarang satu baris palsu"
    )
    baris = (p.get("items") or {}).get("properties") or {}
    # Nama field per-baris untuk QUOTE — BUKAN skema Bill.
    # Sensus produksi pending_actions.action_plan->items CREATE_QUOTE:
    # description 1383, quantity 1285, unit 1284, item_id 1179, unit_price 935.
    for wajib in ("description", "quantity", "unit_price"):
        assert wajib in baris, f"field baris {wajib!r} hilang dari skema Quote"
    for asing in ("product_name", "qty", "price"):
        assert asing not in baris, (
            f"{asing!r} adalah nama field BILL — jalur hilir quote "
            "(_enrich_quote, _enrich_items) tidak mengenalinya"
        )
    # item_id sengaja TIDAK dideklarasikan ke model: UUID tak bisa dikarang.
    assert "item_id" not in baris, (
        "item_id dideklarasikan ke model — model akan mengarang UUID; "
        "resolusinya tugas _enrich_items lewat description"
    )


def test_array_quote_lolos_clean_schema_tanpa_diruntuhkan():
    dalam = build_intent_schema("create_quote")["json_schema"]["schema"]
    bersih = GeminiClient._clean_schema(dalam)
    p = bersih["properties"]["items"]
    assert p["type"] == "array", "array Quote runtuh saat lewat _clean_schema"
    assert p["items"]["type"] == "object"
    assert "description" in p["items"]["properties"]
    # union per-baris ikut diruntuhkan (bukti rekursi benar-benar masuk)
    assert p["items"]["properties"]["unit"]["type"] == "string"


AKSI_TAK_DIUBAH = [
    # T182-A: `create_sales_invoice` DIKELUARKAN dari daftar ini secara sadar.
    # T182-C: `create_sales_order` DAN `create_quote` DIKELUARKAN — keputusan
    # sadar, dua commit terpisah, masing-masing dengan tes positifnya sendiri
    # (test_create_sales_order_items_adalah_array,
    # test_create_quote_items_adalah_array).
    "create_stock_adjustment",
    "create_journal_entry",
]


@pytest.mark.parametrize("intent", AKSI_TAK_DIUBAH)
def test_radius_aksi_lain_belum_memakai_array(intent):
    """Radius T179-Q3 sengaja SATU aksi.

    Aksi lain masih memakai deklarasi string; mereka bekerja hari ini karena
    model KEBETULAN mengembalikan JSON valid di dalam string — keberuntungan
    yang sama yang dulu menutupi create_bill.

    Ini PENGINGAT BERUMUR, bukan persetujuan: saat sebuah aksi diperluas ke
    array, hapus dari daftar supaya keputusannya sadar, bukan tak sengaja.
    """
    try:
        props = properti(intent)
    except AssertionError:
        pytest.skip(f"{intent} tak punya skema")
    for kunci in ("items", "lines"):
        p = props.get(kunci)
        if p is None:
            continue
        assert p.get("type") != "array", (
            f"{intent}.{kunci} kini array — bagus, tapi hapus dari "
            "AKSI_TAK_DIUBAH dan pastikan ia punya gate produksinya sendiri"
        )
