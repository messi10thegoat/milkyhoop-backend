"""SATU SUMBER PENGINGAT dashboard (22 Okt 2026, KONTRAK-dashboard-pengingat). Registri ⊂ kunci AKSI daftar; arah/bentuk; ambang N tunggal;
sum_in/sum_out terpisah; kunci dismiss; faktur & proforma memakai ambang tenant."""
import asyncio
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.services import dashboard_v2 as dv2
from app.services import faktur_aksi as FA
from app.services import pengingat as P
from app.services import po_daftar as PD
from app.services import proforma_aksi as PA
from app.services import so_aksi
from app.services import tagihan_daftar as TD

HARI = date(2026, 10, 22)
AKSI_MODUL = {"sales_invoices": FA.AKSI, "proformas": PA.AKSI, "sales_orders": so_aksi.AKSI, "bills": TD.AKSI_TAGIHAN, "purchase_orders": PD.AKSI}


def test_registri_kunci_anggota_aksi_daftar_dan_bentuk():
    for modul, kunci, label, arah, lane, izin in P.PENGINGAT:
        assert modul in P.MODUL_DAFTAR and kunci in AKSI_MODUL[modul], (modul, kunci)
        assert arah in P.ARAH and lane in ("now", "week", "any") and label and izin
    assert len({(m, k) for m, k, *_ in P.PENGINGAT}) == len(P.PENGINGAT)
    assert not any(m == "vendor_deposits" for m, *_ in P.PENGINGAT)  # putusan MASTER: UM vendor bukan v1
    dp = [k for m, k, *_ in P.PENGINGAT if m == "sales_orders"]
    assert "menunggu_dp" in dp and "perlu_minta_dp" in dp  # DUA pengingat, tak digabung


class _K:
    def __init__(self, n): self.n = n
    async def fetchval(self, sql, *a):
        assert "pengingat_segera_hari FROM accounting_settings" in sql
        return self.n


@pytest.mark.parametrize("n,harap", [(None, 7), (10, 10), (1, 1)])
def test_ambang_segera_satu_pembaca_bawaan_7(n, harap):
    assert asyncio.run(P.ambang_segera(_K(n), "t")) == harap
    assert FA.AMBANG_SEGERA_TEMPO == PA.AMBANG_SEGERA_TEMPO == P.AMBANG_BAWAAN == 7


def test_faktur_dan_proforma_segera_tempo_mengikuti_ambang():
    lima = HARI + timedelta(days=5)
    assert FA.aksi_faktur("posted", 100, lima, HARI, HARI, 7) == "segera_tempo"
    assert FA.aksi_faktur("posted", 100, lima, HARI, HARI, 3) == "belum_lunas"
    assert PA.aksi_proforma("issued", "confirmed", False, lima, HARI, 7) == "segera_tempo"


def test_rangkum_sum_in_out_terpisah_dan_selalu_ada():
    r = P.rangkum([])
    assert r == {"reminders": [], "sum_in": "0.00", "sum_in_ditagih": "0.00", "sum_in_dp_belum_ditagih": "0.00", "sum_out": "0.00",
                 "pengingat_versi": 1}
    ps = [{"key": "a", "arah": "masuk", "amount": "100.50", "izin": "x"}, {"key": "b", "arah": "keluar", "amount": "40.25", "izin": "y"},
          {"key": "c", "arah": "non_uang", "amount": None, "izin": "z"}, {"key": "d", "arah": "masuk", "amount": "0.50", "izin": "x"}]
    r = P.rangkum(ps)
    assert r["sum_in"] == "101.00" and r["sum_out"] == "40.25"
    assert all("izin" not in x for x in r["reminders"])


