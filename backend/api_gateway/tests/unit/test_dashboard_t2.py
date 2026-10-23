"""Dashboard T2 (23 Okt 2026, backend/docs/KONTRAK-dashboard-T2-20261023.md): periode baru, pembanding period-to-date, tren 12 bulan,
umur piutang/hutang + kunci saring umur_* faktur. Syarat MASTER: batas bulan (31 Okt vs 30 Sep, Mar vs Feb), kabisat, null != "0.00",
needs_action hanya kas bersih, Σ bucket + kredit == total."""
import asyncio
import os
from datetime import date
from decimal import Decimal
from uuid import UUID

os.environ.setdefault("OPENAI_API_KEY", "boneka")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.services import dashboard_v2 as DV  # noqa: E402
from app.services import dashboard_angka as DA  # noqa: E402
from app.services import faktur_aksi as FA  # noqa: E402
from app.services import jatuh_tempo as JT  # noqa: E402
from app.routers import dashboard_v2 as RV  # noqa: E402

NOL = Decimal("0")


# ───────── periode ─────────

def test_kunci_lama_tetap_makna_lama():
    h = date(2026, 10, 23)
    for p in DV.PERIODE:
        assert DV.rentang_periode_t2(p, h) == DV.rentang_periode(p, h)
        assert DV.label_periode_t2(p, *DV.rentang_periode(p, h)) == DV.LABEL_PERIODE[p]


def test_periode_baru_kalender_dan_fiskal():
    h = date(2026, 10, 23)
    assert DV.rentang_periode_t2("bulan_ini", h) == (date(2026, 10, 1), date(2026, 10, 31))
    assert DV.rentang_periode_t2("bulan_lalu", h) == (date(2026, 9, 1), date(2026, 9, 30))
    assert DV.rentang_periode_t2("bulan_lalu", date(2026, 1, 5)) == (date(2025, 12, 1), date(2025, 12, 31))
    assert DV.rentang_periode_t2("kuartal_ini", h) == (date(2026, 10, 1), date(2026, 12, 31))
    assert DV.rentang_periode_t2("tahun_ini", h) == (date(2026, 1, 1), date(2026, 12, 31))
    # tahun buku mulai April: 23 Okt 2026 -> FY Apr 2026 – Mar 2027, kuartal 3 = Okt–Des
    assert DV.rentang_periode_t2("tahun_ini", h, 4) == (date(2026, 4, 1), date(2027, 3, 31))
    assert DV.rentang_periode_t2("kuartal_ini", h, 4) == (date(2026, 10, 1), date(2026, 12, 31))
    assert DV.label_periode_t2("kuartal_ini", date(2026, 10, 1), date(2026, 12, 31), 4) == "Kuartal 3 2026"
    assert DV.rentang_periode_t2("tahun_ini", date(2026, 2, 10), 4) == (date(2025, 4, 1), date(2026, 3, 31))
    assert DV.label_periode_t2("tahun_ini", date(2025, 4, 1), date(2026, 3, 31), 4) == "Tahun buku 2025/2026"
    assert DV.label_periode_t2("bulan_ini", date(2026, 10, 1), date(2026, 10, 31)) == "Oktober 2026"


@pytest.mark.parametrize("dari,sampai", [(None, date(2026, 1, 1)), (date(2026, 2, 1), date(2026, 1, 1)),
                                         (date(2025, 1, 1), date(2026, 1, 2))])
def test_kustom_tak_sah_ditolak(dari, sampai):
    with pytest.raises(DV.PeriodeTakSah):
        DV.rentang_periode_t2("kustom", date(2026, 10, 23), 1, dari, sampai)


def test_kustom_366_hari_boleh():
    assert DV.rentang_periode_t2("kustom", date(2026, 10, 23), 1, date(2025, 1, 1), date(2026, 1, 1)) == (date(2025, 1, 1), date(2026, 1, 1))


# ───────── pembanding period-to-date (batas bulan, kabisat) ─────────

def test_31_okt_vs_30_sep_dan_tahun_lalu():
    h = date(2026, 10, 31)
    r, pp, py = DV.rentang_pembanding("bulan_ini", date(2026, 10, 1), date(2026, 10, 31), h)
    assert r == (date(2026, 10, 1), date(2026, 10, 31))
    assert pp == (date(2026, 9, 1), date(2026, 9, 30))
    assert py == (date(2025, 10, 1), date(2025, 10, 31))


