"""Terbilang satu sumber (P3 SO-dokumen). Literal dari spek 03-DOKUMEN (0, 1.000, 1.500.000, 21.000.000, 1.000.000.000)."""
import ast
from pathlib import Path

import pytest

from app.utils.terbilang import terbilang

APP = Path(__file__).resolve().parents[2] / "app"


@pytest.mark.parametrize("n,kata", [
    (0, "Nol Rupiah"),
    (1_000, "Seribu Rupiah"),
    (1_500_000, "Satu Juta Lima Ratus Ribu Rupiah"),
    (21_000_000, "Dua Puluh Satu Juta Rupiah"),
    (1_000_000_000, "Satu Miliar Rupiah"),
    (11, "Sebelas Rupiah"),
    (111, "Seratus Sebelas Rupiah"),
    (1_100, "Seribu Seratus Rupiah"),
    (2_019, "Dua Ribu Sembilan Belas Rupiah"),
    (93_000, "Sembilan Puluh Tiga Ribu Rupiah"),
    (310_000, "Tiga Ratus Sepuluh Ribu Rupiah"),
    (1_000_000_000_000, "Satu Triliun Rupiah"),
    (-5_000, "Minus Lima Ribu Rupiah"),
])
def test_literal(n, kata):
    assert terbilang(n) == kata


def test_desimal_dibulatkan_ke_bawah_seperti_dulu():
    assert terbilang(10000.75) == "Sepuluh Ribu Rupiah"


@pytest.mark.parametrize("berkas", ["routers/receive_payments.py", "routers/customer_deposits.py"])
def test_tak_ada_salinan_privat_lagi(berkas):
    pohon = ast.parse((APP / berkas).read_text(encoding="utf-8"))
    assert not [n for n in ast.walk(pohon) if isinstance(n, ast.FunctionDef) and n.name == "_terbilang"]
