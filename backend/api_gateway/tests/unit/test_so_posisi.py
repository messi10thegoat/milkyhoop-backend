"""P1 SO-dokumen: aturan Posisi (02 §Posisi) dan Kirim (01 §A) + putusan pemilik 1 Okt 2026 -- satu tes per aturan.

Angka dari LITERAL spek, bukan dari fungsi yang diuji (gerbang tautologis dilarang).
"""
from datetime import date
from decimal import Decimal as D

import pytest

from app.services.so_posisi import kirim, posisi

HARI = date(2026, 10, 1)  # tanggal usaha (WIB)


def P(status="confirmed", sisa=D("1000"), belum_bayar=(), ada_faktur=False, sisa_faktur=D("0"),
      ada_sj=False, dp=D("0")):
    return posisi(status, sisa, list(belum_bayar), ada_faktur, sisa_faktur, ada_sj, dp)


DP = {"purpose": "DP", "termin_ke": None}
PEL = {"purpose": "PELUNASAN", "termin_ke": None}


def T(k):
    return {"purpose": "TERMIN", "termin_ke": k}


# ---- Posisi: tiap aturan ----
def test_1_batal_muted_menang_atas_semua():
    assert P("cancelled", sisa=D("0"), belum_bayar=[DP], ada_faktur=True, sisa_faktur=D("5")) == ("Batal", True)


def test_2_lunas_muted_bila_sisa_nol_atau_negatif():
    assert P(sisa=D("0"), belum_bayar=[DP]) == ("Lunas", True)
    assert P(sisa=D("-1")) == ("Lunas", True)


def test_4_menunggu_dp():
    assert P(belum_bayar=[DP], ada_faktur=True, sisa_faktur=D("10")) == ("Menunggu DP", False)


def test_4_menunggu_termin_k():
    assert P(belum_bayar=[T(2)]) == ("Menunggu termin 2", False)


def test_4_pelunasan_tanpa_faktur_menunggu_tagihan():
    assert P(belum_bayar=[PEL]) == ("Menunggu tagihan", False)


def test_putusan3_pelunasan_dengan_faktur_bersisa_menunggu_pelunasan():
    # SO-2609-0020 grapgrap: PRO PELUNASAN issued 0/945.000 + faktur sisa 945.000
    assert P(belum_bayar=[PEL], ada_faktur=True, sisa_faktur=D("945000")) == ("Menunggu pelunasan", False)


def test_4_proforma_pertama_yang_belum_bayar_menentukan():
    assert P(belum_bayar=[T(1), DP]) == ("Menunggu termin 1", False)
    assert P(belum_bayar=[PEL, DP], ada_faktur=True, sisa_faktur=D("1")) == ("Menunggu DP", False)


def test_5_menunggu_pelunasan():
    assert P(ada_faktur=True, sisa_faktur=D("1"), ada_sj=True, dp=D("5")) == ("Menunggu pelunasan", False)


def test_6_perlu_faktur_dikirim_tanpa_faktur():
    assert P(ada_sj=True, dp=D("5")) == ("Perlu faktur", False)


def test_6_tak_berlaku_bila_faktur_ada_tapi_lunas():
    # faktur ada, sisanya 0, SO masih bersisa (belum semua ditagih) -> bukan "Perlu faktur"; DP -> Diproses
    assert P(ada_faktur=True, sisa_faktur=D("0"), ada_sj=True, dp=D("5")) == ("Diproses", False)


def test_6b_sebagian_ditagih_faktur_lunas_tanpa_dp():
    # kaos SO-2609-0333: 2/5 pcs difaktur + lunas (RCV), 300.000 belum ditagih, tanpa uang muka
    assert P(ada_faktur=True, sisa_faktur=D("0"), ada_sj=True) == ("Sebagian ditagih", False)
    assert P(ada_faktur=True, sisa_faktur=D("0")) == ("Sebagian ditagih", False)


def test_6b_tak_berlaku_bila_ada_dp_atau_faktur_bersisa():
    assert P(ada_faktur=True, sisa_faktur=D("0"), dp=D("5")) == ("Diproses", False)
    assert P(ada_faktur=True, sisa_faktur=D("1")) == ("Menunggu pelunasan", False)
    assert P(sisa=D("0"), ada_faktur=True) == ("Lunas", True)


def test_7_produksi_ada_dp():
    assert P(dp=D("1000000")) == ("Diproses", False)


def test_8_belum_ditagih():
    assert P() == ("Belum ditagih", False)
    assert P("draft") == ("Belum ditagih", False)


# ---- Kirim ----
def K(status="confirmed", d=date(2026, 10, 2), perlu=True, semua=False):
    return kirim(status, d, perlu, semua, HARI)


