"""LISTPAGE-DATA (16 Okt 2026, MASTER): data daftar SO/Faktur untuk paket listpage-polish -- FE hanya memformat.

Pasangan satu-sumber (murni): kirim_hari/kirim_terbuka == teks kirim(); doc_count == len(documents);
is_overdue == jatuh_tempo_terbuka AND jatuh_tempo_hari < 0 (predikat SQL dibangun dari _syarat_terbuka_ar);
?search= daftar dan /summary memakai _syarat_cari yang sama. Harness nyata: scripts/ops/uji-nyata-listpage-data.py.
"""
import inspect
from datetime import date, timedelta

import pytest

from app.routers import sales_invoices as SI
from app.routers import sales_orders as SO
from app.services import faktur_daftar as FD
from app.services import so_posisi as P

HARI = date(2026, 10, 16)
STATUS = ("draft", "confirmed", "partial_shipped", "shipped", "invoiced", "completed", "cancelled")


@pytest.mark.parametrize("status", STATUS)
@pytest.mark.parametrize("perlu", (True, False))
@pytest.mark.parametrize("semua", (True, False))
@pytest.mark.parametrize("geser", (None, -30, -2, -1, 0, 1, 2, 30))
def test_kirim_hari_berpasangan_dengan_teks_kirim(status, perlu, semua, geser):
    tgl = None if geser is None else HARI + timedelta(days=geser)
    teks, _ = P.kirim(status, tgl, perlu, semua, HARI)
    n, buka = P.kirim_hari(status, tgl, perlu, semua, HARI)
    if tgl is not None:
        assert buka == (teks not in ("—", "Terkirim")), (teks, buka)
    if not buka or tgl is None:
        assert n is None
        return
    assert n == geser
    assert (n < 0) == teks.endswith(", telat")
    assert (n == 0) == (teks == "Hari ini")
    assert (n == 1) == (teks == "Besok")


def test_kirim_hari_kalender_bukan_jam():
    # tanggal usaha (zona tenant) sudah berupa date -> selisih kalender murni
    assert P.kirim_hari("confirmed", date(2026, 10, 16), True, False, date(2026, 10, 15)) == (1, True)
    assert P.kirim_hari("confirmed", date(2026, 10, 15), True, False, date(2026, 10, 16)) == (-1, True)


def test_dokumen_so_urut_dan_jumlah():
    d = P.dokumen_so(True, 2, 1, 3, 1)
    assert [x["type"] for x in d] == ["quotation", "proforma", "proforma", "receipt",
                                       "delivery", "delivery", "delivery", "invoice"]
    assert P.dokumen_so(False, 0, 0, 0, 0) == []


def test_fakta_daftar_doc_count_dari_documents():
    src = inspect.getsource(P.fakta_daftar)
    assert '"doc_count": len(dok)' in src and '"documents": dok' in src
    assert "kirim_hari(" in src


def test_dokumen_faktur_urut():
    assert [x["type"] for x in FD.dokumen_faktur(2, 1)] == ["invoice", "receipt", "receipt", "credit_note"]
    assert FD.dokumen_faktur(0, 0) == [{"type": "invoice"}]


@pytest.mark.parametrize("terbuka,due,hasil", [
    (True, HARI - timedelta(days=3), -3), (True, HARI, 0), (True, HARI + timedelta(days=5), 5),
    (False, HARI - timedelta(days=3), None), (True, None, None)])
def test_jatuh_tempo_hari(terbuka, due, hasil):
    assert FD.jatuh_tempo_hari(terbuka, due, HARI) == hasil


def test_jatuh_tempo_satu_predikat_dengan_is_overdue():
    terbuka = SI._syarat_terbuka_ar()
    assert terbuka in SI._syarat_jatuh_tempo("$9")
    src = inspect.getsource(SI.list_invoices)
    assert "_syarat_terbuka_ar()} AS jt_terbuka" in src


@pytest.mark.parametrize("modul", (SO, SI))
def test_syarat_cari_parameter_per_kata(modul):
    c, p = modul._syarat_cari("kaos  biru", 4)
    assert p == ["%kaos%", "%biru%"] and "$4" in c and "$5" in c and "$6" not in c
    c1, p1 = modul._syarat_cari("kaos", 2)
    assert p1 == ["%kaos%"] and " AND " not in c1.split("ILIKE")[0]
    assert modul._syarat_cari("   ", 2) == ("TRUE", [])


@pytest.mark.parametrize("modul,daftar,ringkas", [
    (SO, "list_sales_orders", "get_sales_order_summary"), (SI, "list_invoices", "get_invoice_summary")])
def test_daftar_dan_summary_memakai_syarat_cari_yang_sama(modul, daftar, ringkas):
    assert "_syarat_cari(search, param_idx)" in inspect.getsource(getattr(modul, daftar))
    assert "_syarat_cari(search, 2)" in inspect.getsource(getattr(modul, ringkas))


@pytest.mark.parametrize("modul,daftar", [(SO, "list_sales_orders"), (SI, "list_invoices")])
def test_with_totals_opt_in(modul, daftar):
    sig = inspect.signature(getattr(modul, daftar))
    assert sig.parameters["with_totals"].default.default is False
