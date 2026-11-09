"""Gelombang 4 L1 (10 Nov 2026; kontrak KONTRAK-laporan-teks-server-rupiah-20261110.md): laporan PSAK (Laba Rugi, Neraca, Arus Kas)
membawa baris tampil SERVER (SATU peta label report_engine.baris) + nilai desimal mentah + tanda; angka tampil lewat SATU pemformat
teks_galat.angka_laporan (gaya diputus belakangan). Hitungan engine Decimal penuh (Law 9/25); medan lama float tetap. Rute: pool + RO,
tanpa SET LOCAL f-string, tanpa str(e). Paritas nyata (Σ baris == jurnal per akun) = scripts/ops/uji-salinan-laporan-psak.sh."""
import asyncio
import inspect
import uuid
from decimal import Decimal

import pytest

from app.routers import psak_reports as PR
from app.services import teks_galat as tg
from app.services.report_engine import balance_sheet as BS
from app.services.report_engine import baris as B
from app.services.report_engine import cash_flow as CF
from app.services.report_engine import income_statement as IS
from app.services.report_engine import ledger as LG


def run(c):
    return asyncio.run(c)


# ---------------------------------------------------------------- SATU pemformat angka laporan
@pytest.mark.parametrize("x,presisi,gaya,harap", [
    (Decimal("1250000"), 0, "akuntansi", "1.250.000"), (Decimal("-5000"), 0, "akuntansi", "(5.000)"),
    (Decimal("1250000.50"), 0, "akuntansi", "1.250.001"), (Decimal("1250000.49"), 0, "akuntansi", "1.250.000"),
    (Decimal("1250000.50"), 2, "akuntansi", "1.250.000,50"), (Decimal("1250000.00"), 2, "akuntansi", "1.250.000"),
    (Decimal("-5000"), 0, "rp", "Rp -5.000"), (Decimal("0"), 0, "akuntansi", "0"), ("1250000.00", 0, "rp", "Rp 1.250.000"),
])
def test_angka_laporan(x, presisi, gaya, harap):
    assert tg.angka_laporan(x, presisi, gaya) == harap


def test_gaya_bawaan_satu_konstanta_dan_tanggal():
    assert tg.GAYA_ANGKA_LAPORAN == "akuntansi" and tg.angka_laporan(-1, 0) == "(1)"
    assert tg.tanggal_panjang("2026-10-31") == "31 Oktober 2026"
    assert B.periode_berakhir("2026-10-31") == "Untuk Periode yang Berakhir 31 Oktober 2026"
    assert B.per_tanggal("2026-12-01") == "Per Tanggal 1 Desember 2026"


# ---------------------------------------------------------------- engine (ledger dipalsukan)
AKUN = {}


def _det(tipe, *saldo):
    rows = []
    for i, x in enumerate(saldo):
        aid = uuid.uuid4()
        AKUN[aid] = tipe
        rows.append({"account_id": aid, "account_code": f"{tipe}-{i}", "account_name": f"{tipe} {i}", "balance": Decimal(x)})
    return rows


DETAIL = {"REVENUE": _det("REVENUE", "-1000.10", "-500.05"), "COGS": _det("COGS", "400.33"), "EXPENSE": _det("EXPENSE", "200.10", "99.99"),
          "OTHER_INCOME": _det("OTHER_INCOME", "-50.00"), "OTHER_EXPENSE": _det("OTHER_EXPENSE", "10.01")}


@pytest.fixture
def palsu(monkeypatch):
    async def b(conn, tid, fp, start_date=None, end_date=None):
        return sum((d["balance"] for d in DETAIL.get(fp.get("account_type"), [])), Decimal("0"))

    async def bd(conn, tid, fp, start_date=None, end_date=None):
        return DETAIL.get(fp.get("account_type"), [])
    monkeypatch.setattr(IS, "compute_balance", b)
    monkeypatch.setattr(IS, "compute_balance_detail", bd)


def test_laba_rugi_lama_tetap_dan_baris_server(palsu):
    r = run(IS.generate_income_statement(None, "t", "2026-01-01", "2026-10-31"))
    # medan lama float: rumus sama
    assert r["laba_kotor"] == float(Decimal("1500.15") - Decimal("400.33"))
    assert r["laba_bersih"] == float(Decimal("1500.15") - Decimal("400.33") - Decimal("300.09") + Decimal("50.00") - Decimal("10.01"))
    assert r["pendapatan"]["akun"][0] == {"account_code": "REVENUE-0", "account_name": "REVENUE 0", "balance": 1000.10}
    # tampilan server
    assert (r["judul"], r["periode_text"], r["basis_text"]) == ("LAPORAN LABA RUGI", "Untuk Periode yang Berakhir 31 Oktober 2026",
                                                                 "Basis Akrual")
    lb = {x["kunci"]: x for x in r["baris"] if x["jenis"] != "akun"}
    assert [x["label"] for x in r["baris"] if x["jenis"] == "judul"] == ["PENDAPATAN", "HARGA POKOK PENJUALAN", "BEBAN USAHA",
                                                                         "PENDAPATAN (BEBAN) LAIN-LAIN"]
    assert lb["total_pendapatan"]["nilai"] == "1500.15" and lb["laba_kotor"]["nilai"] == "1099.82"
    assert lb["total_lain"]["nilai"] == "39.99" and lb["laba_bersih"]["nilai"] == "839.72" and lb["laba_bersih"]["tanda"] == 1
    akun = [x for x in r["baris"] if x["jenis"] == "akun"]
    assert all(x["account_id"] for x in akun)  # drill-down: id akun ikut (dulu FE accountId '')
    beban_lain = next(x for x in akun if x["account_code"] == "OTHER_EXPENSE-0")
    assert (beban_lain["nilai"], beban_lain["tanda"]) == ("-10.01", -1)  # mengurangi laba (dulu FE menegasi sendiri)


