"""Kontrak daftar seragam Faktur Pembelian (22 Okt 2026): aksi partisi, jatuh tempo, posisi, cari, bundel, riwayat, izin rute.
Pasangan SQL<->Python pada data nyata: scripts/ops/uji-nyata-tagihan-kontrak.sh."""
import inspect
import typing
from datetime import date

import pytest

from app.middleware.permission_middleware import izin_untuk_jalur
from app.routers import bills as BR
from app.schemas.bills import BillListItem, BillListResponse
from app.services import bills_service as BS
from app.services import so_riwayat as SR
from app.services import tagihan_daftar as TD
from app.services import tagihan_riwayat as TR
from app.services.dashboard_v2 import akhir_minggu

H = date(2026, 10, 22)  # Kamis; akhir minggu = Minggu 25 Okt


def test_akhir_minggu_acuan():
    assert akhir_minggu(H) == date(2026, 10, 25)


@pytest.mark.parametrize("keadaan,due,issue,harap", [
    ("draft", date(2026, 1, 1), date(2026, 1, 1), "perlu_diposting"),
    ("overdue", date(2026, 10, 21), date(2026, 10, 1), "lewat_jatuh_tempo"),
    ("partial", date(2026, 10, 21), date(2026, 10, 1), "lewat_jatuh_tempo"),  # keadaan daftar berdasar status, aksi berdasar tanggal
    ("unpaid", date(2026, 10, 22), date(2026, 10, 1), "jatuh_tempo_minggu_ini"),
    ("unpaid", date(2026, 10, 25), date(2026, 10, 1), "jatuh_tempo_minggu_ini"),
    ("unpaid", date(2026, 10, 26), date(2026, 10, 1), None),
    ("unpaid", None, date(2026, 10, 20), "lewat_jatuh_tempo"),  # jatuh tempo efektif = issue_date
    ("paid", date(2026, 10, 1), date(2026, 9, 1), None),
    ("void", date(2026, 10, 1), date(2026, 9, 1), None),
])
def test_aksi_tagihan(keadaan, due, issue, harap):
    assert TD.aksi_tagihan(keadaan, due, issue, H) == harap


def test_sql_aksi_partisi_dan_param():
    for k in TD.AKSI_TAGIHAN:
        pred, par = TD.sql_aksi_berparam(k, 5, H)
        rujuk = {n for n in (5, 6) if f"${n}" in pred}
        assert len(par) == len(rujuk), (k, pred, par)
    assert TD.sql_aksi_berparam("jatuh_tempo_minggu_ini", 3, H)[1] == [H, date(2026, 10, 25)]
    assert "apr.outstanding > 0" in TD.sql_terbuka() and "COALESCE(b.due_date, b.issue_date)" in TD.sql_aksi("lewat_jatuh_tempo", "$2", "$3")


@pytest.mark.parametrize("keadaan,dibayar,harap", [
    ("draft", 0, "Draf"), ("void", 0, "Batal"), ("paid", 100, "Lunas"), ("partial", 10, "Sebagian"),
    ("overdue", 10, "Sebagian"), ("overdue", 0, "Belum lunas"), ("unpaid", 0, "Belum lunas"),
])
def test_posisi_tagihan(keadaan, dibayar, harap):
    assert TD.posisi_tagihan(keadaan, dibayar) == harap
    assert harap in TD.POSISI_TAGIHAN


@pytest.mark.parametrize("keadaan,due,kd,teks,hari", [
    ("overdue", date(2026, 10, 12), "telat", "12 Okt, lewat tempo", -10),
    ("unpaid", date(2026, 10, 22), "hari_ini", "Hari ini", 0),
    ("partial", date(2026, 10, 23), "akan", "Besok", 1),
    ("unpaid", date(2026, 11, 24), "akan", "24 Nov", 33),
    ("paid", date(2026, 10, 1), "selesai", "1 Okt", None),
    ("draft", date(2026, 10, 1), "tanpa", "—", None),
    ("void", date(2026, 10, 1), "tanpa", "—", None),
])
def test_jatuh_tempo_fakta(keadaan, due, kd, teks, hari):
    f = TD.jatuh_tempo_fakta(keadaan, due, H)
    assert (f["jatuh_tempo_keadaan"], f["jatuh_tempo_teks"], f["jatuh_tempo_hari"]) == (kd, teks, hari)
    assert f["jatuh_tempo_terbuka"] == (kd in ("telat", "hari_ini", "akan"))
    assert f["jatuh_tempo_minggu_ini"] == (kd in ("hari_ini", "akan") and due <= date(2026, 10, 25))


