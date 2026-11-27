"""G1: ekspor Buku Kas / Statement rekening (xlsx|csv). BACA saja: angka = hasil GET /bank-accounts/{id}/statement yang SAMA (jurnal efektif), hanya diformat teks.
Pembangun berkas dipakai bersama dgn ekspor laporan persediaan."""
from . import teks_galat as tg
from .ekspor_laporan_persediaan import bangun_csv, bangun_xlsx  # noqa: F401  (dipakai router)

MAKS_BARIS = 20000


def tabel_statement(d: dict, presisi: int) -> tuple:
    u = lambda x: tg.angka_laporan(x, presisi)
    kolom = ["Tanggal", "Jenis", "Keterangan", "Masuk", "Keluar", "Saldo"]
    baris = []
    for t in d["transactions"]:
        a = t["amount"]
        baris.append([t["date"], t["type"], t["description"], u(a) if a > 0 else None, u(abs(a)) if a < 0 else None, u(t["running_balance"])])
    bawah = [[], ["Rekening", d["account_name"]], ["Periode", f"{d['start_date']} s.d. {d['end_date']}"], ["Saldo awal", u(d["opening_balance"])],
             ["Total masuk", u(d["total_deposits"])], ["Total keluar", u(d["total_withdrawals"])], ["Saldo akhir", u(d["closing_balance"])]]
    return kolom, baris, bawah