def test_sigma_baris_akun_sama_total_seksi(palsu):
    r = run(IS.generate_income_statement(None, "t", "2026-01-01", "2026-10-31"))
    seksi, jumlah, total = None, {}, {}
    for x in r["baris"]:
        if x["jenis"] == "judul":
            seksi = x["kunci"]
        elif x["jenis"] == "akun":
            jumlah[seksi] = jumlah.get(seksi, Decimal("0")) + Decimal(x["nilai"])
        elif x["jenis"] == "total":
            total[seksi] = Decimal(x["nilai"])
    assert jumlah == total


def test_neraca_laba_periode_decimal_bukan_float(monkeypatch):
    src = inspect.getsource(BS.generate_balance_sheet)
    assert "hitung_laba_rugi(" in src and 'Decimal(str(pl["laba_bersih"]))' not in src
    assert 'utang_usaha_sec["total"] + current_other_sec["total"]' not in src  # dulu penjumlahan float
    assert "float(" not in src.split("return {")[0].replace('"balance": float(d["balance"])', "")  # float hanya di keluaran


def test_arus_kas_gabung_decimal_dan_label_bertanda():
    assert B.kas_neto_label("operasi", Decimal("5")) == "Kas neto yang diperoleh dari aktivitas operasi"
    assert B.kas_neto_label("investasi", Decimal("-5")) == "Kas neto yang digunakan untuk aktivitas investasi"
    src = inspect.getsource(CF.generate_cash_flow)
    assert '"amount": float(amount)' not in src and "keluar(operating_items)" in src


def test_detail_ledger_membawa_account_id():
    src = inspect.getsource(LG.compute_balance_detail)
    assert "coa.id AS account_id" in src and "GROUP BY coa.id, coa.account_code, coa.name" in src and '"account_id": r["account_id"]' in src


# ---------------------------------------------------------------- rute
def test_rute_pool_ro_tanpa_bocor_dan_tampil_satu_pemformat():
    src = inspect.getsource(PR).split('"""', 2)[2]  # kode sesudah docstring modul (docstring menyebut yang dibuang)
    assert "asyncpg.connect" not in src and "get_db_connection" not in src and "SET LOCAL" not in src
    assert "detail=str(e)" not in src and src.count("conn.transaction(readonly=True)") == 2
    for n, v in (("balance_sheet", "bs"), ("income_statement", "pl"), ("cash_flow", "cf")):
        assert f'"{n}": _tampil({v}, presisi)' in src  # tiap laporan lewat SATU pemformat
    lap = {"baris": [{"nilai": "-5000.00"}, {"nilai": None}]}
    PR._tampil(lap, 0)
    assert [b["nilai_tampil"] for b in lap["baris"]] == ["(5.000)", None]


def test_arus_kas_butir_gabungan_nol_disaring_dan_baris_bertanda(monkeypatch):
    """Gabung per label dalam Decimal: +5000 dan -5000 berlabel sama -> 0 -> tak tampil (dulu float); kas neto berlabel menurut tanda."""
    async def saldo(conn, tid, filt, end_date=None):
        return Decimal("100") if end_date < "2026-11-01" else Decimal("90")

    class K:
        async def fetch(self, sql, *a):
            return [{"flow_category": "OPERATING", "account_type": "EXPENSE", "psak_sub_category": None, "account_name": None,
                     "net_flow": Decimal("5000.10")},
                    {"flow_category": "OPERATING", "account_type": "EXPENSE", "psak_sub_category": None, "account_name": None,
                     "net_flow": Decimal("-5000.10")},
                    {"flow_category": "OPERATING", "account_type": "REVENUE", "psak_sub_category": None, "account_name": None,
                     "net_flow": Decimal("-10")}]

        async def fetchval(self, sql, *a):
            return Decimal("0")
    monkeypatch.setattr(CF, "compute_balance", saldo)
    r = run(CF.generate_cash_flow(K(), "t", "2026-11-01", "2026-11-30"))
    assert [i["label"] for i in r["operasi"]["items"]] == ["Penerimaan dari pelanggan"]  # beban +/- berlabel sama = 0 -> disaring
    butir = [b for b in r["baris"] if b["kunci"] == "butir"]
    assert [(b["label"], b["nilai"]) for b in butir] == [("Penerimaan dari pelanggan", "-10.00")]
    neto = next(b for b in r["baris"] if b["kunci"] == "kas_neto_operasi")
    assert neto["label"] == "Kas neto yang digunakan untuk aktivitas operasi" and neto["nilai"] == "-10.00"
