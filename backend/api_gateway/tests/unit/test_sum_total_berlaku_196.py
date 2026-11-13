"""BUG-196: SATU penentu "berlaku untuk Σ" (services/sum_total_berlaku) untuk sum_total 13 daftar seragam. Putusan pemilik 13 Nov 2026:
batal/void/ditolak/kedaluwarsa TIDAK ikut; DRAF tetap ikut di semua modul. Paritas nyata sebelum/sesudah = uji salinan."""
import asyncio
import pathlib
from decimal import Decimal

import pytest

import app.services.so_posisi as SOP
from app.services import sum_total_berlaku as SG

APP = pathlib.Path(SG.__file__).resolve().parents[1]
SEMUA = list(SG.TIDAK_BERLAKU)
DRAF = {m: "draft" for m in SEMUA}


def test_tiga_belas_modul_tercakup():
    assert sorted(SEMUA) == sorted(["expense", "sales_invoice", "sales_order", "quote", "bill", "purchase_order", "bill_payment", "receive_payment",
                                    "vendor_credit", "credit_note", "customer_deposit", "vendor_deposit", "proforma"])


@pytest.mark.parametrize("modul", SEMUA)
def test_draf_tetap_ikut_dan_batal_keluar(modul):
    assert SG.berlaku(modul, "draft") is True                                   # draf TETAP ikut (putusan b)
    for st in SG.TIDAK_BERLAKU[modul]:
        assert SG.berlaku(modul, st) is False                                   # batal/void/ditolak/kedaluwarsa keluar (putusan a)
    assert "draft" not in SG.TIDAK_BERLAKU[modul]


@pytest.mark.parametrize("modul,keluar", [("sales_invoice", {"void"}), ("sales_order", {"cancelled"}), ("quote", {"void", "declined", "expired"}), ("proforma", {"cancelled", "expired"}),
                                          ("expense", {"void"}), ("bill", {"void"}), ("purchase_order", {"cancelled"}), ("bill_payment", {"voided"}), ("receive_payment", {"voided"}),
                                          ("vendor_credit", {"void"}), ("credit_note", {"void"}), ("customer_deposit", {"void"}), ("vendor_deposit", {"void"})])
def test_tabel_status_persis(modul, keluar):
    assert set(SG.TIDAK_BERLAKU[modul]) == keluar


def test_status_aktif_ikut():
    for modul, st in (("sales_invoice", "posted"), ("sales_invoice", "paid"), ("sales_order", "confirmed"), ("quote", "sent"), ("quote", "accepted"), ("quote", "converted"),
                      ("purchase_order", "closed"), ("proforma", "issued"), ("bill", "posted"), ("expense", "posted")):
        assert SG.berlaku(modul, st) is True


def test_sql_berlaku_bentuk_dan_penawaran_kedaluwarsa_turunan():
    assert SG.sql_berlaku("sales_invoice", "si.status") == "((si.status) NOT IN ('void'))"
    q = SG.sql_berlaku("quote", "status", "$7")
    assert "NOT IN ('void', 'declined', 'expired')" in q and "status IN ('sent', 'viewed')" in q and "expiry_date < $7::date" in q       # penentu kedaluwarsa SAMA dgn ?status=expired
    with pytest.raises(ValueError):
        SG.sql_berlaku("quote", "status")                                       # kedaluwarsa turunan tanggal: p_hari wajib


def test_so_jumlah_daftar_tanpa_batal_draf_ikut(monkeypatch):
    async def sisa(conn, tid, rows):
        return {}
    monkeypatch.setattr(SOP, "sisa_per_so", sisa)
    rows = [{"id": 1, "total_amount": Decimal("100"), "status": "draft"}, {"id": 2, "total_amount": Decimal("200"), "status": "confirmed"},
            {"id": 3, "total_amount": Decimal("400"), "status": "cancelled"}]
    h = asyncio.run(SOP.jumlah_daftar(None, "t", rows))
    assert h["sum_total"] == 300.0                                              # 100 draf + 200 aktif; 400 batal keluar


# Tiap daftar MEMAKAI penentu (bukan literal FILTER tersebar): jangkar sumber (perilaku nyata diuji di salinan)
KABEL = [("routers/expenses.py", "expense"), ("routers/sales_invoices.py", "sales_invoice"), ("routers/quotes.py", "quote"), ("services/bills_service.py", "bill"),
         ("routers/purchase_orders.py", "purchase_order"), ("routers/bill_payments.py", "bill_payment"), ("routers/receive_payments.py", "receive_payment"),
         ("routers/vendor_credits.py", "vendor_credit"), ("routers/credit_notes.py", "credit_note"), ("routers/customer_deposits.py", "customer_deposit"),
         ("routers/vendor_deposits.py", "vendor_deposit"), ("routers/proformas.py", "proforma")]


@pytest.mark.parametrize("berkas,modul", KABEL)
def test_daftar_memakai_penentu_bersama(berkas, modul):
    s = (APP / berkas).read_text()
    assert f"SG.sql_berlaku('{modul}'" in s or f'SG.berlaku("{modul}"' in s


def test_so_memakai_berlaku_dan_penawaran_meneruskan_hari():
    assert 'berlaku("sales_order"' in (APP / "services/so_posisi.py").read_text()
    q = (APP / "routers/quotes.py").read_text()
    assert "SG.sql_berlaku('quote', 'status'" in q and "*params, hari_ini" in q
