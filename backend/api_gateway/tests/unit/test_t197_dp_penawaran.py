"""T197 — DP penawaran: field terstruktur + nomor dokumen milik generator.

Setiap tes di berkas ini sudah dibuktikan BISA MERAH lewat sabotase pada
kode produksi (bukan pada tes), lalu dipulihkan. Lihat laporan tiket.
"""

from app.services.unified_agent.direct_action_registry import (
    get_direct_action,
    build_review_card_payload,
    validate_payload,
)
from app.services.unified_agent.entity_extractor import build_intent_schema
from app.services.unified_agent.tool_executor import _amankan_nomor_dokumen


def _fields():
    return {f.name: f for f in get_direct_action("create_quote").fields}


# ─────────────────────────── U1 ───────────────────────────
def test_u1_create_quote_punya_dp_percent_dan_dp_amount():
    f = _fields()
    assert "dp_percent" in f, "FieldSpec dp_percent tidak ada di create_quote"
    assert "dp_amount" in f, "FieldSpec dp_amount tidak ada di create_quote"
    assert f["dp_percent"].required is False
    assert f["dp_amount"].required is False
    # Tak satu pun boleh hidden — kalau hidden, kartu tidak menampilkannya
    # dan kita kembali ke keadaan "DP tak terlihat".
    assert f["dp_percent"].hidden is False
    assert f["dp_amount"].hidden is False


def test_u1b_dp_dideklarasikan_ke_model_sebagai_angka():
    """Schema ekstraksi Stage-2 harus memuat kedua field sebagai number."""
    schema = build_intent_schema("create_quote")
    props = schema["json_schema"]["schema"]["properties"]
    assert props["dp_percent"]["type"] == ["number", "null"]
    assert props["dp_amount"]["type"] == ["number", "null"]
    # Instruksi anti-notes harus sampai ke model.
    assert "notes" in props["dp_percent"]["description"]
    assert "notes" in props["dp_amount"]["description"]


# ─────────────────────────── U2 ───────────────────────────
def _payload_dasar(**extra):
    p = {
        "customer_id": "11111111-1111-1111-1111-111111111111",
        "customer_name": "Toko Merdeka",
        "quote_date": "2026-09-01",
        "items": [{"description": "Kaos Biru 30s", "quantity": 3, "unit_price": 90000}],
    }
    p.update(extra)
    return p


def test_u2_dp_percent_lolos_validasi_dan_bertahan():
    payload = _payload_dasar(dp_percent=60)
    ok, _missing = validate_payload("create_quote", payload)
    assert ok, f"payload ber-DP ditolak validasi: {_missing}"

    # Jalur nomor-dokumen tidak boleh membuang DP.
    _amankan_nomor_dokumen(payload, "create_quote")
    assert payload["dp_percent"] == 60

    # Jalur konfirmasi hanya membuang field display_only (unified_chat.py).
    cfg = get_direct_action("create_quote")
    display_only = {f.name for f in cfg.fields if f.display_only}
    body = {k: v for k, v in payload.items() if k not in display_only}
    assert body["dp_percent"] == 60, "dp_percent tidak sampai ke body POST /api/quotes"


def test_u2b_kartu_menampilkan_dp_sebagai_baris_sendiri():
    card = build_review_card_payload("create_quote", _payload_dasar(dp_percent=60))
    baris = {h["key"]: h for h in card["header"]}
    assert "dp_percent" in baris, "kartu tidak punya baris DP"
    assert baris["dp_percent"]["value"] == "60%"
    assert baris["dp_percent"]["label"] == "DP (%)"

    card2 = build_review_card_payload("create_quote", _payload_dasar(dp_amount=5000000))
    baris2 = {h["key"]: h for h in card2["header"]}
    assert baris2["dp_amount"]["value"] == "Rp 5.000.000"


# ─────────────────────────── U3 ───────────────────────────
def test_u3_quote_number_dari_payload_tidak_dipakai():
    prosa = "dp 60 persen untuk bunaken oasis\n1. kaos 20s 100 pcs"
    payload = _payload_dasar(quote_number=prosa, dp_percent=60)
    _amankan_nomor_dokumen(payload, "create_quote")
    assert "quote_number" not in payload, (
        "quote_number dari payload masih hidup — nomor dokumen bisa dicemari model"
    )
    # DP tidak boleh ikut jadi korban.
    assert payload["dp_percent"] == 60

    cfg = get_direct_action("create_quote")
    assert cfg.get_entity_name(payload) == "", (
        "nama entitas kartu masih dibaca dari payload"
    )
    assert "quote_number" not in {f.name for f in cfg.fields}


def test_u3b_guard_tidak_mengganggu_aksi_lain():
    """Pagar create_quote tidak boleh menyentuh payload aksi lain."""
    p = {"quote_number": "QUO-1", "customer_name": "X"}
    _amankan_nomor_dokumen(p, "create_sales_invoice")
    assert p["quote_number"] == "QUO-1"


# ─────────────────────────── U4 (kontrol negatif) ───────────────────────────
def test_u4_payload_tanpa_dp_tetap_sah_dan_kartu_tak_punya_baris_dp():
    payload = _payload_dasar()
    ok, missing = validate_payload("create_quote", payload)
    assert ok, f"payload tanpa DP ditolak: {missing}"
    _amankan_nomor_dokumen(payload, "create_quote")
    assert "dp_percent" not in payload
    assert "dp_amount" not in payload

    card = build_review_card_payload("create_quote", payload)
    kunci = {h["key"] for h in card["header"]}
    assert "dp_percent" not in kunci
    assert "dp_amount" not in kunci


def test_u4b_dp_nol_tidak_memunculkan_baris():
    card = build_review_card_payload(
        "create_quote", _payload_dasar(dp_percent=0, dp_amount=0)
    )
    kunci = {h["key"] for h in card["header"]}
    assert "dp_percent" not in kunci
    assert "dp_amount" not in kunci


# ─────────────────────────── U5 (template PDF) ───────────────────────────
def _render_quote(**quote_extra):
    # Jinja env NYATA milik PDFService — filter currency/date_id yang sama
    # yang dipakai endpoint GET /api/quotes/{id}/pdf. Sengaja TIDAK memakai
    # env buatan sendiri: filter tiruan bisa membuat tes lulus atas template
    # yang di produksi justru meledak.
    from app.services.pdf_service import PDFService

    tpl = PDFService().jinja_env.get_template("quote.html")
    quote = {
        "quote_number": "QUO-2609-0001",
        "quote_date": "2026-09-01",
        "customer_name": "Toko Merdeka",
        "items": [],
        "subtotal": 270000,
        "total_amount": 270000,
        "dp_amount": None,
        "dp_percent": None,
        "dp_remaining": None,
    }
    quote.update(quote_extra)
    return tpl.render(quote=quote, tenant={}, company={})


def test_u5_template_menampilkan_baris_dp():
    html = _render_quote(dp_amount=162000, dp_percent=60, dp_remaining=108000)
    assert "Uang Muka" in html
    assert "(60%)" in html
    assert "162.000" in html


def test_u5b_template_tanpa_dp_tidak_memuat_baris_dp():
    html = _render_quote()
    assert "Uang Muka" not in html
