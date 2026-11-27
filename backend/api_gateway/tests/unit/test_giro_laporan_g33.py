"""G33: laporan giro (jatuh tempo/umur/per pihak) -- hitung & teks; SQL langsung ke `cheques`, bukan fungsi DB yang dulu diparkir."""
from datetime import date
from decimal import Decimal

from app.services import giro_laporan as GL

H = date(2026, 11, 27)


def baris(n, tgl, amt, tipe="received", st="pending"):
    return {"id": f"id{n}", "cheque_number": f"G{n}", "cheque_date": tgl, "bank_name": "BCA", "cheque_type": tipe, "amount": Decimal(amt), "party_name": "PT A",
            "status": st, "reference_number": None}


def test_hari_text():
    assert GL.hari_text(0) == "Jatuh tempo hari ini" and GL.hari_text(5) == "5 hari lagi" and GL.hari_text(-3) == "Terlambat 3 hari"


def test_hiasi_dan_daftar_total():
    rows = [baris(1, date(2026, 11, 27), "1000000"), baris(2, date(2026, 11, 20), "500000", st="bounced")]
    o = GL.daftar(rows, H, 0)
    assert o["count"] == 2 and o["total_amount"] == 1500000.0 and o["total_text"]
    a, b = o["data"]
    assert a["jatuh_tempo_text"] == "Jatuh tempo hari ini" and a["jenis_text"] == "Giro masuk" and a["status_text"] == "Menunggu"
    assert b["jatuh_tempo_text"] == "Terlambat 7 hari" and b["status_text"] == "Ditolak bank" and b["days_until_due"] == -7


def test_bucket_batas():
    assert [GL.bucket(d) for d in (5, 0, -1, -30, -31, -60, -61, -90, -91)] == [0, 0, 1, 1, 2, 2, 3, 3, 4]


def test_umur_lima_bucket_selalu_tampil_dengan_jumlah():
    rows = [baris(1, date(2026, 12, 5), "100"), baris(2, date(2026, 11, 20), "200"), baris(3, date(2026, 11, 1), "300"), baris(4, date(2026, 8, 1), "400")]
    u = GL.susun_umur(rows, H, 0)
    assert [x["aging_bucket"] for x in u] == list(GL.BUCKET) and [x["count"] for x in u] == [1, 2, 0, 0, 1]
    assert u[1]["total_amount"] == 500.0


def test_sql_tak_memakai_fungsi_db_parkir():
    semua = " ".join((GL.SQL_HARI_INI, GL.SQL_AKAN, GL.SQL_PIHAK, GL.SQL_UMUR))
    for f in ("get_cheques_due_for_deposit", "get_upcoming_cheques", "get_customer_cheques", "get_vendor_cheques", "get_cheque_aging"):
        assert f not in semua
    assert "tenant_id = $1" in GL.SQL_HARI_INI and "tenant_id = $1" in GL.SQL_AKAN and "tenant_id = $1" in GL.SQL_PIHAK and "tenant_id = $1" in GL.SQL_UMUR
