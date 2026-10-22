"""Penentu hitung + blok Faktur Pembelian (22 Okt 2026): create v2 / update v2 / POST /bills/calculate / POST /bills/{id}/edit/preview memakai
SATU kalkulator (tagihan_rencana.hitung) dan SATU penentu blok. Paritas angka pada data nyata: scripts/ops/uji-nyata-tagihan-hitung.sh."""
import inspect

import pytest

from app.middleware.permission_middleware import izin_untuk_jalur
from app.routers import bills as BR
from app.schemas.bills import CalculateBillRequestV2, CreateBillRequestV2
from app.services import bills_service as BS
from app.services import tagihan_rencana as TR


def test_tulis_dan_pratinjau_satu_kalkulator():
    for f in (BS.BillsService.create_bill_v2, BS.BillsService.update_bill_v2):
        src = inspect.getsource(f)
        assert "TR.hitung(" in src and "BillCalculator.calculate(" not in src and "_bill_line_tax(" not in src, f.__name__
    assert "TR.blok_buat(" in inspect.getsource(BS.BillsService.create_bill_v2)
    assert "TR.blok_ubah(" in inspect.getsource(BS.BillsService.update_bill_v2)
    calc = inspect.getsource(BR.calculate_bill_totals)
    assert "TR.blok_buat(" in calc and "TR.hitung(" in calc and "readonly=True" in calc and "BillCalculator" not in calc
    pv = inspect.getsource(BR.preview_edit_bill)
    assert "TR.blok_ubah(" in pv and "TR.hitung(" in pv and "readonly=True" in pv and "pg_advisory" not in pv


def test_blok_buat_sebelum_nomor_dan_vendor():
    src = inspect.getsource(BS.BillsService.create_bill_v2)
    assert src.index("TR.blok_buat(") < src.index("generate_purchase_bill_number") < src.index("INSERT INTO vendors")


def test_aturan_pajak_kepala_nol_dan_ppn_baris_ke_grand_total():
    src = inspect.getsource(TR.hitung)
    assert "tarif_kepala = 0 if pajak_baris" in src
    assert '"grand_total": float(header["grand_total"]) + float(total_ppn)' in src


@pytest.mark.parametrize("items,kode", [
    ([{"qty": None, "price": 1}], "ITEM_QTY_WAJIB"), ([{"qty": 1, "price": None}], "ITEM_HARGA_WAJIB"),
    ([{"qty": "x", "price": 1}], "ITEM_BUKAN_ANGKA"), ([{"qty": 1, "price": 2}], None),
])
def test_blok_baris(items, kode):
    b = TR.blok_baris(items)
    assert (b[0]["code"] if b else None) == kode


def test_badan_calculate_longgar_badan_simpan_ketat():
    CalculateBillRequestV2(items=[])  # due_date & items boleh kosong -> blok, bukan 422
    with pytest.raises(Exception):
        CreateBillRequestV2(items=[])


def test_izin_rute():
    assert izin_untuk_jalur("/api/bills/calculate", "POST")[:2] == ("purchase_invoice", "R")
    assert izin_untuk_jalur("/api/bills/x/edit/preview", "POST")[:2] == ("purchase_invoice", "U")
