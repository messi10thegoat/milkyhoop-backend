"""ND-SESUDAH (1 Nov 2026, tiket RENDAH, GO MASTER): pratinjau aksi Nota Debit -- after DIHITUNG ULANG, bukan disalin dari before.
Dulu _sesudah = {**before, **ubah}: status_text basi di 5 aksi (posting tetap "Draf") dan status basi di terapkan/lepas/refund (trigger DB
update_vendor_credit_status mengubahnya). Kini: status (picu) = NDD.status_picu (kembar trigger), sisa_keadaan + status_text =
NDD.teks_keadaan (penentu SAMA dengan medan_baris daftar/detail). Diblok = after == before (== NK). Penjaga UTAMA = uji nyata kaos
(after == _keadaan_nd sesudah aksi asli, SEMUA kunci); berkas ini = spek bentuk + pemindai."""
import re
from decimal import Decimal
from pathlib import Path

import pytest

from app.routers import vendor_credits as RVC
from app.services import nota_debit_daftar as NDD
from app.services import nota_debit_rencana as NDR

APP = Path(__file__).resolve().parents[2] / "app"
R = (APP / "routers/vendor_credits.py").read_text()


@pytest.mark.parametrize("status,total,dipakai,kembali,harap", [
    ("draft", 1000, 1000, 0, "draft"), ("void", 1000, 0, 1000, "void"),
    ("posted", 1000, 1000, 0, "applied"), ("posted", 1000, 0, 1000, "applied"), ("partial", 1000, 400, 600, "applied"),  # Σ == total persis
    ("posted", 1000, 1200, 0, "applied"), ("posted", 1000, 0, 250, "partial"), ("applied", 1000, 0, 999, "partial"),
    ("applied", 1000, 0, 0, "posted"), ("partial", Decimal("1000.00"), 0.0, Decimal("0"), "posted"), ("posted", "1000", "0.01", None, "partial"),
])
def test_status_picu_kembar_trigger(status, total, dipakai, kembali, harap):
    assert NDD.status_picu(status, total, dipakai, kembali) == harap


def test_status_picu_docstring_kembar_trigger():
    assert "kembar trg update_vendor_credit_status" in NDD.status_picu.__doc__ and "ubah keduanya bersama" in NDD.status_picu.__doc__


@pytest.mark.parametrize("status", ["draft", "posted", "partial", "applied", "void"])
@pytest.mark.parametrize("dipakai,kembali", [(0, 0), (0, 500), (500, 0), (500, 500), (0, 1000)])
@pytest.mark.parametrize("bill", [None, "6b3c3b1e-0000-4000-8000-000000000001"])
def test_teks_keadaan_sama_dengan_medan_baris(status, dipakai, kembali, bill):
    v = {"id": 1, "status": status, "total_amount": 1000, "amount_applied": dipakai, "amount_received": kembali, "original_bill_id": bill,
         "original_bill_number": None}
    m = NDD.medan_baris(v, 0, None, [], None)
    t = NDD.teks_keadaan({"status": status, "amount_applied": dipakai, "amount_refunded": kembali, "original_bill_id": bill})
    assert t == {"sisa_keadaan": m["sisa_keadaan"], "status_text": m["status_text"]}


def _sebelum(**k):
    b = {"exists": True, "status": "draft", "sisa_keadaan": "draf", "status_text": "Draf", "remaining": 0.0, "terpakai": 0.0, "dikembalikan": 0.0,
         "original_bill_id": None, "original_bill_number": None, "total_amount": 1000.0, "amount_applied": 0.0, "amount_refunded": 0.0}
    b.update(k)
    return b


def _r(blok=False):
    bl = NDR.Blok(True)
    if blok:
        bl.galat("X", 400, "diblok")
    return {"blocks": bl}


def test_posting_teks_dihitung_ulang_bukan_disalin():
    a = RVC._sesudah(_sebelum(), _r(), status="posted", remaining=1000.0)
    assert (a["status"], a["sisa_keadaan"], a["status_text"]) == ("posted", "utuh", "Terbit")
    a = RVC._sesudah(_sebelum(original_bill_id="b"), _r(), status="posted", remaining=0.0, terpakai=1000.0)
    assert (a["sisa_keadaan"], a["status_text"]) == ("terpakai", "Terpakai")


