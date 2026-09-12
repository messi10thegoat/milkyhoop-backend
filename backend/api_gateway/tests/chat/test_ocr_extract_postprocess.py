"""fix/ocr-nota-tulisan-tangan — unit murni (tanpa LLM, tanpa DB) untuk
post-processing deterministik ekstraksi dokumen. Tiap aturan punya kasus
yang HARUS tidak berubah (no-op / flag) dan kasus yang HARUS dikoreksi."""
from datetime import date

import pytest

from app.services.unified_agent.ocr_extract import (
    build_ocr_prompt,
    display_party,
    extraction_notes_text,
    parse_ocr_json,
    postprocess_ocr,
)

TODAY = date(2026, 9, 2)


def _nota(**over):
    """Gejala 2 Sep 2026: LLM mengambil harga satuan sebagai total."""
    d = {
        "doc_type": "receipt",
        "vendor_name": "unknown",
        "customer_name": "BEM TRINITA",
        "document_date": "2016-08-10",
        "total_amount": 165000,
        "grand_total": 4125000,
        "line_items": [
            {"qty": 25, "name": "Jersy Baseball", "unit_price": 165000, "line_total": None}
        ],
        "extracted_text": "10 08 2026 | BEM TRINITA | 25 Jersy Baseball 165.000 | LUNAS 10 AUG 2026 | Jumlah Rp 4.125.000",
        "confidence": 0.9,
    }
    d.update(over)
    return d


# ── (i) total dari line_items ──────────────────────────────────────────────


def test_gejala_2sep_total_satuan_dikoreksi_ke_sigma():
    d = postprocess_ocr(_nota(), TODAY)
    assert d["total_amount"] == 4125000
    assert d["total_source"] == "line_items"
    assert d["total_flag"] is None
    assert "Total dihitung dari 25 × 165.000 = 4.125.000" in extraction_notes_text(d)


def test_total_satuan_tanpa_grand_total_tetap_dikoreksi_karena_qty_gt_1():
    # bukti B: total == unit_price sebuah baris ber-qty>1
    d = postprocess_ocr(_nota(grand_total=None), TODAY)
    assert d["total_amount"] == 4125000
    assert d["total_source"] == "line_items"


def test_qty_1_tidak_boleh_diubah():
    d = postprocess_ocr(
        _nota(
            total_amount=165000,
            grand_total=None,
            line_items=[{"qty": 1, "name": "Jersy", "unit_price": 165000}],
        ),
        TODAY,
    )
    assert d["total_amount"] == 165000
    assert d["total_source"] == "ocr"
    assert d["total_flag"] is None
    assert extraction_notes_text(d) == ""


def test_sigma_beda_tanpa_grand_total_tanpa_bukti_satuan_hanya_flag():
    d = postprocess_ocr(
        _nota(
            total_amount=999000,  # ≠ Σ(4.125.000), ≠ unit_price
            grand_total=None,
        ),
        TODAY,
    )
    assert d["total_amount"] == 999000  # JANGAN koreksi
    assert d["total_source"] == "ocr"
    assert d["total_flag"] == "line_items_mismatch"
    assert "mohon periksa" in extraction_notes_text(d)


def test_total_sudah_benar_noop():
    d = postprocess_ocr(_nota(total_amount=4125000, document_date="2026-08-10"), TODAY)
    assert d["total_amount"] == 4125000
    assert d["total_source"] == "ocr"
    assert d["date_flag"] is None
    assert d["extraction_notes"] == []


def test_multi_baris_struk_cetak_sigma_sama_total_noop():
    d = postprocess_ocr(
        {
            "doc_type": "expense",
            "total_amount": 30000,
            "grand_total": 30000,
            "line_items": [
                {"qty": 2, "name": "Kopi", "unit_price": 10000},
                {"qty": 1, "name": "Roti", "unit_price": 10000},
            ],
            "document_date": "2026-09-01",
        },
        TODAY,
    )
    assert d["total_amount"] == 30000 and d["total_source"] == "ocr"


def test_bukti_transfer_tanpa_line_items_tidak_tersentuh():
    d = postprocess_ocr(
        {"doc_type": "bank_transfer", "total_amount": 165000, "line_items": [],
         "grand_total": None, "document_date": "2026-08-13", "vendor_name": "ANTHONIUS"},
        TODAY,
    )
    assert d["total_amount"] == 165000 and d["total_source"] == "ocr"
    assert d["document_date"] == "2026-08-13" and d["vendor_name"] == "ANTHONIUS"


def test_total_nol_ambil_grand_total():
    d = postprocess_ocr({"total_amount": 0, "grand_total": 250000, "line_items": []}, TODAY)
    assert d["total_amount"] == 250000 and d["total_source"] == "grand_total"


