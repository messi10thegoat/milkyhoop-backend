"""BUG-385/332/379: saringan daftar+summary Penyesuaian Stok (SATU predikat), items_summary_text, warehouse_name, filter_tersedia, reason_text."""
import asyncio
import inspect
from datetime import date

import pytest
from fastapi import HTTPException

import app.routers.stock_adjustments as SA
from app.schemas import stock_adjustments as S
from app.services import stok_sesuai as SS


def test_syarat_saring_kosong_dan_all():
    assert SS.syarat_saring("all", None, None, None, None, 2) == ([], [])
    assert SS.syarat_saring(None, None, None, None, None, 2) == ([], [])


def test_syarat_saring_semua_dan_penomoran():
    wid = "11111111-1111-1111-1111-111111111111"
    c, p = SS.syarat_saring("posted", "damaged", wid, date(2026, 11, 1), date(2026, 11, 30), 4)
    assert p == ["posted", "damaged", wid, date(2026, 11, 1), date(2026, 11, 30)]
    j = " AND ".join(c)
    for n in ("$4", "$5", "$6", "$7", "$8"):
        assert n in j
    assert "stock_adjustments.status = $4" in j and "adjustment_type = $5" in j
    assert "stock_adjustments.warehouse_id = $6" in j and "wi.warehouse_id = $6" in j      # header ATAU baris
    assert "adjustment_date >= $7" in j and "adjustment_date <= $8" in j


def test_syarat_saring_alias():
    c, _ = SS.syarat_saring("draft", None, None, None, None, 2, a="x")
    assert c == ["x.status = $2"]


def test_filter_tersedia_nilai_dan_label():
    f = SS.filter_tersedia()
    assert [x["nilai"] for x in f["status"]] == ["all", "draft", "posted", "void"]
    assert {x["label"] for x in f["adjustment_type"]} == {"Penambahan", "Pengurangan", "Hitung ulang", "Rusak", "Kedaluwarsa"}
    assert f["status"][1]["label"] == "Draf"


def test_daftar_dan_summary_memakai_syarat_saring_yang_sama():
    sl, ss = inspect.getsource(SA.list_stock_adjustments), inspect.getsource(SA.get_stock_adjustments_summary)
    assert "SS.syarat_saring(status, adjustment_type, warehouse_id, date_from, date_to" in sl
    assert ss.count("SS.syarat_saring(status, adjustment_type, warehouse_id, date_from, date_to") == 2   # tabel + kartu/pill
    assert "SS.filter_tersedia()" in sl and "SS.ringkas_baris" in sl
    for src in (sl, ss):
        assert "Tanggal awal tidak boleh setelah tanggal akhir" in src


def test_status_asing_422_lewat_literal():
    ann = inspect.signature(SA.list_stock_adjustments).parameters["status"].annotation
    assert "Literal" in str(ann) and "ngawur" not in str(ann)
    assert "warehouse_id" in inspect.signature(SA.get_stock_adjustments_summary).parameters


def test_skema_menjaga_medan_baru():
    assert {"items_summary_text", "warehouse_name"} <= set(S.StockAdjustmentListItem.model_fields)
    assert "filter_tersedia" in S.StockAdjustmentListResponse.model_fields
    assert "reason_text" in S.StockAdjustmentItemResponse.model_fields


def test_teks_alasan_tanpa_gema_kode():
    assert SS.teks_alasan("correction") is None and SS.teks_alasan("  ") is None and SS.teks_alasan(None) is None
    assert SS.teks_alasan("Salah hitung rak B") == "Salah hitung rak B"


class _Conn:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, q, *a):
        return self.rows


def test_ringkas_baris_satu_dan_beberapa():
    import uuid
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rows = [{"id": a, "gudang_header": "Gudang A", "pertama": "Bodrex", "n_baris": 2, "n_gudang": 1},
            {"id": b, "gudang_header": "Gudang A", "pertama": "Panadol", "n_baris": 1, "n_gudang": 2},
            {"id": c, "gudang_header": None, "pertama": None, "n_baris": 0, "n_gudang": 0}]
    r = asyncio.run(SS.ringkas_baris(_Conn(rows), [a, b, c], "t"))
    assert r[str(a)] == {"items_summary_text": "Bodrex +1 lagi", "warehouse_name": "Gudang A"}
    assert r[str(b)] == {"items_summary_text": "Panadol", "warehouse_name": "Beberapa gudang"}
    assert r[str(c)]["items_summary_text"] is None
    assert asyncio.run(SS.ringkas_baris(_Conn([]), [], "t")) == {}
