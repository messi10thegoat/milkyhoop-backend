"""Laporan (6 Nov 2026, putusan pemilik langsung): Arus Kas (a) jurnal saldo awal = kas awal, bukan aktivitas; (b) lawan ekuitas = pendanaan;
(c) ASSET tanpa sub-kategori berlabel nama akun. Profitabilitas: qty dalam satuan dasar (nyawit Minyakita 23 -> 56 pcs; bukti data nyata
dibaca 6 Nov: kas awal 0 -> 282.000.000, Operasi +250.319.916 -> -31.680.084, saldo akhir sama)."""
import asyncio
import os
from decimal import Decimal as D

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

from app.services.report_engine import cash_flow as CF  # noqa: E402
from app.services import profitability_query as PQ  # noqa: E402


class _K:
    def __init__(self, rows, kas_saldo_awal):
        self.rows, self.kas, self.args = rows, kas_saldo_awal, []

    async def fetch(self, sql, *a):
        self.args.append((sql, a))
        return self.rows

    async def fetchval(self, sql, *a):
        self.args.append((sql, a))
        return self.kas


def _r(cat, tipe, n, sub=None, nama=None):
    return {"flow_category": cat, "account_type": tipe, "psak_sub_category": sub, "account_name": nama, "net_flow": D(n)}


def test_arus_kas_saldo_awal_jadi_kas_awal_ekuitas_pendanaan_label_aset(monkeypatch):
    async def saldo(conn, tid, filt, end_date=None):
        return D("0") if end_date < "2026-11-01" else D("250319916")
    monkeypatch.setattr(CF, "compute_balance", saldo)
    rows = [_r("NONE", "ASSET", "-5000000", nama="Uang Muka Pembelian"), _r("NONE", "EQUITY", "10000000"),
            _r("OPERATING", "PAYABLE", "-33993784"), _r("NONE", "RECEIVABLE", "7813700")]
    k = _K(rows, D("282000000"))
    r = asyncio.run(CF.generate_cash_flow(k, "t", "2026-11-01", "2026-11-30"))
    sql, a = k.args[0]
    assert "je.source_type <> ALL($4::text[])" in sql and a[3] == CF.SUMBER_SALDO_AWAL and "BANK_OPENING_BALANCE" in a[3]
    assert r["kas_awal"] == 282000000.0                                         # (a)
    assert [i["label"] for i in r["pendanaan"]["items"]] == ["Setoran modal / prive"] and r["pendanaan"]["total"] == 10000000.0  # (b)
    assert "Uang Muka Pembelian" in [i["label"] for i in r["operasi"]["items"]]  # (c)
    assert "Transaksi ekuitas" not in str(r) and "Penerimaan/pengeluaran aset" not in str(r)


def test_kategori_eksplisit_tetap_dihormati():
    assert CF._kategori("INVESTING", "EQUITY") == "INVESTING" and CF._kategori(None, "EXPENSE") == "OPERATING"
    assert CF._kategori("NONE", "EQUITY") == "FINANCING"


def test_profitabilitas_qty_satuan_dasar_di_kueri_barang_bukan_jasa():
    # bentuk dikunci: qty baris DIKALI faktor konversi satuan baris (perilaku terbukti pada data nyata nyawit 23 -> 56; runner tanpa DB)
    assert " ".join(PQ.QTY_DASAR.split()).startswith("sii.quantity * COALESCE(( SELECT uc.conversion_factor FROM unit_conversions uc")
    assert " ".join(PQ.QTY_DASAR.split()).endswith("LIMIT 1), 1)")
    for q in (PQ.ITEMS_QUERY, PQ.SUMMARY_QUERY):
        assert PQ.QTY_DASAR in q
        assert "__QTY_DASAR__" not in q and "SUM(sii.quantity)" not in q and "uc.conversion_factor" in q
    assert "SUM(sii.quantity) AS qty_sold" in PQ.SERVICES_QUERY  # jasa tanpa konversi