def test_bulan_berjalan_hari_ke_hari():
    r, pp, py = DV.rentang_pembanding("bulan_ini", date(2026, 10, 1), date(2026, 10, 31), date(2026, 10, 23))
    assert r == (date(2026, 10, 1), date(2026, 10, 23)) and pp == (date(2026, 9, 1), date(2026, 9, 23))
    assert py == (date(2025, 10, 1), date(2025, 10, 23))


def test_maret_vs_februari_biasa_dan_kabisat():
    _, pp, _ = DV.rentang_pembanding("bulan_ini", date(2026, 3, 1), date(2026, 3, 31), date(2026, 3, 31))
    assert pp == (date(2026, 2, 1), date(2026, 2, 28))
    _, pp, _ = DV.rentang_pembanding("bulan_ini", date(2028, 3, 1), date(2028, 3, 31), date(2028, 3, 31))
    assert pp == (date(2028, 2, 1), date(2028, 2, 29))
    _, pp, _ = DV.rentang_pembanding("bulan_ini", date(2028, 3, 1), date(2028, 3, 31), date(2028, 3, 30))
    assert pp == (date(2028, 2, 1), date(2028, 2, 29))  # 30 Mar dijepit ke akhir Feb
    _, pp, _ = DV.rentang_pembanding("bulan_ini", date(2026, 3, 1), date(2026, 3, 31), date(2026, 3, 15))
    assert pp == (date(2026, 2, 1), date(2026, 2, 15))


def test_februari_kabisat_vs_tahun_lalu_dan_maret():
    _, pp, py = DV.rentang_pembanding("bulan_ini", date(2028, 2, 1), date(2028, 2, 29), date(2028, 2, 29))
    assert py == (date(2027, 2, 1), date(2027, 2, 28)) and pp == (date(2028, 1, 1), date(2028, 1, 31))
    # 28 Feb 2028 BUKAN akhir bulan (kabisat) -> hari ke hari
    _, pp, py = DV.rentang_pembanding("bulan_ini", date(2028, 2, 1), date(2028, 2, 29), date(2028, 2, 28))
    assert pp == (date(2028, 1, 1), date(2028, 1, 28)) and py == (date(2027, 2, 1), date(2027, 2, 28))
    # 28 Feb 2027 (akhir bulan) -> tahun lalu 29 Feb 2026? 2026 bukan kabisat -> 28 Feb 2026
    _, _, py = DV.rentang_pembanding("bulan_ini", date(2027, 2, 1), date(2027, 2, 28), date(2027, 2, 28))
    assert py == (date(2026, 2, 1), date(2026, 2, 28))


def test_bulan_lalu_penuh_vs_bulan_sebelumnya_penuh():
    _, pp, py = DV.rentang_pembanding("bulan_lalu", date(2026, 9, 1), date(2026, 9, 30), date(2026, 10, 23))
    assert pp == (date(2026, 8, 1), date(2026, 8, 31)) and py == (date(2025, 9, 1), date(2025, 9, 30))


def test_kuartal_tahun_minggu_hari_kustom():
    _, pp, _ = DV.rentang_pembanding("kuartal_ini", date(2026, 10, 1), date(2026, 12, 31), date(2026, 11, 30))
    assert pp == (date(2026, 7, 1), date(2026, 8, 31))  # 30 Nov akhir bulan -> akhir Agu
    _, pp, _ = DV.rentang_pembanding("tahun_ini", date(2026, 1, 1), date(2026, 12, 31), date(2026, 10, 23))
    assert pp == (date(2025, 1, 1), date(2025, 10, 23))
    _, pp, _ = DV.rentang_pembanding("week", date(2026, 10, 19), date(2026, 10, 25), date(2026, 10, 23))
    assert pp == (date(2026, 10, 12), date(2026, 10, 16))
    _, pp, _ = DV.rentang_pembanding("day", date(2026, 10, 23), date(2026, 10, 23), date(2026, 10, 23))
    assert pp == (date(2026, 10, 22), date(2026, 10, 22))
    r, pp, py = DV.rentang_pembanding("kustom", date(2026, 1, 10), date(2026, 1, 19), date(2026, 10, 23))
    assert r == (date(2026, 1, 10), date(2026, 1, 19)) and pp == (date(2025, 12, 31), date(2026, 1, 9))
    assert py == (date(2025, 1, 10), date(2025, 1, 19))


