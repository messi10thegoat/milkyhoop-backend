"""laporan-fix-2: BASIS KAS per akun, SATU sumber untuk Laba Rugi PSAK basis kas DAN dasbor laba_rugi_kas (putusan pemilik 10 Nov; arah MASTER 12 Nov).
Pendapatan + HPP faktur proporsional saat DIBAYAR (alokasi bayar × bagian jurnal efektif ÷ total faktur), beban saat dibayar. Paritas jurnal/alokasi nyata =
uji salinan (scripts/ops/uji-salinan-laporan-psak.sh)."""
import asyncio
import datetime as dt
import inspect
import re
import uuid
from decimal import Decimal as D

import pytest

from app.services import dashboard_angka as DA
from app.services.report_engine import basis_kas as BK
from app.services.report_engine import income_statement as IS

T = "kaos-biru-konveksi"
A_JUAL, A_JASA, A_HPP, A_BEBAN, A_SELISIH = (uuid.uuid4() for _ in range(5))
AKUN = {A_JUAL: ("4-10100", "Penjualan", "REVENUE"), A_JASA: ("4-20100", "Pendapatan Jasa", "REVENUE"), A_HPP: ("5-10100", "HPP Barang", "COGS"),
        A_BEBAN: ("6-10100", "Beban Listrik", "EXPENSE"), A_SELISIH: ("7-10100", "Selisih", "OTHER_EXPENSE")}


class Kon:
    """Tiruan asyncpg: jawab per konstanta SQL; mencatat (sql, argumen); placeholder tertinggi WAJIB == jumlah argumen (pola uji lain)."""

    def __init__(self, pend=(), hpp=(), langsung=(), tagihan=()):
        self.peta = {id(BK.SQL_PENDAPATAN): pend, id(BK.SQL_HPP_FAKTUR): hpp, id(BK.SQL_BEBAN_LANGSUNG): langsung, id(BK.SQL_BEBAN_TAGIHAN): tagihan}
        self.log = []

    async def fetch(self, sql, *a):
        n = max([int(x) for x in re.findall(r"\$(\d+)", sql)] or [0])
        assert n == len(a), (n, len(a), sql[:80])
        self.log.append((sql, a))
        assert a[0] == T  # SELALU tersaring tenant (argumen pertama)
        if sql is BK.SQL_AKUN:
            return [{"id": i, "account_code": AKUN[i][0], "name": AKUN[i][1], "account_type": AKUN[i][2]} for i in a[1] if i in AKUN]
        for k, v in self.peta.items():
            if id(sql) == k:
                return [{"account_id": i, "jumlah": j} for i, j in v]
        raise AssertionError(sql[:60])


def jalan(aw):
    return asyncio.run(aw)


def det(**kw):
    return jalan(BK.detail(Kon(**kw), T, dt.date(2026, 11, 1), dt.date(2026, 11, 12)))


def test_detail_tanda_penggabungan_tipe_urut_dan_nol_dibuang():
    r = det(pend=[(A_JUAL, D("1000.004")), (A_JASA, D("500.005"))], hpp=[(A_HPP, D("400.123"))],
            langsung=[(A_BEBAN, D("200")), (A_HPP, D("0.01"))], tagihan=[(A_BEBAN, D("99.999")), (A_SELISIH, D("0.001"))])
    assert [(z["account_code"], z["balance"]) for z in r["REVENUE"]] == [("4-10100", D("-1000.00")), ("4-20100", D("-500.01"))]  # pendapatan NEGATIF (konvensi ledger)
    assert [(z["account_code"], z["balance"]) for z in r["COGS"]] == [("5-10100", D("400.13"))]            # HPP faktur + beban langsung akun sama digabung
    assert [(z["account_code"], z["balance"]) for z in r["EXPENSE"]] == [("6-10100", D("300.00"))]         # 200 + 99,999 = 299,999 -> 300,00
    assert r["OTHER_EXPENSE"] == [] and r["OTHER_INCOME"] == []                                           # 0,001 -> 0,00 dibuang
    k = BK.ringkas(r)
    assert k == {"pendapatan": D("1500.01"), "beban": D("700.13"), "laba": D("799.88")}


