"""U-SI-LAW (2 Nov 2026; putusan pemilik LANGSUNG "B1 Tolak", MASTER: PO simetris): diskon baris > nilai baris DITOLAK di semua pintu
(penjualan lewat sales_doc_calc.line_net -> 400; PO lewat po_hitung.hitung_baris -> 400 / blok pratinjau), penjaga RECOG Law 4
(pecah_pendapatan: Σ kredit == Σ alokasi), auto_post faktur cek periode SEBELUM tulis (kalimat == POST /post)."""
import ast
import inspect
import itertools
from decimal import Decimal as D

import pytest

from app.routers import purchase_orders as PO
from app.routers import sales_invoices as SI
from app.services import faktur_terbit_rencana as F
from app.services import po_hitung as PH
from app.services import po_rencana as PR
from app.services import sales_doc_calc as C
from app.services import teks_galat as tg
from app.services.galat_berkode import GalatBerkode


def test_satu_kalimat():
    assert tg.diskon_baris_melebihi(D("150000"), D("100000.5")) == "Diskon baris Rp 150.000 melebihi nilai baris Rp 100.000,50."


def test_penjualan_line_net_menolak_neto_negatif():
    with pytest.raises(C.LineDiscountError) as e:
        C.line_net({"quantity": 2, "unit_price": D("50000"), "discount_amount": D("100000.01")})
    assert isinstance(e.value, C.DocumentDiscountError)  # penangkap lama (400) ikut menangkap
    assert str(e.value) == "Diskon baris Rp 100.000,01 melebihi nilai baris Rp 100.000."
    assert C.line_net({"quantity": 2, "unit_price": D("50000"), "discount_amount": D("100000")})["net"] == 0   # sama = boleh (gratis)
    assert C.line_net({"quantity": 1, "unit_price": D("10"), "discount_percent": D("100")})["net"] == 0
    with pytest.raises(C.LineDiscountError):
        C.line_net({"quantity": 3, "unit_price": D("1"), "jumlah_baris": D("2.99"), "discount_amount": D("3")})  # V427 nilai tersimpan
    with pytest.raises(C.DocumentDiscountError):
        C.compute_document([{"quantity": 1, "unit_price": D("10"), "discount_amount": D("11"), "tax_rate": 0}])


def test_po_tolak_penulis_blok_pratinjau_kalimat_sama():
    it = {"quantity": 1, "unit_price": D("1000"), "discount_amount": D("1500"), "tax_rate": 0}
    with pytest.raises(GalatBerkode) as e:
        PH.hitung_baris(it)
    assert (e.value.status_code, e.value.code, e.value.field) == (400, "DISKON_BARIS_MELEBIHI", "discount_amount")
    b = PH.blok_diskon_baris([it, {"quantity": 1, "unit_price": 5, "discount_amount": 1}])
    assert b == [{"code": "DISKON_BARIS_MELEBIHI", "message": e.value.detail, "status": 400}]
    assert PH.hitung_baris(it, tolak=False)["discount_amount"] == D("1000.00")  # pratinjau: angka tampil dipotong, blok menolak
    assert PH.hitung_baris({"quantity": 1, "unit_price": 1000, "discount_amount": 1000})["total"] == 0
    assert "PH.blok_diskon_baris(" in inspect.getsource(PO.calculate_purchase_order)
    assert "bloks.extend(PH.blok_diskon_baris(" in inspect.getsource(PR.rencana_ubah)
    assert not hasattr(PO, "calculate_item_totals")  # kode mati dihapus: SATU aturan baris PO


def test_penjaga_recog_sigma_kredit_sama_sigma_alokasi():
    assert F.pecah_pendapatan([(D("100"), "service"), (D("-5"), "service"), (D("50"), "goods")]) == (D("50"), D("95"))
    assert F.pecah_pendapatan([(D("30"), "goods"), (D("-5"), "service")]) == (D("25"), D("0"))
    assert F.pecah_pendapatan([(D("-5"), "goods"), (D("30"), "service")]) == (D("0"), D("25"))
    assert F.pecah_pendapatan([(D("10"), "goods"), (D("20"), "service")]) == (D("10"), D("20"))  # semua positif: identik
    nilai = [D("-7"), D("-0.01"), D("0"), D("3"), D("40")]
    for kombinasi in itertools.product(nilai, repeat=3):
        for jenis in itertools.product(("service", "goods"), repeat=3):
            pas = list(zip(kombinasi, jenis))
            total = sum(kombinasi, D("0"))
            if total <= 0:
                continue
            b, j = F.pecah_pendapatan(pas)
            assert b + j == total and b >= 0 and j >= 0, (pas, b, j)   # Law 4: kredit RECOG == debit (Σ alokasi)


def test_penulis_dan_pratinjau_tak_membuang_alokasi_negatif():
    s = inspect.getsource(SI._internal_post_invoice)
    assert "if _a == 0:  # U-SI-LAW" in s and "if _a <= 0:" not in s
    assert 'if _d(it["_allocated"]) != 0]' in inspect.getsource(F.rencana_terbit_ro)


def test_auto_post_cek_periode_sebelum_tulis():
    s = inspect.getsource(SI.create_invoice)
    i = s.index("if body.auto_post:\n                    await check_period_is_open(conn, ctx[\"tenant_id\"], body.invoice_date)")
    assert i < s.index("INSERT INTO sales_invoices") and i < s.index("_internal_post_invoice(")