def test_delta_pct_dan_null():
    assert DV.delta_pct(Decimal("110"), Decimal("100")) == 10.0
    assert DV.delta_pct(Decimal("-50"), Decimal("-100")) == 50.0  # |pembanding|
    assert DV.delta_pct(Decimal("5"), NOL) is None
    assert DV._objek_banding(Decimal("5"), None) is None
    assert DV._objek_banding(Decimal("5"), NOL) == {"value": "0.00", "delta": "5.00", "delta_pct": None}


def _palsukan_metrik(monkeypatch, per_rentang):
    async def metrik(conn, t, rentang):
        return [per_rentang[rr] for rr in rentang]
    monkeypatch.setattr(DV, "_metrik_rentang_banyak", metrik)


def test_pembanding_null_sebelum_jurnal_pertama_dan_needs_action_hanya_kas(monkeypatch):
    h = date(2026, 10, 23)
    m = lambda r, e, c: {"revenue": Decimal(r), "expense": Decimal(e), "profit": Decimal(r) - Decimal(e), "net_cash": Decimal(c)}
    _palsukan_metrik(monkeypatch, {(date(2026, 10, 1), h): m("100", "300", "-5"),
                                   (date(2026, 9, 1), date(2026, 9, 23)): m("50", "10", "7")})
    out = asyncio.run(DV.pembanding(None, "t", "bulan_ini", date(2026, 10, 1), date(2026, 10, 31), h, date(2026, 7, 1)))
    assert out["available"] is True
    mt = out["metrics"]
    assert mt["revenue"] == {"current": "100.00", "prev_period": {"value": "50.00", "delta": "50.00", "delta_pct": 100.0},
                             "prev_year": None}
    assert mt["profit"]["current"] == "-200.00" and "needs_action" not in mt["profit"]
    assert "needs_action" not in mt["expense"] and "needs_action" not in mt["revenue"]
    assert mt["net_cash"]["needs_action"] is True
    assert out["range"]["label"] == "1–23 Okt 2026" and out["prev_year"]["start"] == "2025-10-01"


def test_pembanding_tak_tersedia_tanpa_jurnal(monkeypatch):
    h = date(2026, 10, 23)
    _palsukan_metrik(monkeypatch, {(date(2026, 10, 1), h): {"revenue": NOL, "expense": NOL, "profit": NOL, "net_cash": NOL}})
    out = asyncio.run(DV.pembanding(None, "t", "bulan_ini", date(2026, 10, 1), date(2026, 10, 31), h, None))
    assert out["available"] is False and out["metrics"]["net_cash"]["needs_action"] is False
    assert all(out["metrics"][k]["prev_period"] is None and out["metrics"][k]["prev_year"] is None for k in out["metrics"])


# ───────── tren 12 bulan ─────────

def test_tren_12_titik_null_sebelum_data_partial_bulan_ini(monkeypatch):
    async def bulanan(conn, t, a, b):
        assert (a, b) == (date(2025, 11, 1), date(2026, 10, 31))
        return {date(2026, 9, 1): {"pendapatan": Decimal("52690000"), "beban": Decimal("9385451"), "laba": Decimal("43304549")}}
    monkeypatch.setattr(DV.DA, "laba_rugi_bulanan", bulanan)
    out = asyncio.run(DV.tren_12_bulan(None, "t", date(2026, 10, 23), date(2026, 7, 1)))
    p = out["points"]
    assert len(p) == 12 and p[0]["month"] == "2025-11" and p[-1]["month"] == "2026-10" and p[-1]["partial"] is True
    assert p[7]["month"] == "2026-06" and p[7]["revenue"] is None  # sebelum jurnal pertama: null, bukan "0.00"
    assert p[8] == {"month": "2026-07", "label": "Jul", "revenue": "0.00", "expense": "0.00", "profit": "0.00", "partial": False}
    assert p[10]["revenue"] == "52690000.00" and p[10]["profit"] == "43304549.00"
    assert sum(1 for x in p if x["partial"]) == 1