def test_angka_string_format_indonesia():
    d = postprocess_ocr(
        _nota(total_amount="165.000", grand_total="4.125.000,-",
              line_items=[{"qty": "25", "unit_price": "165.000"}]),
        TODAY,
    )
    assert d["total_amount"] == 4125000


# ── (ii) tahun ─────────────────────────────────────────────────────────────


def test_tahun_2016_dengan_konteks_2026_dikoreksi():
    d = postprocess_ocr(_nota(), TODAY)
    assert d["document_date"] == "2026-08-10"
    assert d["date_flag"] == "year_from_context"
    assert "dikoreksi ke 2026" in extraction_notes_text(d)


def test_tahun_2016_tanpa_konteks_tetap_tapi_diflag():
    d = postprocess_ocr(_nota(extracted_text="nota 25 jersy"), TODAY)
    assert d["document_date"] == "2016-08-10"
    assert d["date_flag"] == "old_year"


def test_tahun_1926_konteks_2026_plus_100():
    d = postprocess_ocr(_nota(document_date="1926-08-10"), TODAY)
    assert d["document_date"] == "2026-08-10"
    assert d["date_flag"] == "century_fixed"


def test_tahun_1999_tanpa_konteks_null():
    d = postprocess_ocr(_nota(document_date="1999-08-10", extracted_text="nota"), TODAY)
    assert d["document_date"] is None
    assert d["date_flag"] == "ambiguous_year"


def test_tahun_1999_plus_100_melebihi_hari_ini_plus_1_null():
    # 2099 > 2027 walau konteks menyebut 2099
    d = postprocess_ocr(_nota(document_date="1999-08-10", extracted_text="stempel 2099"), TODAY)
    assert d["document_date"] is None and d["date_flag"] == "ambiguous_year"


def test_tanggal_format_ddmmyy_dinormalkan():
    d = postprocess_ocr(_nota(document_date="10-08-26", total_amount=4125000), TODAY)
    assert d["document_date"] == "2026-08-10"


def test_tanggal_masa_depan_null():
    d = postprocess_ocr(_nota(document_date="2031-01-01"), TODAY)
    assert d["document_date"] is None and d["date_flag"] == "future_year"


def test_tanggal_tak_terbaca_null():
    d = postprocess_ocr(_nota(document_date="kemarin"), TODAY)
    assert d["document_date"] is None and d["date_flag"] == "unparseable"


# ── (iii) nama ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", ["unknown", "Unknown", "null", "-", "", "Tuan/Toko", None])
def test_vendor_unknown_jadi_none(raw):
    d = postprocess_ocr({"vendor_name": raw, "customer_name": raw}, TODAY)
    assert d["vendor_name"] is None and d["customer_name"] is None
    assert display_party(raw) == "—"


def test_vendor_nyata_dipertahankan():
    d = postprocess_ocr({"vendor_name": " grapgrap clothing "}, TODAY)
    assert d["vendor_name"] == "grapgrap clothing"
    assert display_party("grapgrap clothing") == "grapgrap clothing"


# ── parse JSON terpotong ───────────────────────────────────────────────────


def test_parse_json_terpotong_masih_menyelamatkan_field_kunci():
    s = ('```json\n{\n  "doc_type": "receipt",\n  "total_amount": 4125000,\n'
         '  "document_date": "2026-08-10",\n  "extracted_text": "10 08 2026\\n\\n\\n\\n\\n')
    d = parse_ocr_json(s)
    assert d["doc_type"] == "receipt" and d["total_amount"] == 4125000
    assert d["_parse_error"]
    d = postprocess_ocr(d, TODAY)
    assert d["confidence"] <= 0.5
    assert "terpotong" in extraction_notes_text(d)


def test_parse_json_rusak_total_kembalikan_dict_kosong_bukan_raise():
    d = parse_ocr_json("bukan json sama sekali")
    assert d.get("_parse_error") and d.get("doc_type") is None


def test_parse_json_normal():
    assert parse_ocr_json('{"a": 1}') == {"a": 1}


# ── prompt ─────────────────────────────────────────────────────────────────


def test_prompt_memuat_aturan_nota_dan_caption():
    p = build_ocr_prompt('Bukti "x"')
    for frasa in ("GRAND TOTAL", "line_items", "grand_total", "harga SATUAN",
                  "dd-mm-yyyy", "Tahun 2 digit", "JANGAN mengarang", 'Bukti "x"'):
        assert frasa in p, frasa
    # extracted_text di AKHIR skema — supaya loop "\n\n" tak memakan field kunci
    assert p.index('"extracted_text"') > p.index('"total_amount"')
