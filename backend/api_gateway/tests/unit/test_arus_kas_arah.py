"""laporan-fix-2 (c)(d): arus kas metode langsung = aliran jurnal EFEKTIF (satu definisi dgn kartu dasbor) + butir dipecah per label DAN arah
(penerimaan dan pembayaran tak dinetokan). Label "(masuk)"/"(keluar)" HANYA bila satu label punya dua arah (putusan MASTER 12 Nov). Kas awal/akhir tetap
saldo POSTED (rekonsiliasi kaos menampakkan jurnal VOID lama: tiket data). Paritas nyata dasbor == Σ butir = uji salinan."""
import asyncio
import inspect
from decimal import Decimal as D

import pytest

from app.services.report_engine import cash_flow as CF
from app.services.report_engine import footing as F


def baris_sql(kategori, tipe, arah, jumlah, sub=None, nama=None):
    return {"flow_category": kategori, "account_type": tipe, "arah": arah, "psak_sub_category": sub, "account_name": nama, "net_flow": D(str(jumlah))}


class Kon:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        return self.rows

    async def fetchval(self, sql, *a):
        return 0


@pytest.fixture
def jalan(monkeypatch):
    async def saldo(conn, tid, fp, start_date=None, end_date=None):
        return D("1000")
    monkeypatch.setattr(CF, "compute_balance", saldo)

    def _j(rows):
        k = Kon(rows)
        return asyncio.run(CF.generate_cash_flow(k, "t", "2026-01-01", "2026-11-11")), k
    return _j


def test_label_dua_arah_dipecah_satu_arah_tetap(jalan):
    r, k = jalan([baris_sql("OPERATING", "LIABILITY", 1, 201000, nama="Uang Muka Pelanggan"),
                  baris_sql("OPERATING", "LIABILITY", -1, -378000, nama="Uang Muka Pelanggan"),
                  baris_sql("OPERATING", "RECEIVABLE", 1, 20783400, sub="TRADE_RECEIVABLE", nama="Piutang Usaha"),
                  baris_sql("OPERATING", "PAYABLE", -1, -48451584, sub="TRADE_PAYABLE", nama="Utang Usaha")])
    butir = {(v["label"], v["amount"]) for v in r["operasi"]["items"]}
    assert ("Uang Muka Pelanggan (masuk)", 201000.0) in butir and ("Uang Muka Pelanggan (keluar)", -378000.0) in butir  # DP masuk TAK tertelan neto
    assert ("Penerimaan piutang usaha", 20783400.0) in butir and ("Pembayaran utang usaha", -48451584.0) in butir       # satu arah: label tetap
    assert len(r["operasi"]["items"]) == 4
    assert r["operasi"]["total"] == 201000 - 378000 + 20783400 - 48451584            # total neto TAK berubah oleh pemecahan
    baris = [b for b in r["baris"] if b["kunci"] == "butir"]
    assert sum(D(b["nilai"]) for b in baris if D(b["nilai"]) > 0) == D("20984400.00")   # Σ masuk == uang masuk per jurnal (== dasbor)
    assert -sum(D(b["nilai"]) for b in baris if D(b["nilai"]) < 0) == D("48829584.00")


def test_butir_sama_label_sama_arah_digabung_dan_arah_beda_kategori(jalan):
    r, _ = jalan([baris_sql("OPERATING", "REVENUE", 1, 100, nama="x"), baris_sql("OPERATING", "REVENUE", 1, 50, nama="x"),
                  baris_sql("FINANCING", "EQUITY", -1, -30), baris_sql("FINANCING", "EQUITY", 1, 70)])
    assert r["operasi"]["items"] == [{"label": "Penerimaan dari pelanggan", "amount": 150.0}]
    assert {(v["label"], v["amount"]) for v in r["pendanaan"]["items"]} == {("Setoran modal / prive (masuk)", 70.0), ("Setoran modal / prive (keluar)", -30.0)}


def test_footing_tetap_jumlah_dari_butir_tampil_dan_sql_efektif_per_arah(jalan):
    r, k = jalan([baris_sql("OPERATING", "LIABILITY", 1, 201000.4, nama="Uang Muka Pelanggan"),
                  baris_sql("OPERATING", "LIABILITY", -1, -378000.4, nama="Uang Muka Pelanggan")])
    F.footing("arus_kas", r["baris"], 0)
    n = {b["kunci"]: b for b in r["baris"] if b["jenis"] != "judul"}
    assert n["kas_neto_operasi"]["nilai"] == "-177000.00"                             # Σ tampil: 201.000 − 378.000
    sql = k.sql[0][0]
    assert "is_effective_journal(je.id)" in sql and "CASE WHEN cpj.net_cash > 0 THEN 1 ELSE -1 END AS arah" in sql
    assert "GROUP BY flow_category, account_type, arah," in sql
    assert "je.tenant_id = $1" in sql