def test_detail_kosong_tanpa_kueri_akun():
    k = Kon()
    assert jalan(BK.detail(k, T, dt.date(2026, 11, 1), dt.date(2026, 11, 12))) == {t: [] for t in BK.TIPE_PENDAPATAN + BK.TIPE_BEBAN}
    assert len(k.log) == 4 and all(s is not BK.SQL_AKUN for s, _ in k.log)  # 4 sumber, nol akun -> tak menanya CoA


def test_sql_definisi_qbo():
    for q in (BK.SQL_PENDAPATAN, BK.SQL_HPP_FAKTUR):
        assert "receive_payment_allocations" in q and "customer_deposit_applications" in q and "NULLIF(si.total_amount, 0)" in q
        assert "is_effective_journal(je.id)" in q and "b.tgl BETWEEN $2 AND $3" in q and "GROUP BY bagian.account_id" in q
    assert "'INVOICE_REVENUE', 'INVOICE'" in BK.SQL_PENDAPATAN and "'REVENUE', 'OTHER_INCOME'" in BK.SQL_PENDAPATAN
    assert "'INVOICE_FULFILLMENT', 'DELIVERY_INVOICED'" in BK.SQL_HPP_FAKTUR and "c.account_type IN ('COGS')" in BK.SQL_HPP_FAKTUR
    assert "jl.debit - jl.credit" in BK.SQL_HPP_FAKTUR and "jl.credit - jl.debit" in BK.SQL_PENDAPATAN
    assert "kc.is_cash" in BK.SQL_BEBAN_LANGSUNG and "bill_payment_allocations" in BK.SQL_BEBAN_TAGIHAN
    assert "NULLIF(COALESCE(b.grand_total, b.amount), 0)" in BK.SQL_BEBAN_TAGIHAN
    for q in (BK.SQL_PENDAPATAN, BK.SQL_HPP_FAKTUR, BK.SQL_BEBAN_LANGSUNG, BK.SQL_BEBAN_TAGIHAN):
        assert "je.tenant_id = $1" in q  # tersaring tenant


def test_dasbor_dan_psak_satu_sumber(monkeypatch):
    async def palsu(conn, tid, mulai, akhir):
        return {"REVENUE": [{"account_id": A_JUAL, "account_code": "4-10100", "account_name": "Penjualan", "balance": D("-1000.50")}],
                "OTHER_INCOME": [], "COGS": [{"account_id": A_HPP, "account_code": "5-10100", "account_name": "HPP", "balance": D("300.25")}],
                "EXPENSE": [{"account_id": A_BEBAN, "account_code": "6-10100", "account_name": "Listrik", "balance": D("100.10")}], "OTHER_EXPENSE": []}

    async def tabu(*a, **k):
        raise AssertionError("basis kas tak boleh membaca saringan jurnal kas lama")
    monkeypatch.setattr(BK, "detail", palsu)
    monkeypatch.setattr(IS, "compute_balance", tabu)
    monkeypatch.setattr(IS, "compute_balance_detail", tabu)
    lr = jalan(DA.laba_rugi_kas(None, T, dt.date(2026, 11, 1), dt.date(2026, 11, 12)))
    m = jalan(IS.hitung_laba_rugi(None, T, "2026-11-01", "2026-11-12", "cash"))
    assert lr == {"pendapatan": D("1000.50"), "beban": D("400.35"), "laba": D("600.15")}
    assert (m["revenue"], m["cogs_raw"], m["opex_raw"], m["net"]) == (lr["pendapatan"], D("300.25"), D("100.10"), lr["laba"])  # TOTAL sama (satu sumber)
    assert m["revenue_detail"][0]["account_code"] == "4-10100" and m["basis"] == "cash"


def test_kode_lama_dihapus_dan_akrual_tak_berubah():
    for nama in ("SQL_PENDAPATAN_KAS", "SQL_BEBAN_KAS_LANGSUNG", "SQL_BEBAN_TAGIHAN_DIBAYAR"):
        assert not hasattr(DA, nama)          # yang digantikan DIHAPUS (bukan dibiarkan jadi sumber kedua)
    src = inspect.getsource(IS.hitung_laba_rugi)
    assert "cash_only" not in src and "compute_balance(conn, tenant_id, fp, start_date=start, end_date=end)" in src  # akrual tetap lewat ledger
