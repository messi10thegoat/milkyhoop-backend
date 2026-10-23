"""Saring umur_* Faktur Pembelian (KONTRAK-dashboard-T2 §5, 23 Okt 2026): SATU rumus umur (jatuh_tempo.kelas_umur / sql_umur), SQL == Python
di semua batas; kunci SARING ?aksi= + /summary, BUKAN pill (partisi AKSI_TAGIHAN & aksi baris tak berubah)."""
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.services import jatuh_tempo as JT
from app.services import tagihan_daftar as TD

APP = Path(__file__).resolve().parents[2] / "app"
H = date(2026, 10, 23)


def _eval_sql(fragmen: str, eff: date, hari: date) -> bool:
    """Penilai mini predikat sql_umur (eff = 'E', hari = 'H'): tanggal -> ordinal; 'H::date - n' -> H - n."""
    py = fragmen.replace("H::date", "H").replace(" AND ", " and ")
    return bool(eval(py, {}, {"E": eff.toordinal(), "H": hari.toordinal()}))


@pytest.mark.parametrize("telat,kunci", [(-5, "umur_current"), (0, "umur_current"), (1, "umur_1_30"), (30, "umur_1_30"),
                                         (31, "umur_31_60"), (60, "umur_31_60"), (61, "umur_61_90"), (90, "umur_61_90"),
                                         (91, "umur_90_plus"), (400, "umur_90_plus")])
def test_kelas_umur_batas(telat, kunci):
    assert JT.kelas_umur(H - timedelta(days=telat), H) == kunci


def test_tanpa_tanggal_tanpa_umur():
    assert JT.kelas_umur(None, H) is None


@pytest.mark.parametrize("telat", list(range(-3, 95)))
def test_sql_setara_python_tepat_satu_kunci(telat):
    eff = H - timedelta(days=telat)
    cocok = [k for k in JT.UMUR_KUNCI if _eval_sql(JT.sql_umur(k, "E", "H"), eff, H)]
    assert cocok == [JT.kelas_umur(eff, H)]


def test_kunci_dan_bucket_kontrak_t2():
    assert JT.UMUR_KUNCI == ("umur_current", "umur_1_30", "umur_31_60", "umur_61_90", "umur_90_plus")
    assert JT.UMUR_KE_BUCKET == {"umur_current": "current", "umur_1_30": "d1_30", "umur_31_60": "d31_60", "umur_61_90": "d61_90",
                                 "umur_90_plus": "d90_plus"}
    assert TD.SARING_UMUR == JT.UMUR_KUNCI


def test_umur_tagihan_hanya_terbuka_dan_jt_efektif():
    lama = H - timedelta(days=45)
    for k in ("draft", "void", "paid"):
        assert TD.umur_tagihan(k, lama, lama, H) is None
    for k in TD.TERBUKA:
        assert TD.umur_tagihan(k, lama, None, H) == "umur_31_60"
    assert TD.umur_tagihan("unpaid", None, H - timedelta(days=95), H) == "umur_90_plus"  # due kosong -> tanggal dokumen
    assert TD.umur_tagihan("unpaid", None, None, H) is None


def test_sql_aksi_umur_terbuka_dan_parameter():
    for k in TD.SARING_UMUR:
        s = TD.sql_aksi(k, "$5", "$6")
        assert TD.sql_terbuka() in s and TD.sql_jt_efektif() in s and "$6" not in s
        pred, par = TD.sql_aksi_berparam(k, 5, H)  # tanpa ambang: umur tak butuh N
        assert par == [H] and "$5::date" in pred and "$6" not in pred


def test_bukan_pill_partisi_dan_aksi_baris_tetap():
    assert TD.AKSI_TAGIHAN == ("lewat_jatuh_tempo", "segera_tempo", "perlu_diposting")
    assert not set(TD.SARING_UMUR) & set(TD.AKSI_TAGIHAN)
    f = TD.fakta_baris("overdue", 0, 1000, H - timedelta(days=45), None, H, 7)
    assert f["aksi"] == "lewat_jatuh_tempo" and f["aksi_semua"] == ["lewat_jatuh_tempo"]


def test_daftar_dan_summary_menerima_umur():
    bs = (APP / "services/bills_service.py").read_text()
    lb = bs[bs.index("    async def list_bills("):bs.index("    async def get_bill(")]
    assert "aksi in TD.SARING_UMUR" in lb
    sm = bs[bs.index("    async def get_summary("):]
    assert sm.count("TD.AKSI_TAGIHAN + TD.SARING_UMUR") == 2  # kolom kueri + kunci respons {k}_count/_value
    assert re.search(r'"umur_current", "umur_1_30", "umur_31_60", "umur_61_90", "umur_90_plus"', (APP / "routers/bills.py").read_text())
