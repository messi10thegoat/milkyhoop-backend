"""Gelombang 4 L2 (10 Nov 2026): laporan umur piutang/hutang membawa teks tampil SERVER (services/laporan_umur, SATU peta label ember,
tanggal 'per' = tanggal bisnis tenant). Uang = medan lama yang sama. Bukti salinan: scripts/ops/uji-salinan-laporan-umur.sh."""
import inspect
from datetime import date
from decimal import Decimal

from app.routers import reports as RP
from app.services import laporan_umur as LU
from app.services import teks_galat as tg


def _hasil():
    inv = [{"invoice_number": "INV-1", "due_date": "2026-10-12", "days_overdue": 29, "balance": Decimal("300")},
           {"invoice_number": None, "due_date": "2026-11-20", "days_overdue": 0, "balance": Decimal("-100")}]
    p = {"customer_id": "c", "customer_name": "A", "invoices": inv,
         "totals": {"current": Decimal("-100"), "1-30": Decimal("300"), "31-60": 0, "61-90": 0, "90+": 0}, "total": Decimal("200")}
    summary = {"current": Decimal("-100"), "1-30": Decimal("300"), "31-60": 0, "61-90": 0, "90+": 0}
    return {"success": True, "customers": [p], "summary": summary, "total_outstanding": Decimal("200")}


def test_tampilan_umur():
    h = LU.tambah_tampilan(_hasil(), "customers", "invoices", "invoice_number", "pelanggan", date(2026, 11, 10))
    assert (h["per_tanggal"], h["per_text"]) == ("2026-11-10", "Per 10 November 2026")
    assert [(e["kunci"], e["label"], e["total"], e["persen"]) for e in h["ember"]] == [
        ("current", "Belum jatuh tempo", "-100.00", "-50.0"), ("1-30", "1–30 hari", "300.00", "150.0"),
        ("31-60", "31–60 hari", "0.00", "0.0"), ("61-90", "61–90 hari", "0.00", "0.0"), ("90+", "> 90 hari", "0.00", "0.0")]
    assert (h["belum_jatuh_tempo_total"], h["jatuh_tempo_total"]) == ("-100.00", "300.00")
    assert (h["jumlah_pihak"], h["jumlah_pihak_text"]) == (1, "1 pelanggan")
    p = h["customers"][0]
    assert p["jatuh_tempo_total"] == "300.00"
    a, b = p["invoices"]
    assert (a["nomor_text"], a["jatuh_tempo_text"], a["telat_text"]) == ("INV-1", "jatuh tempo 12 Okt 2026", "telat 29 hari")
    assert (b["nomor_text"], b["telat_text"]) == ("Kredit tanpa dokumen", None)
    assert h["summary"]["1-30"] == Decimal("300") and h["total_outstanding"] == Decimal("200")  # medan lama utuh


def test_umur_kosong_tanpa_bagi_nol():
    h = LU.tambah_tampilan({"success": True, "vendors": [], "summary": dict.fromkeys(LU.EMBER, 0), "total_outstanding": 0},
                           "vendors", "bills", "bill_number", "vendor", date(2026, 11, 10))
    assert {e["persen"] for e in h["ember"]} == {"0.0"} and h["jumlah_pihak_text"] == "0 vendor"


def test_tanggal_pendek():
    assert tg.tanggal_pendek("2026-08-03") == "3 Agu 2026" and tg.tanggal_pendek(date(2026, 12, 31)) == "31 Des 2026"


def test_kedua_rute_memakai_pembantu_bersama_dengan_tanggal_bisnis():
    for f, args in ((RP.get_aging_receivable, '"customers", "invoices", "invoice_number", "pelanggan", hari_ini'),
                    (RP.get_aging_payable, '"vendors", "bills", "bill_number", "vendor", hari_ini')):
        s = inspect.getsource(f)
        assert "LU.tambah_tampilan(" in s and args in s and "hari_ini = await tanggal_dokumen(conn" in s
