"""#10b-2 — tanggal bisnis tenant di payroll + persediaan (lanjutan #10a/#10b-1).

Void gaji (payroll, payroll_runs), void pembayaran gaji, void kasbon, void
penyesuaian stok, penyesuaian cepat dari Barang (jurnal + ledger), saldo awal
produk baru (ledger), batal transfer stok (rute diparkir 409, tetap dibetulkan),
nomor PR-YYYYMM cadangan. Gaji grapgrap = alasan unit ini sebelum 30 Sep.

Jam DISUNTIK ke tanggal_tenant: 2026-09-30 18:00Z (-> 1 Okt WIB), kontrol 10:00Z.
"""

import ast
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.utils import tanggal_tenant as tt

APP = Path(tt.__file__).resolve().parents[1]
TENANT = "grapgrap-manado"
KASUS = [(datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc), date(2026, 10, 1)),
         (datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc), date(2026, 9, 30))]

FUNGSI = {
    "routers/payroll.py": ["get_next_payroll_number", "void_payroll"],
    "routers/payroll_runs.py": ["void_payroll"],
    "routers/payroll_payments.py": ["void_payment"],
    "routers/employee_advances.py": ["void_advance"],
    "routers/stock_adjustments.py": ["void_stock_adjustment"],
    "routers/items.py": ["create_stock_adjustment"],
    "routers/inventory.py": ["add_product"],
    "routers/stock_transfers.py": ["cancel_stock_transfer"],
}
# Alias impor `date` yang dipakai di kode (items.py: date_type / dateclass) — pemindai
# pertama melewatkan date_type.today() karena hanya mengenal `date`.
ALIAS_DATE = ("date", "dt_date", "datetime", "date_type", "dateclass")


def _fungsi(berkas, nama):
    t = ast.parse((APP / berkas).read_text())
    hasil = [n for n in ast.walk(t) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nama]
    assert len(hasil) == 1, (berkas, nama, len(hasil))
    return hasil[0]


def _jam_utc(node):
    salah = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            v = n.func.value
            if n.func.attr == "today" and isinstance(v, ast.Name) and v.id in ALIAS_DATE:
                salah.append(n.lineno)
            if n.func.attr == "now" and isinstance(v, ast.Name) and v.id in ("datetime", "dt") and not n.args:
                salah.append(n.lineno)  # datetime.now() tanpa zona dipakai sebagai tanggal
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and "CURRENT_DATE" in n.value \
                and re.search(r"INSERT INTO (journal_entries|bank_transactions|inventory_ledger)\b|FROM fiscal_periods", n.value):
            salah.append(n.lineno)
    return salah


@pytest.mark.parametrize("berkas,nama", [(b, f) for b, fs in FUNGSI.items() for f in fs])
def test_fungsi_tanpa_tanggal_utc(berkas, nama):
    salah = _jam_utc(_fungsi(berkas, nama))
    assert salah == [], f"{berkas}::{nama} tanggal UTC di baris {salah}"


def test_penjaga_bisa_merah():
    t = ast.parse('async def f():\n    a = date_type.today()\n    b = datetime.now()\n'
                  '    q = """INSERT INTO inventory_ledger (movement_date) VALUES (CURRENT_DATE)"""\n')
    assert _jam_utc(t) == [2, 3, 4]


@pytest.fixture
def jam(monkeypatch):
    def pasang(instant):
        class _DT(datetime):
            @classmethod
            def now(cls, tz=None):
                return instant if tz else instant.replace(tzinfo=None)

        monkeypatch.setattr(tt, "datetime", _DT)
        tt._cache.clear()

    yield pasang
    tt._cache.clear()


@pytest.mark.parametrize("instant,harap", KASUS)
@pytest.mark.asyncio
async def test_nomor_gaji_cadangan_ikut_bulan_bisnis(jam, instant, harap):
    """Jalur NOMOR cadangan PR-YYYYMM (fungsi DB tak menghasilkan nomor)."""
    from app.routers import payroll as pr

    jam(instant)

    class _C:
        def __init__(self):
            self.pola = None

        async def fetchval(self, q, *a):
            if "timezone" in q:
                return "Asia/Jakarta"
            if "LIKE" in q:
                self.pola = a[1]
            return None

    c = _C()
    nomor = await pr.get_next_payroll_number(c, TENANT)
    assert c.pola.startswith(f"PR-{harap.strftime('%Y%m')}"), c.pola
    assert nomor.startswith(f"PR-{harap.strftime('%Y%m')}"), nomor


def test_ledger_dan_jurnal_penyesuaian_cepat_satu_tanggal():
    """items.create_stock_adjustment: tanggal jurnal (today) dan movement_date ledger berasal
    dari SATU hari_ini yang ditetapkan SEBELUM cabang (dulu today hanya ada di cabang jurnal)."""
    f = _fungsi("routers/items.py", "create_stock_adjustment")
    defs = [n for n in ast.walk(f) if isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "hari_ini" for t in n.targets)]
    assert len(defs) == 1, "hari_ini harus didefinisikan tepat sekali"
    today = [n for n in ast.walk(f) if isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "today" for t in n.targets)]
    assert today and all(isinstance(n.value, ast.Name) and n.value.id == "hari_ini" for n in today)
    assert defs[0].lineno < min(n.lineno for n in today)
