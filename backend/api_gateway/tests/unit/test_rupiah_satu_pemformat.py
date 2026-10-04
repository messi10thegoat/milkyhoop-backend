"""Satu pemformat Rupiah untuk teks Penjualan (MASTER 5 Okt 2026): services/teks_galat.rp -- Decimal, tanpa float,
"Rp 1.250.000" / sen hanya bila != 0. Dulu 9 cara berbeda: "Rp1.250,50" (tanpa spasi, float), "1.250,50" (tanpa Rp),
"Rp 1.251" (DIBULATKAN: pihak_helpers.rupiah, so_ubah_terkonfirmasi) -> "melebihi Rp 100.000" padahal 100.000,40.
PDF TIDAK lewat sini (pdf_service punya filter Jinja sendiri). Di luar lingkup (dicatat): mesin chat, anomali, kasbon."""
import re
from decimal import Decimal
from pathlib import Path

import pytest

from app.routers import sales_orders as SO
from app.services import pihak_helpers as PH, so_pelunasan as PL, so_riwayat as SR, so_ubah_terkonfirmasi as UT
from app.services import teks_galat as tg

APP = Path(tg.__file__).resolve().parents[1]
PEMFORMAT = {"pihak_helpers.rupiah": PH.rupiah, "so_pelunasan._rp": PL._rp, "so_riwayat._rp": SR._rp,
             "so_ubah_terkonfirmasi._rp": UT._rp, "sales_orders._rp_teks": SO._rp_teks}


@pytest.mark.parametrize("nama", sorted(PEMFORMAT))
@pytest.mark.parametrize("x", [Decimal("250000"), Decimal("100000.40"), Decimal("1250.50"), 0, Decimal("-5000")])
def test_keluaran_sama_dengan_tg_rp(nama, x):
    assert PEMFORMAT[nama](x) == tg.rp(x)


def test_riwayat_tetap_aman_untuk_masukan_tak_sah():
    assert SR._rp("bukan angka") == "Rp\u2014"


BERKAS = ["routers/sales_orders.py", "routers/sales_invoices.py", "routers/quotes.py", "routers/proformas.py",
          "routers/receive_payments.py", "routers/customer_deposits.py", "routers/credit_notes.py",
          "routers/deliveries.py", "services/so_pelunasan.py", "services/so_riwayat.py",
          "services/so_ubah_terkonfirmasi.py", "services/pihak_helpers.py", "services/proforma_atribusi.py"]
IDIOM = re.compile(r':,\.[02]f\}"?\.(replace|translate)')


@pytest.mark.parametrize("rel", BERKAS)
def test_tak_ada_idiom_rupiah_float_lokal(rel):
    temuan = [n for n, l in enumerate((APP / rel).read_text().splitlines(), 1) if IDIOM.search(l)]
    assert temuan == [], f"{rel}: baris {temuan} memformat Rupiah sendiri -- pakai tg.rp"


def test_pemindai_bisa_merah():
    assert IDIOM.search('"Rp" + f"{float(x):,.2f}".replace(",", "#")')
    assert IDIOM.search('f"{x:,.0f}".replace(",", ".")')
    assert not IDIOM.search("tg.rp(x)")
