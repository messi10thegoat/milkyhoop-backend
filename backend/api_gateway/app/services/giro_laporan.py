"""G33: laporan giro (jatuh tempo hari ini, akan jatuh tempo, per pelanggan/vendor, umur). BACA saja dari tabel `cheques` (dokumen giro; belum ada jurnal sebelum disetor).
Dulu rute-rute ini diparkir 409 karena fungsi DB-nya rusak; kini SQL langsung, tanpa fungsi DB. Hari ini = tanggal bisnis tenant."""
from datetime import date
from decimal import Decimal
from typing import Optional

from . import teks_galat as tg

STATUS_TEXT = {"pending": "Menunggu", "deposited": "Disetor", "cleared": "Cair", "bounced": "Ditolak bank", "cancelled": "Dibatalkan", "replaced": "Diganti"}
JENIS_TEXT = {"received": "Giro masuk", "issued": "Giro keluar"}
KOLOM = ("id, cheque_number, cheque_date, bank_name, cheque_type, amount, party_name, status, reference_number, customer_id, vendor_id")

SQL_HARI_INI = f"SELECT {KOLOM} FROM cheques WHERE tenant_id = $1 AND status = 'pending' AND cheque_date = $2 AND ($3::text IS NULL OR cheque_type = $3) ORDER BY cheque_date, cheque_number"
SQL_AKAN = (f"SELECT {KOLOM} FROM cheques WHERE tenant_id = $1 AND status = 'pending' AND cheque_date > $2 AND cheque_date <= $2 + $4::int "
            "AND ($3::text IS NULL OR cheque_type = $3) ORDER BY cheque_date, cheque_number")
SQL_PIHAK = (f"SELECT {KOLOM} FROM cheques WHERE tenant_id = $1 AND {{kolom}} = $2 AND ($3::text IS NULL OR status = $3) ORDER BY cheque_date DESC, cheque_number")
SQL_UMUR = "SELECT cheque_date, amount FROM cheques WHERE tenant_id = $1 AND status = 'pending' AND cheque_type = $2"

BUCKET = ("Belum jatuh tempo", "Lewat 1-30 hari", "Lewat 31-60 hari", "Lewat 61-90 hari", "Lewat lebih dari 90 hari")


def hari_text(selisih: int) -> str:
    if selisih == 0:
        return "Jatuh tempo hari ini"
    return f"{selisih} hari lagi" if selisih > 0 else f"Terlambat {-selisih} hari"


def hiasi(r: dict, hari: date, presisi: int) -> dict:
    sisa = (r["cheque_date"] - hari).days
    return {"id": str(r["id"]), "cheque_number": r["cheque_number"], "cheque_date": r["cheque_date"], "bank_name": r["bank_name"], "cheque_type": r["cheque_type"],
            "jenis_text": JENIS_TEXT.get(r["cheque_type"], r["cheque_type"]), "amount": r["amount"], "amount_text": tg.angka_laporan(r["amount"], presisi),
            "party_name": r["party_name"], "status": r["status"], "status_text": STATUS_TEXT.get(r["status"], r["status"]), "reference_number": r["reference_number"],
            "days_until_due": sisa, "jatuh_tempo_text": hari_text(sisa)}


def daftar(rows: list, hari: date, presisi: int) -> dict:
    items = [hiasi(dict(r), hari, presisi) for r in rows]
    total = sum((Decimal(str(r["amount"])) for r in rows), Decimal(0))
    return {"data": items, "count": len(items), "total_amount": float(total), "total_text": tg.angka_laporan(total, presisi)}


def bucket(selisih_hari: int) -> int:
    """selisih = cheque_date - hari ini. >=0 belum jatuh tempo; negatif = lewat."""
    lewat = -selisih_hari
    return 0 if lewat <= 0 else 1 if lewat <= 30 else 2 if lewat <= 60 else 3 if lewat <= 90 else 4


def susun_umur(rows: list, hari: date, presisi: int) -> list:
    jml, tot = [0] * len(BUCKET), [Decimal(0)] * len(BUCKET)
    for r in rows:
        i = bucket((r["cheque_date"] - hari).days)
        jml[i] += 1
        tot[i] += Decimal(str(r["amount"]))
    return [{"aging_bucket": BUCKET[i], "count": jml[i], "total_amount": float(tot[i]), "total_text": tg.angka_laporan(tot[i], presisi)} for i in range(len(BUCKET))]