def test_kirim_batal():
    assert K("cancelled") == ("—", "muted")


def test_putusan8_selesai_strip_meski_baris_belum_terkirim_dan_telat():
    assert K("completed", d=date(2026, 9, 9)) == ("—", "muted")


@pytest.mark.parametrize("status", ["invoiced", "partial_invoiced", "confirmed", "partial_shipped"])
def test_putusan8_terbuka_termasuk_lunas_tetap_telat_tebal(status):
    # SO-2609-0033/0031 grapgrap: invoiced + Lunas, baris perlu_kirim=true tanpa SJ
    assert K(status, d=date(2026, 9, 9)) == ("9 Sep, telat", "strong")


def test_putusan2_tanpa_baris_perlu_kirim_strip():
    assert K(perlu=False, d=date(2026, 9, 26)) == ("—", "muted")  # dulu "26 Sep, telat" tebal (SO-0024/0025)


def test_putusan2_semua_terkirim():
    assert K(semua=True, d=date(2026, 9, 1)) == ("Terkirim", "muted")


def test_kirim_tanpa_tanggal():
    assert K(d=None) == ("—", "muted")


def test_kirim_hari_ini_tebal():
    assert K(d=date(2026, 10, 1)) == ("Hari ini", "strong")


def test_kirim_besok():
    assert K(d=date(2026, 10, 2)) == ("Besok", "normal")


def test_kirim_telat_tebal():
    assert K(d=date(2026, 9, 28)) == ("28 Sep, telat", "strong")


def test_kirim_nanti():
    assert K(d=date(2026, 10, 21)) == ("21 Okt", "normal")
    assert K(d=date(2026, 12, 3)) == ("3 Des", "normal")


def test_kirim_besok_lintas_bulan():
    assert kirim("confirmed", date(2026, 11, 1), True, False, date(2026, 10, 31)) == ("Besok", "normal")


@pytest.mark.parametrize("bulan,teks", [(1, "Jan"), (5, "Mei"), (8, "Agu"), (10, "Okt"), (12, "Des")])
def test_nama_bulan_indonesia(bulan, teks):
    assert K(d=date(2027, bulan, 9))[0] == f"9 {teks}"


# ---- Kirim x jam tenant (gerbang 05 D2): hari = tanggal_dokumen (zona tenant), BUKAN tanggal UTC server ----
@pytest.mark.parametrize("utc,d,status,semua,harap", [
    # 23.30 WIB 30 Sep (= 16.30 UTC 30 Sep)
    ("2026-09-30T16:30", date(2026, 9, 30), "confirmed", False, ("Hari ini", "strong")),
    ("2026-09-30T16:30", date(2026, 10, 1), "confirmed", False, ("Besok", "normal")),
    ("2026-09-30T16:30", date(2026, 9, 29), "confirmed", False, ("29 Sep, telat", "strong")),
    ("2026-09-30T16:30", date(2026, 9, 29), "confirmed", True, ("Terkirim", "muted")),
    ("2026-09-30T16:30", None, "confirmed", False, ("—", "muted")),
    ("2026-09-30T16:30", date(2026, 9, 30), "cancelled", False, ("—", "muted")),
    # 00.30 WIB 1 Okt (= 17.30 UTC 30 Sep -- tanggal UTC masih 30 Sep)
    ("2026-09-30T17:30", date(2026, 10, 1), "confirmed", False, ("Hari ini", "strong")),
    ("2026-09-30T17:30", date(2026, 10, 2), "confirmed", False, ("Besok", "normal")),
    ("2026-09-30T17:30", date(2026, 9, 30), "confirmed", False, ("30 Sep, telat", "strong")),
    ("2026-09-30T17:30", date(2026, 9, 30), "confirmed", True, ("Terkirim", "muted")),
    ("2026-09-30T17:30", None, "confirmed", False, ("—", "muted")),
    ("2026-09-30T17:30", date(2026, 10, 1), "cancelled", False, ("—", "muted")),
])
def test_kirim_pada_jam_tenant_23_30_dan_00_30(monkeypatch, utc, d, status, semua, harap):
    import asyncio
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    from app.utils import tanggal_tenant as tt
    saat = datetime.fromisoformat(utc).replace(tzinfo=timezone.utc)

    class _Jam(datetime):
        @classmethod
        def now(cls, tz=None):
            return saat
    async def zona(conn, tenant_id):
        return ZoneInfo("Asia/Jakarta")
    monkeypatch.setattr(tt, "datetime", _Jam)
    monkeypatch.setattr(tt, "zona_tenant", zona)
    hari = asyncio.run(tt.tanggal_dokumen(None, "t"))
    assert kirim(status, d, True, semua, hari) == harap
