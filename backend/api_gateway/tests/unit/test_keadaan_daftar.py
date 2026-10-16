"""KEADAAN-LIST (16 Okt 2026): kirim_keadaan (SO) dan jatuh_tempo_keadaan (SI) = enum dari fakta YANG SAMA dengan
ship_text/kirim_hari dan jatuh_tempo_terbuka/_hari. Kisi menyeluruh: tiap kombinasi dicocokkan dengan keluaran lama.
Pasangan di data nyata grapgrap+kaos: scripts/ops/uji-nyata-keadaan-daftar.py."""
import itertools
from datetime import date, timedelta

from app.services import faktur_daftar as FD
from app.services import so_posisi as SP

HARI = date(2026, 10, 16)
TANGGAL = [None, HARI - timedelta(days=9), HARI - timedelta(days=1), HARI, HARI + timedelta(days=1), HARI + timedelta(days=7)]
STATUS_SO = ["draft", "confirmed", "in_progress", "completed", "cancelled"]


def test_so_kisi_cocok_ship_text_dan_kirim_hari():
    n = 0
    for st, tgl, perlu, semua in itertools.product(STATUS_SO, TANGGAL, (True, False), (True, False)):
        kd = SP.kirim_keadaan(st, tgl, perlu, semua, HARI)
        teks, gaya = SP.kirim(st, tgl, perlu, semua, HARI)
        hari, terbuka = SP.kirim_hari(st, tgl, perlu, semua, HARI)
        assert kd in ("telat", "hari_ini", "akan", "selesai", "tanpa")
        if terbuka and hari is not None:  # kirim() menampilkan tanggal terbuka
            assert kd == ("telat" if hari < 0 else "hari_ini" if hari == 0 else "akan"), (st, tgl, perlu, semua)
            assert (kd == "telat") == teks.endswith(", telat") and (kd == "hari_ini") == (teks == "Hari ini")
        elif teks == "—":
            assert kd in ("tanpa", "selesai") and (kd == "tanpa" or tgl is not None and st in SP.SELESAI_KIRIM and perlu)
        else:  # "Terkirim"
            assert teks == "Terkirim" and kd == ("selesai" if tgl is not None else "tanpa")
        if st == "cancelled":
            assert kd == "tanpa"
        if not perlu:
            assert kd == "tanpa"
        n += 1
    assert n == len(STATUS_SO) * len(TANGGAL) * 4


def test_so_selesai_hanya_bertanggal_dan_batal_tanpa():
    assert SP.kirim_keadaan("completed", HARI, True, False, HARI) == "selesai"
    assert SP.kirim_keadaan("completed", None, True, False, HARI) == "tanpa"
    assert SP.kirim_keadaan("confirmed", HARI - timedelta(days=3), True, True, HARI) == "selesai"
    assert SP.kirim_keadaan("confirmed", HARI - timedelta(days=3), True, False, HARI) == "telat"


def test_si_kisi_cocok_terbuka_hari_dan_is_overdue():
    for st, terbuka, due in itertools.product(["draft", "posted", "partial", "paid", "void"], (True, False), TANGGAL):
        kd = FD.jatuh_tempo_keadaan(st, terbuka, due, HARI)
        hari = FD.jatuh_tempo_hari(terbuka, due, HARI)
        assert kd in ("telat", "hari_ini", "akan", "selesai", "tanpa")
        is_overdue = terbuka and hari is not None and hari < 0
        assert (kd == "telat") == (is_overdue and st not in ("draft", "void")), (st, terbuka, due)
        if st in ("draft", "void") or due is None:
            assert kd == "tanpa"
        elif not terbuka:
            assert kd == "selesai"  # lunas lewat tempo = selesai, BUKAN telat
        else:
            assert kd == ("telat" if hari < 0 else "hari_ini" if hari == 0 else "akan")


def test_si_lunas_lewat_tempo_selesai_bukan_telat():
    assert FD.jatuh_tempo_keadaan("paid", False, HARI - timedelta(days=30), HARI) == "selesai"
    assert FD.jatuh_tempo_keadaan("posted", True, HARI - timedelta(days=1), HARI) == "telat"
