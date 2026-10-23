"""GRN SERAGAM (23 Okt 2026, ANDROID/MASTER): medan turunan, pill AKSI, bills[], /documents, /history, pratinjau batal -- ADITIF."""
import inspect
from decimal import Decimal as D
from pathlib import Path

import pytest

from app.routers import goods_receipts as GR
from app.services import grn_daftar as GD


@pytest.mark.parametrize("status,dasar,taut,harap", [
    ("void", "10", "10", "batal"), ("posted", "10", "0", "belum_ditagih"), ("posted", "10", "4", "sebagian_ditagih"),
    ("posted", "10", "10", "sudah_ditagih"), ("posted", "0", "0", None)])
def test_billing_state(status, dasar, taut, harap):
    assert GD.billing_state(status, D(dasar), D(taut)) == harap


def test_medan_aksi_dan_teks_paritas():
    m = GD.medan({"status": "posted", "qty_dasar": D("10"), "qty_tertaut": D("4"), "grni_terbuka": D("6000")})
    assert m == {"billing_state": "sebagian_ditagih", "status_text": "Sebagian ditagih", "grni_open_amount": "6000.00",
                 "aksi": "belum_ditagih", "aksi_semua": ["belum_ditagih"]}
    assert GD.medan({"status": "posted", "qty_dasar": D("10"), "qty_tertaut": D("10"), "grni_terbuka": D("0")})["aksi"] is None
    m0 = GD.medan({"status": "posted", "qty_dasar": D("0"), "qty_tertaut": D("0"), "grni_terbuka": D("0")})
    assert m0["status_text"] == "Diterima" and m0["aksi"] is None  # GRN non-stok saja: tak bisa diukur tagihnya


def test_satu_predikat_aksi_untuk_daftar_dan_summary():
    with pytest.raises(ValueError):
        GD.sql_aksi("lain")
    p = GD.sql_aksi(GD.AKSI_BELUM_DITAGIH)
    assert GD.sql_qty_tertaut() in p and GD.sql_qty_dasar() in p and "status = 'posted'" in p
    assert "GD.sql_aksi(ak)" in inspect.getsource(GR.list_goods_receipts)
    assert "GD.sql_aksi(GD.AKSI_BELUM_DITAGIH)" in inspect.getsource(GR.goods_receipts_summary)
    assert "isinstance(aksi, str)" in inspect.getsource(GR.list_goods_receipts)  # pemanggilan langsung: default = objek Query


def test_grni_terbuka_sama_dengan_rumus_penjaga():
    s = GD.sql_grni_terbuka()
    assert "i.ber_stok" in s and "ROUND((i.quantity - i.quantity_returned) * i.unit_cost, 2)" in s and "l.status = 'active'" in s
    assert "status = 'void' THEN 0" in s


def test_rute_seragam_terdaftar_dan_summary_sebelum_id():
    jalur = [r.path for r in GR.router.routes]
    for p in ("/summary", "/{gr_id}/documents", "/{gr_id}/history"):
        assert p in jalur
    assert jalur.index("/summary") < jalur.index("/{gr_id}")  # "summary" tak tertangkap sebagai UUID


def test_izin_rute_seragam():
    mw = (Path(GR.__file__).parents[1] / "middleware" / "permission_middleware.py").read_text()
    assert r'"^/api/goods-receipts/[^/]+/(documents|history)$", ["GET"], "purchase_order", "R"' in mw


def test_detail_bills_dan_pratinjau_akun_sebelum_sesudah():
    assert "GD.tagihan_terkait(" in inspect.getsource(GR.get_goods_receipt)
    assert "l.status = 'active'" in GD.SQL_TAGIHAN
    src = inspect.getsource(GR.preview_void_goods_receipt)
    assert "account_code" in src and "account_name" in src and '"before"' in src and '"after"' in src


def test_bundel_po_grn_faktur_dan_riwayat():
    k = inspect.getsource(GD.kelompok_grn)
    assert '"key": "po"' in k and '"key": "grn"' in k and '"key": "faktur"' in k and "TD.dok_tagihan" in k
    r = inspect.getsource(GD.riwayat_grn)
    assert "GRN_DITERIMA" in r and "GRN_DIBATALKAN" in r and "GRN_DITAGIH" in r and "_selesaikan" in r