def test_hitung_hanya_count_positif_dan_bentuk(monkeypatch):
    async def amb(conn, t): return 7
    monkeypatch.setattr(P, "ambang_segera", amb)
    async def isi(conn, t, h, a):
        return {"jatuh_tempo": {"count": 2, "amount": Decimal("150000")}, "segera_tempo": {"count": 0, "amount": Decimal("0")},
                "lewat_tempo": {"count": 1, "amount": Decimal("10.5")}, "telat_kirim": {"count": 3, "amount": Decimal("999")},
                "telat_terima": {"count": 1, "amount": None}, "lewat_jatuh_tempo": {"count": 4, "amount": Decimal("2500.25")}}
    monkeypatch.setattr(P, "PENGHITUNG", {m: isi for m in P.MODUL_DAFTAR})
    out = asyncio.run(P.hitung(None, "t", HARI))
    k = {(x["modul"], x["kunci_aksi"]): x for x in out}
    assert ("sales_invoices", "segera_tempo") not in k
    f = k[("sales_invoices", "jatuh_tempo")]
    assert f["count"] == 2 and f["amount"] == "150000.00" and f["arah"] == "masuk"
    assert f["list"] == {"modul": "sales_invoices", "aksi": "jatuh_tempo"}
    assert f["key"] == "sales_invoices:jatuh_tempo:2:150000.00" and dv2.POLA_KUNCI.match(f["key"])
    so = k[("sales_orders", "telat_kirim")]
    assert so["amount"] is None and so["arah"] == "non_uang" and dv2.POLA_KUNCI.match(so["key"])
    b = k[("bills", "lewat_jatuh_tempo")]
    assert b["arah"] == "keluar" and b["amount"] == "2500.25"


def test_rangkum_tugas_memuat_reminders_dan_dismiss():
    p = [{"key": "bills:lewat_jatuh_tempo:4:2500.25", "arah": "keluar", "amount": "2500.25", "izin": "purchase_invoice"},
         {"key": "sales_invoices:jatuh_tempo:2:150000.00", "arah": "masuk", "amount": "150000.00", "izin": "sales_invoice"}]
    r = dv2.rangkum_tugas([], {"bills:lewat_jatuh_tempo:4:2500.25"}, 0, "2026-10-22T10:00:00+07:00", p)
    assert {"tasks", "sum_now", "reminders", "sum_in", "sum_out", "pengingat_versi"} <= set(r)
    assert [x["key"] for x in r["reminders"]] == ["sales_invoices:jatuh_tempo:2:150000.00"] and r["sum_out"] == "0.00"
    r0 = dv2.rangkum_tugas([], set(), 0, "x")
    assert r0["reminders"] == [] and r0["sum_in"] == "0.00"


def test_sum_in_pecah_ditagih_dan_dp_belum_ditagih_invarian():
    """MASTER 23 Okt (pola QBO/Xero): DP yang belum diminta bukan piutang. sum_in == ditagih + dp_belum_ditagih; versi tetap 1 (aditif)."""
    ps = [{"key": "f", "modul": "sales_invoices", "kunci_aksi": "jatuh_tempo", "arah": "masuk", "amount": "5511000.00", "izin": "x"},
          {"key": "m", "modul": "sales_orders", "kunci_aksi": "menunggu_dp", "arah": "masuk", "amount": "150000.00", "izin": "x"},
          {"key": "p", "modul": "sales_orders", "kunci_aksi": "perlu_minta_dp", "arah": "masuk", "amount": "60752000.00", "izin": "x"},
          {"key": "b", "modul": "bills", "kunci_aksi": "lewat_jatuh_tempo", "arah": "keluar", "amount": "1805006.00", "izin": "y"}]
    r = P.rangkum(ps)
    assert r["sum_in_dp_belum_ditagih"] == "60752000.00"
    assert r["sum_in_ditagih"] == "5661000.00"  # menunggu_dp = DP SUDAH diminta (proforma/tagihan) -> ikut ditagih
    assert r["sum_in"] == "66413000.00" == f"{Decimal(r['sum_in_ditagih']) + Decimal(r['sum_in_dp_belum_ditagih']):.2f}"
    assert r["sum_out"] == "1805006.00" and r["pengingat_versi"] == 1
    assert P.KUNCI_BELUM_DITAGIH == (("sales_orders", "perlu_minta_dp"),)
    assert set(P.KUNCI_BELUM_DITAGIH) <= {(m, k) for m, k, _l, arah, *_ in P.PENGINGAT if arah == "masuk"}