def test_void_teks_batal():
    a = RVC._sesudah(_sebelum(status="posted", sisa_keadaan="utuh", status_text="Terbit"), _r(), status="void", remaining=0.0)
    assert (a["status"], a["sisa_keadaan"], a["status_text"]) == ("void", "batal", "Batal")
    # pemanggil void: jurnal posting dibalik -> terpakai 0 (diukur nyata D2: ND bertaut FPB terpakai 1000 -> 0)
    assert 'status="void", remaining=0.0, terpakai=0.0' in R


def test_picu_terapkan_lepas_refund():
    terbit = _sebelum(status="posted", sisa_keadaan="utuh", status_text="Terbit", remaining=1000.0)
    a = RVC._sesudah(terbit, _r(), picu=True, amount_applied=1000.0, original_bill_id="b")
    assert (a["status"], a["sisa_keadaan"], a["status_text"]) == ("applied", "terpakai", "Terpakai")
    a = RVC._sesudah({**terbit, "status": "applied", "amount_applied": 1000.0, "original_bill_id": "b"}, _r(), picu=True,
                     amount_applied=0.0, original_bill_id=None)
    assert (a["status"], a["sisa_keadaan"], a["status_text"]) == ("posted", "utuh", "Terbit")
    a = RVC._sesudah(terbit, _r(), picu=True, amount_refunded=250.0)
    assert (a["status"], a["sisa_keadaan"], a["status_text"]) == ("partial", "sebagian_dikembalikan", "Sebagian dikembalikan")
    a = RVC._sesudah({**terbit, "status": "partial", "amount_refunded": 250.0}, _r(), picu=True, amount_refunded=1000.0)
    assert (a["status"], a["sisa_keadaan"], a["status_text"]) == ("applied", "dikembalikan", "Dikembalikan")


def test_tanpa_picu_status_tak_dihitung_trigger():
    # posting ND bertaut tak menyisipkan aplikasi -> trigger tak jalan -> status 'posted' (bukan 'applied')
    a = RVC._sesudah(_sebelum(original_bill_id="b"), _r(), status="posted", terpakai=1000.0, amount_applied=0.0)
    assert a["status"] == "posted"


def test_diblok_atau_tak_ada_after_sama_dengan_before():
    s = _sebelum(status="posted", sisa_keadaan="utuh", status_text="Terbit")
    assert RVC._sesudah(s, _r(blok=True), picu=True, amount_applied=1000.0) == s
    assert RVC._sesudah({"exists": False}, _r(), status="void") == {"exists": False}


def _blok_pratinjau():
    return R[R.index("def _sesudah("):R.index("@router.post(\"/{vendor_credit_id}/create-tax-invoice\")")]


def test_pemindai_pemanggil_tanpa_literal_turunan():
    src = _blok_pratinjau()
    panggil = re.findall(r"_sesudah\(sebelum, r\b[^\n]*(?:\n\s{20,}[^\n]*)*", src)
    assert len(panggil) == 5, panggil  # post, apply, unapply, refund, void (definisi tak cocok pola)
    for p in panggil:
        assert "sisa_keadaan=" not in p and "status_text=" not in p and "status_detail=" not in p, p
    assert sum("picu=True" in p for p in panggil) == 3
    assert "return {**sebelum, **ubah}" not in src and "{**sebelum, **ubah} if" not in src


def test_pemindai_after_lewat_penentu_bersama():
    d = R[R.index("def _sesudah("):R.index("async def _pratinjau_nd(")]
    assert "NDD.status_picu(" in d and "NDD.teks_keadaan(" in d and ".daftar" in d
    tk = Path(APP / "services/nota_debit_daftar.py").read_text()
    t = tk[tk.index("def teks_keadaan("):tk.index("def sisa_nd_baris(")]
    assert "status_detail_nd(" in t and "keadaan_nd(" in t and "status_text_nd(" in t
