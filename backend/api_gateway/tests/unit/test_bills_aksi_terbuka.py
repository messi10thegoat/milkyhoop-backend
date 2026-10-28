"""?aksi=terbuka di /api/bills (28 Okt 2026, kartu payung "Belum lunas" FPB): himpunan SAMA dengan /summary tagihan_terbuka_count/_value
(TD.sql_terbuka lewat SATU pintu TD.sql_aksi). Saring, bukan pill: partisi AKSI_TAGIHAN tak berubah."""
import inspect
import typing
from datetime import date

from app.routers import bills as BR
from app.services import bills_service as BS
from app.services import tagihan_daftar as TD


def test_kunci_terbuka_diterima_rute():
    lit = typing.get_args(inspect.signature(BR.list_bills).parameters["aksi"].annotation)
    assert "terbuka" in typing.get_args(lit[0])
    assert TD.SARING_PAYUNG == ("terbuka",)


def test_predikat_sama_dengan_summary():
    assert TD.sql_aksi("terbuka", "$2", "$3") == TD.sql_terbuka()
    c, p = TD.sql_aksi_berparam("terbuka", 7, date(2026, 10, 28), 7)
    assert c == TD.sql_terbuka() and p == []  # tanpa parameter (asyncpg menolak $N tak terpakai)


def test_daftar_dan_summary_lewat_pintu_sama():
    daftar = inspect.getsource(BS.BillsService.list_bills)
    assert "TD.SARING_PAYUNG" in daftar
    sm = inspect.getsource(BS.BillsService.get_summary)
    assert "TD.sql_aksi('terbuka'" in sm and sm.count("TD.sql_terbuka()") == 0
    assert '"tagihan_terbuka_count": int(aksi_row["terbuka_count"])' in sm


def test_bukan_pill_partisi_tetap():
    assert TD.AKSI_TAGIHAN == ("lewat_jatuh_tempo", "segera_tempo", "perlu_diposting")
    assert "terbuka" not in TD.AKSI_TAGIHAN and "terbuka" not in TD.SARING_UMUR