def test_laba_rugi_bulanan_filter_sama_dengan_laba_rugi():
    sql = DA.SQL_LABA_RUGI_BULANAN
    assert "is_effective_journal(je.id)" in sql and "je.status = 'POSTED'" in sql and "je.tenant_id = $1" in sql
    assert DA.TIPE_PENDAPATAN == ("REVENUE", "OTHER_INCOME") and DA.TIPE_BEBAN == ("COGS", "EXPENSE", "OTHER_EXPENSE")


# ───────── umur ─────────

def _r(idk, sisa, due, kolom="invoice_id", tgl="invoice_date", t=date(2026, 9, 1)):
    return {kolom: UUID(int=idk) if idk else None, "outstanding": Decimal(sisa), "due_date": due, tgl: t}


def test_umur_bucket_sigma_kredit_dan_batas():
    h = date(2026, 10, 23)
    rows = [_r(1, "600000", h), _r(2, "100", date(2026, 10, 22)), _r(3, "200", date(2026, 9, 23)),
            _r(4, "300", date(2026, 9, 22)), _r(5, "400", date(2026, 7, 25)), _r(6, "500", date(2026, 7, 24)),
            _r(7, "700", None, t=date(2026, 10, 30)), _r(None, "-46000", h), _r(8, "-10", h)]
    out = DV.umur_blok(rows, h, "sales_invoices", "invoice_id", "invoice_date", True)
    b = {x["key"]: x for x in out["buckets"]}
    assert [x["key"] for x in out["buckets"]] == ["current", "d1_30", "d31_60", "d61_90", "d90_plus"]
    assert (b["current"]["count"], b["current"]["amount"]) == (2, "600700.00")  # due NULL -> tanggal faktur (30 Okt, belum)
    assert (b["d1_30"]["count"], b["d31_60"]["count"], b["d61_90"]["count"], b["d90_plus"]["count"]) == (2, 1, 1, 1)
    assert b["d1_30"]["amount"] == "300.00" and b["d90_plus"]["amount"] == "500.00"
    assert out["unapplied_credits"] == "-46010.00"
    s = sum(Decimal(x["amount"]) for x in out["buckets"]) + Decimal(out["unapplied_credits"])
    assert s == Decimal(out["total"]) == sum(Decimal(x["outstanding"]) for x in rows)
    assert b["d1_30"]["list"] == {"modul": "sales_invoices", "aksi": "umur_1_30"}


def test_umur_tagihan_list_null_sampai_live():
    out = DV.umur([], [_r(1, "5", date(2026, 10, 1), "bill_id", "bill_date")], date(2026, 10, 23))
    assert all(x["list"] is None for x in out["payables"]["buckets"]) and out["payables"]["total"] == "5.00"
    assert all(x["list"] is not None for x in out["receivables"]["buckets"]) and out["receivables"]["total"] == "0.00"


def test_umur_faktur_python_memakai_rumus_tunggal():
    h = date(2026, 10, 23)
    assert FA.umur_faktur("draft", 5, h, h, h) is None and FA.umur_faktur("posted", 0, h, h, h) is None
    assert FA.umur_faktur("posted", 5, None, date(2026, 9, 22), h) == JT.kelas_umur(date(2026, 9, 22), h) == "umur_31_60"
    for k in JT.UMUR_KUNCI:
        sql = FA.sql_umur(k, "$2")
        assert "si.status NOT IN ('draft', 'void')" in sql and JT.sql_umur(k, FA.SQL_JATUH_TEMPO_EFEKTIF, "$2") in sql
    pred, par = FA.sql_umur_berparam("umur_1_30", 4, h)
    assert "$4" in pred and par == [h]


def test_umur_bukan_pill_partisi_aksi_tak_berubah():
    assert FA.AKSI == ("perlu_diterbitkan", "belum_lunas", "segera_tempo", "jatuh_tempo")
    assert not set(JT.UMUR_KUNCI) & set(FA.AKSI)


# ───────── hutang aditif ─────────

def test_hutang_medan_v1_tetap_plus_aditif():
    h = date(2026, 10, 23)
    rows = [{"vendor_id": UUID(int=1), "vendor_name": "A", "bill_id": UUID(int=2), "bill_number": "B-1",
             "due_date": date(2026, 10, 1), "outstanding": Decimal("1805006")},
            {"vendor_id": UUID(int=1), "vendor_name": "A", "bill_id": None, "bill_number": "VENDOR-CREDIT",
             "due_date": h, "outstanding": Decimal("-6")}]
    out = asyncio.run(DV.hutang(None, "t", h, rows))
    assert (out["total"], out["overdue_total"]) == ("1805000.00", "1805006.00")
    assert out["not_due"] == "-6.00" and out["overdue_count"] == 1 and out["vendor_count"] == 1
    assert [x["number"] for x in out["due_7d"]] == ["B-1"]


