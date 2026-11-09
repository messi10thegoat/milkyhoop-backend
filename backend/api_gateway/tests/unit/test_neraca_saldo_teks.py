"""Gelombang 4 L3 (10 Nov 2026; kontrak KONTRAK-laporan-teks-server-rupiah-20261110.md §8): Neraca Saldo dari jurnal POSTED <= as_of
(dulu fungsi DB menjumlah baris VOID/DRAFT/masa depan), akun nonaktif bersaldo ikut, as_of DAN as_of_date, bawaan tanggal bisnis tenant;
teks tampil server + SATU peta tipe/saldo normal (Neraca Saldo, /full, Buku Besar). Paritas nyata = scripts/ops/uji-salinan-neraca-saldo.sh."""
import asyncio
import inspect
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.routers import reports as RP
from app.schemas import ledger as SL
from app.services import neraca_saldo as NS
from app.services.report_engine import baris as B


def run(c):
    return asyncio.run(c)


class Conn:
    def __init__(self, rows):
        self.rows, self.args = rows, None

    async def fetch(self, sql, *a):
        self.args = a
        return self.rows


def r(kode, tipe, normal, d, k):
    return {"account_id": uuid.uuid4(), "account_code": kode, "account_name": f"Akun {kode}", "account_type": tipe,
            "normal_balance": normal, "total_debit": Decimal(d), "total_credit": Decimal(k)}


@pytest.mark.parametrize("d,k,harap", [("100", "40", ("60", "0")), ("40", "100", ("0", "60")), ("5", "5", ("0", "0"))])
def test_kolom_saldo_menurut_tanda_neto(d, k, harap):
    assert tuple(str(x) for x in NS.kolom_saldo(Decimal(d), Decimal(k))) == tuple(str(Decimal(h)) for h in harap)


def test_neraca_saldo_seimbang_dan_teks_server():
    c = Conn([r("1-10100", "ASSET", "DEBIT", "1500000.50", "250000"), r("1-10900", "ASSET", "CREDIT", "0", "50000"),
              r("3-10000", "EQUITY", "CREDIT", "0", "1200000.50")])
    h = run(NS.neraca_saldo(c, "t", date(2026, 11, 10), 0))
    assert c.args == ("t", date(2026, 11, 10))
    assert (h["judul"], h["per_text"], h["per_tanggal"]) == ("NERACA SALDO", "Per Tanggal 10 November 2026", "2026-11-10")
    assert (h["total_debit_saldo"], h["total_kredit_saldo"], h["selisih"], h["status_text"]) == \
        ("1250000.50", "1250000.50", "0.00", "Seimbang")
    assert h["total_debit_tampil"] == "1.250.001" and h["is_balanced"] is True
    kas, akum, modal = h["accounts"]
    assert (kas["debit_saldo"], kas["kredit_saldo"], kas["debit_tampil"], kas["kredit_tampil"]) == ("1250000.50", "0.00", "1.250.001", "")
    assert (akum["debit_saldo"], akum["kredit_saldo"], akum["balance"]) == ("0.00", "50000.00", 50000.0)  # kontra ASSET/CREDIT
    assert modal["balance"] == 1200000.5 and kas["balance"] == 1250000.5  # medan lama: saldo menurut saldo normal CoA


def test_tidak_seimbang_menyebut_selisih():
    h = run(NS.neraca_saldo(Conn([r("1", "ASSET", "DEBIT", "1000", "0")]), "t", date(2026, 11, 10), 0))
    assert h["status_text"] == "Tidak seimbang — selisih 1.000" and h["selisih"] == "1000.00" and h["is_balanced"] is False


def test_kueri_hanya_posted_sampai_tanggal_dan_akun_nonaktif_ikut():
    q = " ".join(NS.KUERI.split())
    assert "je.status = 'POSTED'" in q and "je.journal_date <= $2" in q and "je.tenant_id = $1" in q and "coa.tenant_id = $1" in q
    assert "LEFT JOIN" not in q  # dulu LEFT JOIN baris tanpa syarat -> VOID/DRAFT/masa depan ikut
    assert "is_active" not in q  # BL-07: akun nonaktif bersaldo ikut (baris nol disaring HAVING)
    assert "get_trial_balance(" not in q and "is_effective_journal" not in q  # POSTED saja, sama dengan /full dan /api/ledger


def test_rute_hitung_sendiri_as_of_alias_dan_tanggal_bisnis():
    src = inspect.getsource(RP.get_trial_balance)
    kode = src.split('"""', 2)[2]  # kode sesudah docstring (docstring menyebut fungsi DB yang dibuang)
    assert "get_facade(" not in kode and "get_trial_balance(" not in kode and "NS.neraca_saldo(" in kode and "conn.transaction(readonly=True)" in kode
    assert "as_of_date: Optional[date]" in src and "as_of or as_of_date or await tanggal_dokumen(conn, tenant_id)" in kode
    full = inspect.getsource(RP.get_trial_balance_full)  # 500 tanpa str(e) di BALASAN (log boleh)
    assert "report: {str(e)}" not in full and 'detail="Failed to generate trial balance report"' in full


def test_satu_peta_tipe_dan_saldo_normal():
    assert [B.tipe_label(t) for t in ("LIABILITY", "liability", "COGS", "revenue", "XYZ")] == \
        ["Liabilitas", "Liabilitas", "Harga Pokok Penjualan", "Pendapatan", "XYZ"]
    assert (B.saldo_normal_label("CREDIT"), B.saldo_normal_label("debit"), B.saldo_normal_label(None)) == ("Kredit", "Debit", "")


def test_skema_membawa_label_tipe_dan_saldo_normal_dari_coa():
    info = SL.AccountInfoResponse(id="a", code="1-10900", name="Akumulasi", account_type="ASSET", normal_balance="CREDIT").model_dump()
    assert (info["tipe_label"], info["saldo_normal_label"]) == ("Aset", "Kredit")  # dari CoA, bukan dari tipe
    ring = SL.LedgerAccountSummary(id="a", code="2", name="U", account_type="LIABILITY", normal_balance="CREDIT", debit_balance=0,
                                   credit_balance=0, net_balance=0, transaction_count=0).model_dump()
    assert (ring["tipe_label"], ring["saldo_normal_label"]) == ("Liabilitas", "Kredit")
    full = RP.TrialBalanceAccountEnhanced(id="a", account_code="1", account_name="K", account_type="asset", category="asset",
                                          normal_balance="DEBIT").model_dump()
    assert (full["tipe_label"], full["saldo_normal_label"]) == ("Aset", "Debit")
    row = RP.TrialBalanceAccountRow(account_id="a", account_code="1", account_name="K", account_type="EXPENSE", normal_balance="DEBIT",
                                    total_debit=1, total_credit=0, balance=1).model_dump()
    assert (row["tipe_label"], row["saldo_normal_label"]) == ("Beban", "Debit")