def test_fakta_baris_kunci_dan_skema():
    f = TD.fakta_baris("unpaid", 0, "1500", date(2026, 10, 24), date(2026, 10, 1), H)
    assert f["aksi"] == "jatuh_tempo_minggu_ini" and f["aksi_semua"] == ["jatuh_tempo_minggu_ini"]
    assert f["outstanding_amount"] == "1500.00" and f["position_text"] == "Belum lunas"
    assert TD.fakta_baris("draft", 0, None, None, None, H)["outstanding_amount"] is None
    medan = set(BillListItem.model_fields)
    assert set(f) | {"documents", "doc_count"} <= medan
    assert {"sum_total", "sum_sisa"} <= set(BillListResponse.model_fields)


def test_cari_satu_param_per_kata():
    pred, par = TD.syarat_cari("abc  def", 4)
    assert par == ["%abc%", "%def%"] and "$4" in pred and "$5" in pred and "$6" not in pred
    assert TD.syarat_cari("   ", 2) == ("TRUE", [])


def test_daftar_dan_summary_memakai_satu_sumber():
    src = inspect.getsource(BS.BillsService.list_bills)
    assert "TD.syarat_cari(" in src and "TD.sql_aksi_berparam(" in src and "TD.fakta_baris(" in src and "TD.kelompok_tagihan(" in src
    sm = inspect.getsource(BS.BillsService.get_summary)
    assert "TD.syarat_cari(" in sm and "TD.sql_aksi(" in sm
    for f in (BS.BillsService.get_bill, BS.BillsService.get_bill_v2):
        assert "TD.fakta_baris(" in inspect.getsource(f) and "TD.kelompok_tagihan(" in inspect.getsource(f)
    lit = typing.get_args(inspect.signature(BR.list_bills).parameters["aksi"].annotation)
    assert set(typing.get_args(lit[0])) == set(TD.AKSI_TAGIHAN)


def test_tanpa_tanggal_server_utc():
    src = inspect.getsource(TD)
    assert "date.today(" not in src and "CURRENT_DATE" not in src


def test_dok_tagihan_status():
    b = {"id": "x", "invoice_number": "BILL-1", "issue_date": date(2026, 10, 1), "amount": 1000}
    assert [TD.dok_tagihan(b, k)["status"] for k in ("draft", "void", "paid", "terbuka")] == ["draf", "batal", "lunas", "diposting"]
    assert TD.dok_tagihan(b, "void")["batal"] is True and TD.dok_tagihan(b, "paid")["paid"] is True
    kel = [{"docs": [{"kind": "purchase_order"}]}, {"docs": [{"kind": "bill"}, ]}, {"docs": [{"kind": "payment_out"}, {"kind": "payment_out"}]}]
    assert TD.documents_baris(kel) == [{"type": "purchase_order"}, {"type": "bill"}, {"type": "payment_out"}, {"type": "payment_out"}]


@pytest.mark.parametrize("jalur", ["/api/bills/x/documents", "/api/bills/x/history"])
def test_izin_rute_baca(jalur):
    r = izin_untuk_jalur(jalur, "GET")
    assert (r[0], r[1]) == ("purchase_invoice", "R"), r


def test_riwayat_modul_dan_audit():
    assert TR.MODUL_TAGIHAN == {"bill": "purchase_invoice", "payment_out": "send_payment", "vendor_deposit": "vendor_deposit",
                                "debit_note": "debit_note"}
    assert SR.ENTITAS_AUDIT["bills"] == "bill"