# ───────── rute ─────────

class _Req:
    def __init__(self):
        from types import SimpleNamespace
        self.state = SimpleNamespace(user={"tenant_id": "t", "user_id": "u"})


def test_rute_kustom_tanpa_from_to_panggilan_langsung_422():
    """Default from/to = objek Query pada panggilan langsung: tetap 422, bukan TypeError."""
    with pytest.raises(HTTPException) as e:
        asyncio.run(RV.ringkasan_v2(_Req(), period="kustom"))
    assert e.value.status_code == 422


@pytest.mark.parametrize("dari,sampai", [(None, None), (date(2026, 2, 1), date(2026, 1, 1)), (date(2025, 1, 1), date(2026, 1, 2))])
def test_rute_kustom_tak_sah_422_sebelum_db(dari, sampai):
    with pytest.raises(HTTPException) as e:
        asyncio.run(RV.ringkasan_v2(_Req(), period="kustom", from_=dari, to=sampai))
    assert e.value.status_code == 422 and e.value.detail["code"] == "PERIOD_INVALID"


def test_rute_menerima_kunci_baru_dan_lama():
    import inspect
    import re
    pola = re.search(r'pattern="\^\(([^)]*)\)\$"', inspect.getsource(RV.ringkasan_v2)).group(1).split("|")
    assert set(pola) == set(DV.PERIODE + DV.PERIODE_T2)


def test_laba_rugi_rentang_satu_kueri_filter_per_rentang():
    """Pembanding: SEMUA rentang dalam satu kueri; filter = laba_rugi (POSTED, efektif, tenant) + rentang per kolom."""
    tangkap = {}

    class C:
        async def fetchrow(self, sql, *par):
            tangkap["sql"], tangkap["par"] = sql, par
            return [Decimal("100"), Decimal("40"), Decimal("7"), Decimal("1")]
    r = [(date(2026, 10, 1), date(2026, 10, 23)), (date(2026, 9, 1), date(2026, 9, 23))]
    out = asyncio.run(DA.laba_rugi_rentang(C(), "t", r))
    assert out == [{"pendapatan": Decimal("100"), "beban": Decimal("40"), "laba": Decimal("60")},
                   {"pendapatan": Decimal("7"), "beban": Decimal("1"), "laba": Decimal("6")}]
    sql = tangkap["sql"]
    assert "is_effective_journal(je.id)" in sql and "je.status = 'POSTED'" in sql and sql.count("BETWEEN") == 4
    assert tangkap["par"][-2:] == (date(2026, 9, 1), date(2026, 10, 23))


def test_arus_kas_neto_rentang_satu_arus_kas_harian(monkeypatch):
    panggil = []

    async def harian(conn, t, a, b):
        panggil.append((a, b))
        return {date(2026, 9, 5): (Decimal("10"), Decimal("3")), date(2026, 10, 2): (Decimal("1"), Decimal("5"))}
    monkeypatch.setattr(DA, "arus_kas_harian", harian)
    out = asyncio.run(DA.arus_kas_neto_rentang(None, "t", [(date(2026, 10, 1), date(2026, 10, 23)), (date(2026, 9, 1), date(2026, 9, 23))]))
    assert out == [Decimal("-4"), Decimal("7")] and panggil == [(date(2026, 9, 1), date(2026, 10, 23))]


def test_daftar_faktur_tanpa_aksi_lewat_panggilan_langsung_tak_galat():
    """Gerbang merge 23 Okt menolak T2: panggilan langsung list_invoices tanpa aksi -> default = objek Query (bukan None);
    `aksi.startswith` meledak. Cabang umur WAJIB memeriksa tipe (isinstance str) seperti `aksi in _fa.AKSI`."""
    import inspect
    from app.routers import sales_invoices as SI
    src = inspect.getsource(SI.list_invoices)
    assert "isinstance(aksi, str) and aksi in _UMUR_KUNCI" in src and "aksi.startswith" not in src
    assert SI._UMUR_KUNCI == JT.UMUR_KUNCI
