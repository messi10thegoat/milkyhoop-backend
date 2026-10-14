"""JUMLAH-BARIS (V427, 14 Okt 2026; putusan MASTER Opsi A): jumlah baris yang diketik MENGGANTIKAN
q2(qty x harga) pada tingkat BRUTO bila |selisih| <= qty x 0,005; faktur parsial pro-rata, yang menuntaskan
baris menyerap sisa (sisa pembulatan 0 rupiah). Kasus pemilik: SO-2610-0014, 26 pcs, Rp6.150.000."""
from decimal import Decimal as D

import pytest

from app.services import sales_doc_calc as C

HARGA = D("236538.46")
JUMLAH = D("6150000")


def _baris(**kw):
    b = {"id": "L1", "quantity": D("26"), "unit_price": HARGA, "discount_percent": 0, "tax_rate": 0}
    b.update(kw)
    return b


# ---------------------------------------------------------------- override di kalkulator
def test_tanpa_jumlah_baris_perilaku_lama_persis():
    r = C.compute_document([_baris()])
    assert r["total_amount"] == D("6149999.96")  # qty x harga 2dp -- bukan 6.150.000


def test_jumlah_diketik_menggantikan_qty_kali_harga():
    r = C.compute_document([_baris(line_amount=JUMLAH)])
    assert r["items"][0]["subtotal"] == JUMLAH
    assert r["total_amount"] == JUMLAH and r["gross_subtotal"] == JUMLAH


def test_jumlah_diketik_diskon_baris_dan_ppn_dari_neto():
    r = C.compute_document([_baris(line_amount=JUMLAH, discount_percent=10, tax_rate=11)])
    ln = r["items"][0]
    assert ln["subtotal"] == JUMLAH
    assert ln["net"] == D("5535000.00")          # 6.150.000 - 10%
    assert ln["tax_amount"] == D("608850.00")     # 11% dari neto
    assert ln["total"] == D("6143850.00")


@pytest.mark.parametrize("jumlah,ok", [
    ("6150000", True),             # selisih 0,04
    ("6149999.87", True),          # tepat di batas 26 x 0,005 = 0,13 -> 6.149.999,96 - 0,09 (di dalam)
    ("6150000.09", True),          # 6.149.999,96 + 0,13 = 6.150.000,09 (batas)
    ("6150000.10", False),         # lewat batas
    ("6149988", False),            # harga bulat 236.538 x 26 -- bukan selisih pembulatan
    ("-1", False),
])
def test_toleransi_qty_kali_0_005(jumlah, ok):
    if ok:
        assert C.compute_document([_baris(line_amount=D(jumlah))])["items"][0]["subtotal"] == D(jumlah)
    else:
        with pytest.raises(C.LineAmountError):
            C.compute_document([_baris(line_amount=D(jumlah))])


def test_toleransi_dikendurkan_berarti_merah():
    """Kontrol merah: bila toleransi dilonggarkan (mis. 0,5/unit) jumlah yang semestinya DITOLAK lolos."""
    with pytest.raises(C.LineAmountError):
        C.compute_document([_baris(line_amount=D("6150000.50"))])


def test_jumlah_baris_db_dipercaya_tanpa_uji_toleransi():
    r = C.compute_document([_baris(jumlah_baris=D("6000000"))])  # kolom tersimpan (hasil pro-rata faktur)
    assert r["total_amount"] == D("6000000")


# ---------------------------------------------------------------- SO -> faktur parsial
def _so(**kw):
    b = {"id": "L1", "quantity": D("26"), "unit_price": HARGA, "discount_percent": 0, "tax_rate": 0,
         "jumlah_baris": JUMLAH, "quantity_invoiced": D("0"), "prior_gross": D("0")}
    b.update(kw)
    return b


def _tagih(qty_list):
    """Simulasi faktur berurutan; kembalikan jumlah baris tiap faktur. Meniru router: quantity_invoiced &
    prior_gross diperbarui dari faktur non-void sebelumnya."""
    done_q, done_g, out = D("0"), D("0"), []
    for q in qty_list:
        row = _so(quantity_invoiced=done_q, prior_gross=done_g)
        n = len(qty_list)
        last = done_q + D(q) >= row["quantity"]
        plan = C.plan_so_invoice([row], {"L1": D(q)}, 0, 0, is_last=last)
        g = plan["items"][0]["subtotal"]
        out.append(g)
        done_q += D(q)
        done_g += g
    return out


@pytest.mark.parametrize("pecah", [[26], [10, 16], [1, 1, 24], [24, 1, 1], [13, 13], [7, 7, 7, 5], [1] * 26])
def test_faktur_parsial_jumlah_sama_dengan_so_sisa_nol(pecah):
    jml = _tagih(pecah)
    assert sum(jml) == JUMLAH, (pecah, jml)          # Σ faktur == SO, sisa persis 0
    assert all(j == j.quantize(D("0.01")) for j in jml)


def test_parsial_10_16_nilai():
    a, b = _tagih([10, 16])
    assert a == D("2365384.62")                       # q2(6.150.000 x 10/26)
    assert b == JUMLAH - a


def test_tanpa_override_parsial_tetap_qty_kali_harga():
    row = {"id": "L1", "quantity": D("26"), "unit_price": HARGA, "discount_percent": 0, "tax_rate": 0,
           "quantity_invoiced": D("0")}
    p = C.plan_so_invoice([row], {"L1": D("10")}, 0, 0, is_last=False)
    assert p["items"][0]["subtotal"] == D("2365384.60")  # perilaku lama: 10 x 236.538,46
