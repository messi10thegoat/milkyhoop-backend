"""G1: ekspor Buku Kas/Statement rekening (berkas == angka statement; saldo awal/akhir + total ikut)."""
import csv
import io

from app.services import ekspor_kasbank as EK

D = {"account_name": "BCA Operasional", "start_date": "2026-11-01", "end_date": "2026-11-27", "opening_balance": 1000.0, "closing_balance": 1700.0,
     "total_deposits": 1000.0, "total_withdrawals": 300.0,
     "transactions": [{"date": "2026-11-05", "type": "RECEIVE_PAYMENT", "description": "Pelunasan INV-1", "amount": 1000.0, "running_balance": 2000.0},
                      {"date": "2026-11-09", "type": "EXPENSE", "description": "Listrik", "amount": -300.0, "running_balance": 1700.0}]}


def test_tabel_statement_masuk_keluar_terpisah_dan_ringkasan():
    k, b, bawah = EK.tabel_statement(D, 0)
    assert k == ["Tanggal", "Jenis", "Keterangan", "Masuk", "Keluar", "Saldo"]
    assert b[0][3] is not None and b[0][4] is None and b[1][3] is None and b[1][4] is not None
    teks = str(bawah)
    assert "BCA Operasional" in teks and "Saldo awal" in teks and "Saldo akhir" in teks


def test_csv_dan_xlsx():
    k, b, bawah = EK.tabel_statement(D, 0)
    rows = list(csv.reader(io.StringIO(EK.bangun_csv(k, b, bawah).decode("utf-8-sig"))))
    assert rows[0] == k and rows[1][2] == "Pelunasan INV-1"
    from openpyxl import load_workbook
    ws = load_workbook(io.BytesIO(EK.bangun_xlsx("buku-kas", k, b, bawah))).active
    assert [c.value for c in ws[1]] == k
