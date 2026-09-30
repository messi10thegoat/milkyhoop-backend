"""Nilai belum dikirim SO = NETO (line_total − PPN baris), 30 Sep 2026.

line_total baris SO = neto + PPN (BRUTO; diukur di baris ber-PPN kaos SO-2609-0335: 372.960 = 333.000 + 39.960).
Semua pembaca yang memberi nilai "belum dikirim" ke nilai_belum_dikirim WAJIB mengurangkan tax_amount dulu.
"""
import ast
import os
from decimal import Decimal as D
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

from app.services import so_kirim as K  # noqa: E402

APP = Path(K.__file__).resolve().parents[1]


def test_baris_ber_ppn_dihitung_dari_neto():
    # 3 pcs, line_total 372.960 (DPP 333.000 + PPN 39.960), terkirim 1 -> sisa 2 x 111.000 = 222.000
    assert K.nilai_belum_dikirim(D("3"), D("372960") - D("39960"), D("1")) == D("222000.00")


def test_sql_ringkasan_mengurangkan_ppn():
    src = (APP / "services" / "so_kirim.py").read_text(encoding="utf-8")
    sqls = [n.value for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and "soi.line_total" in n.value and "FROM sales_orders so" in n.value]
    assert len(sqls) == 2
    for s in sqls:
        assert "soi.line_total - COALESCE(soi.tax_amount, 0) AS line_total" in " ".join(s.split()), s


def test_detail_so_mengurangkan_ppn():
    src = (APP / "routers" / "sales_orders.py").read_text(encoding="utf-8")
    panggil = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)
               and getattr(n.func, "attr", None) == "nilai_belum_dikirim"]
    assert panggil, "detail SO tak memanggil nilai_belum_dikirim"
    for c in panggil:
        assert "tax_amount" in ast.unparse(c.args[1]), ast.unparse(c.args[1])
