"""Default bisa-ditimpa tahap 1 (30 Sep 2026): aturan uang muka SO (hitung_dp) + penentu default_pesanan.

Nominal diketik = 'manual' (terkunci); persen saja = nominal dihitung SERVER ROUND_HALF_UP ke rupiah ('percent',
ikut total). create, PATCH, dan /calculate memakai hitung_dp yang SAMA. Uji nyata salinan DB: D1-D9 + SO lama.
"""
import ast
import os
from decimal import Decimal as D
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.services import default_dokumen as DD  # noqa: E402
from app.routers import sales_orders as SO  # noqa: E402


def test_hitung_dp_aturan():
    assert DD.hitung_dp(D("999999"), 60, None) == {"dp_percent": D("60"), "dp_amount": D("599999"), "dp_amount_source": "percent"}
    assert DD.hitung_dp(D("1000"), D("33.33"), None)["dp_amount"] == D("333")        # HALF_UP ke rupiah
    assert DD.hitung_dp(D("1001"), 50, None)["dp_amount"] == D("501")                # 500,5 -> 501
    assert DD.hitung_dp(D("1000"), 60, 250) == {"dp_percent": D("60"), "dp_amount": D("250"), "dp_amount_source": "manual"}
    assert DD.hitung_dp(D("1000"), None, 250)["dp_amount_source"] == "manual"
    assert DD.hitung_dp(D("1000"), None, None) == {"dp_percent": None, "dp_amount": None, "dp_amount_source": None}
    assert DD.hitung_dp(D("1234567"), 100, None)["dp_amount"] == D("1234567")         # DP 100% = total persis


class _C:
    def __init__(self, pct=None, rek=None):
        self.pct, self.rek, self.q = pct, rek, []

    async def fetchval(self, sql, *a):
        self.q.append((sql, a))
        assert "FROM accounting_settings WHERE tenant_id = $1" in sql
        return self.pct

    async def fetchrow(self, sql, *a):
        self.q.append((sql, a))
        assert "is_default AND is_active" in sql and "tenant_id = $1" in sql  # bendera eksplisit, bukan urutan daftar
        return self.rek


@pytest.mark.asyncio
async def test_penentu_default_perusahaan():
    c = _C(pct=D("50"), rek={"id": 1, "account_name": "BCA Operasional", "bank_name": "BCA", "account_number": "111",
                             "account_holder_name": "PT Kaos"})
    d = await DD.default_pesanan(c, "kaos", None)
    assert d == {"dp_percent": {"value": 50.0, "source": "company"},
                 "receiving_account": {"id": "1", "account_name": "BCA Operasional", "bank_name": "BCA",
                                       "account_number": "111", "account_holder": "PT Kaos", "source": "company_main"}}
    assert all(a[0] == "kaos" for _, a in c.q)


@pytest.mark.asyncio
async def test_penentu_tanpa_setelan_null():
    assert await DD.default_pesanan(_C(), "grapgrap", "cust") == {"dp_percent": None, "receiving_account": None}


def _src():
    return Path(SO.__file__).read_text(encoding="utf-8")


def _fn(nama):
    return next(n for n in ast.walk(ast.parse(_src())) if isinstance(n, ast.AsyncFunctionDef) and n.name == nama)


@pytest.mark.parametrize("nama", ["create_sales_order", "update_sales_order", "calculate_sales_order"])
def test_satu_kalkulator_dp(nama):
    assert any(isinstance(c, ast.Call) and getattr(c.func, "id", None) == "hitung_dp" for c in ast.walk(_fn(nama))), nama


def test_patch_tak_menulis_dp_mentah():
    s = " ".join(ast.unparse(_fn("update_sales_order")).split())
    assert "if field in ('dp_percent', 'dp_amount'): continue" in s
