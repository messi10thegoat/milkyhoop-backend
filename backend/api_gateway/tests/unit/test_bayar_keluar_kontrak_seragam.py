"""Kontrak daftar seragam Pembayaran Keluar (22 Okt 2026): posisi, cari, bundel, riwayat, izin rute, satu sumber.
Pasangan SQL<->Python pada data nyata: scripts/ops/uji-nyata-bayar-keluar-kontrak.sh."""
import inspect
import typing
from datetime import date

import pytest

from app.middleware.permission_middleware import izin_untuk_jalur
from app.routers import bill_payments as BP
from app.schemas.bill_payments import BillPaymentDetail, BillPaymentListItem, BillPaymentListResponse
from app.services import bayar_keluar_riwayat as RW
from app.services import pembayaran_keluar_daftar as PK
from app.services import so_riwayat as SR


@pytest.mark.parametrize("status,bruto,dialokasi,harap", [
    ("draft", 100, 0, "Draf"), ("voided", 100, 100, "Batal"), ("posted", 100, 0, "Belum dialokasikan"),
    ("posted", 100, 40, "Sebagian dialokasikan"), ("posted", 100, 100, "Teralokasi penuh"),
])
def test_posisi_bayar(status, bruto, dialokasi, harap):
    assert PK.posisi_bayar(status, PK.alokasi_fakta(status, bruto, dialokasi)) == harap


def test_cari_satu_param_per_kata_dan_cast_uuid():
    pred, par = PK.syarat_cari("abc  def", "q", 4)
    assert par == ["%abc%", "%def%"] and "$4" in pred and "$5" in pred and "$6" not in pred
    assert "q.id::uuid" in pred and "b.invoice_number ILIKE" in pred
    assert PK.syarat_cari("   ", "q", 2) == (None, []) and PK.syarat_cari(None, "q", 2) == (None, [])


def test_dok_bayar_status():
    p = {"id": "x", "payment_number": "BP-1", "payment_date": date(2026, 10, 1), "total_amount": 1000}
    assert [PK.dok_bayar({**p, "status": s})["status"] for s in ("draft", "voided", "posted")] == ["draf", "batal", "dibayar"]
    assert PK.dok_bayar({**p, "status": "voided"})["batal"] is True
    assert PK.dok_bayar({**p, "status": "posted"})["kind"] == "payment_out"


def test_skema_dan_satu_sumber():
    for m in (BillPaymentListItem, BillPaymentDetail):
        assert {"position_text", "documents", "doc_count"} <= set(m.model_fields)
    assert {"sum_total", "sum_sisa"} <= set(BillPaymentListResponse.model_fields)
    ls = inspect.getsource(BP.list_bill_payments)
    assert "_pk.syarat_cari(" in ls and "_pk.kelompok_untuk(" in ls and "_pk.posisi_bayar(" in ls and "q.belum >" in ls
    sm = inspect.getsource(BP.get_bill_payments_summary)
    assert "_pk.syarat_cari(" in sm and "_pk.sql_belum(" in sm and "belum_dialokasikan_count" in sm
    dt = inspect.getsource(BP.get_bill_payment)
    assert "_pk.kelompok_untuk(" in dt and "_pk.posisi_bayar(" in dt
    lit = typing.get_args(inspect.signature(BP.list_bill_payments).parameters["aksi"].annotation)
    assert set(typing.get_args(lit[0])) == set(PK.AKSI)


def test_bundel_dok_tagihan_dari_satu_pembentuk():
    src = inspect.getsource(PK.kelompok_untuk)
    assert "TD.dok_tagihan(" in src and "TD.sql_keadaan_dok()" in src and "bp.status = 'posted'" in src


@pytest.mark.parametrize("jalur", ["/api/bill-payments/x/documents", "/api/bill-payments/x/history"])
def test_izin_rute_baca(jalur):
    r = izin_untuk_jalur(jalur, "GET")
    assert (r[0], r[1]) == ("send_payment", "R"), r


def test_riwayat_modul_dan_audit():
    assert RW.MODUL_BAYAR == {"payment_out": "send_payment", "bill": "purchase_invoice"}
    assert SR.ENTITAS_AUDIT["bill_payments"] == SR.ENTITAS_AUDIT["bill_payments_v2"] == "payment_out"
