"""#10b-3a — sisi BACA memakai tanggal bisnis tenant (lanjutan #10a/#10b-1/#10b-2).

Sisi tulis sudah bertanggal WIB, sisi baca masih UTC: 25 Sep 00:17–00:53 WIB, 12 jurnal
TAK TERLIHAT di Buku Besar (as_of default date.today() = 24 Sep). Hal yang sama pada
overdue/aging/ringkasan "bulan ini" dashboard, faktur, tagihan, biaya, laporan umur.

Pola: hari_ini = await tanggal_dokumen(conn, tenant) SEKALI, dikirim sebagai $N::date.
Risiko utama pola itu = nomor parameter asyncpg bergeser → diuji di sini dengan koneksi
tiruan yang menolak kueri bila {$N} != {1..len(args)}.

Jam DISUNTIK ke tanggal_tenant: 2026-09-24 18:00Z (-> 25 Sep WIB), kontrol 10:00Z.
"""

import ast
import itertools
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.utils import tanggal_tenant as tt

APP = Path(tt.__file__).resolve().parents[1]
TENANT = "kaos-biru-konveksi"
KASUS = [(datetime(2026, 9, 24, 18, 0, tzinfo=timezone.utc), date(2026, 9, 25)),
         (datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc), date(2026, 9, 24))]

FUNGSI = {
    "routers/ledger.py": ["list_ledger_accounts", "get_ledger_summary", "get_account_balance"],
    "routers/sales_invoices.py": ["get_invoice_summary", "get_outstanding_summary", "list_invoices"],
    "routers/customers.py": ["get_customer_balance", "get_customer_open_invoices"],
    "routers/vendors.py": ["list_vendors", "get_vendor_balance", "get_vendor_open_bills"],
    "routers/expenses.py": ["get_expenses_summary"],
    "services/bills_service.py": ["list_bills", "get_bill", "get_bill_v2", "get_summary",
                                  "get_outstanding_summary"],
    "routers/dashboard.py": ["get_days_in_period", "get_period_date_range",
                             "get_prev_period_date_range", "_get_upcoming_due", "_get_sales_today",
                             "get_dashboard_summary", "get_piutang_detail", "get_hutang_detail",
                             "get_cash_flow_trends", "get_top_expenses", "get_overdue_invoices",
                             "get_overdue_bills", "get_cash_flow_projection", "get_sales_daily"],
    "routers/reports.py": ["get_trial_balance_full", "get_timing_differences", "_tolak_as_of_lampau",
                           "get_ar_aging_summary", "get_ar_aging_detail", "get_ar_aging_for_customer",
                           "get_ap_aging_summary", "get_ap_aging_detail", "get_ap_aging_for_vendor",
                           "get_aging_receivable", "get_customer_aging_invoices", "get_aging_payable",
                           "get_vendor_aging_bills"],
}
# Berkas yang SELURUHNYA sisi baca untuk CURRENT_DATE: tak boleh ada satu pun di string kode.
# (Sisi tulis sisa — customers/vendors saldo awal, fulfillment, snapshot aging — memakai
# date.today() di Python, bukan CURRENT_DATE; itu unit #10b-3b.)
TANPA_CURRENT_DATE = list(FUNGSI)
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
                salah.append(n.lineno)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and re.search(r"\bCURRENT_DATE\b", n.value):
            salah.append(n.lineno)
    return salah


@pytest.mark.parametrize("berkas,nama", [(b, f) for b, fs in FUNGSI.items() for f in fs])
def test_fungsi_baca_tanpa_tanggal_utc(berkas, nama):
    salah = _jam_utc(_fungsi(berkas, nama))
    assert salah == [], f"{berkas}::{nama} tanggal UTC di baris {salah}"


@pytest.mark.parametrize("berkas", TANPA_CURRENT_DATE)
def test_berkas_baca_tanpa_current_date(berkas):
    t = ast.parse((APP / berkas).read_text())
    salah = [n.lineno for n in ast.walk(t) if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and re.search(r"\bCURRENT_DATE\b", n.value)]
    assert salah == [], f"{berkas}: CURRENT_DATE di baris {salah}"


def test_penjaga_bisa_merah():
    t = ast.parse('async def f():\n    a = date.today()\n    b = datetime.now()\n'
                  '    q = "SELECT 1 WHERE due_date < CURRENT_DATE"\n    ok = datetime.now(timezone.utc)\n')
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


def _cek_nomor(q, args):
    pakai = {int(x) for x in re.findall(r"\$(\d+)", q)}
    assert pakai == set(range(1, len(args) + 1)), f"placeholder {sorted(pakai)} vs {len(args)} arg\n{q}"


class _Conn:
    """Koneksi tiruan: menolak kueri yang nomor $N-nya tak cocok dengan jumlah argumen."""

    def __init__(self):
        self.panggilan = []

    async def fetchval(self, q, *a):
        if 'FROM "Tenant"' in q:
            return "Asia/Jakarta"
        _cek_nomor(q, a)
        self.panggilan.append((q, a))
        return 0

    async def fetch(self, q, *a):
        _cek_nomor(q, a)
        self.panggilan.append((q, a))
        return []

    async def fetchrow(self, q, *a):
        _cek_nomor(q, a)
        self.panggilan.append((q, a))
        return None

    async def execute(self, q, *a):
        return "OK"


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool.conn

            async def __aexit__(self, *e):
                return False

        return _Ctx()


def test_pemeriksa_nomor_bisa_merah():
    with pytest.raises(AssertionError):
        _cek_nomor("SELECT $1, $3::date", ("t", date(2026, 9, 25)))
    _cek_nomor("SELECT $1, $2::date", ("t", date(2026, 9, 25)))


@pytest.mark.parametrize("instant,harap", KASUS)
@pytest.mark.asyncio
async def test_buku_besar_default_as_of_tanggal_bisnis(jam, monkeypatch, instant, harap):
    """Gejala asli: Buku Besar (FE tak kirim as_of) menyembunyikan jurnal 00–07 WIB."""
    from app.routers import ledger

    jam(instant)
    c = _Conn()

    async def _pool():
        return _Pool(c)

    monkeypatch.setattr(ledger, "get_pool", _pool)
    monkeypatch.setattr(ledger, "get_user_context", lambda r: {"tenant_id": TENANT})
    res = await ledger.list_ledger_accounts(request=None, as_of_date=None, account_type=None,
                                            include_zero=False)
    assert res.as_of_date == harap
    (q, a), = c.panggilan
    assert a[1] == harap and "je.journal_date <= $2" in q


@pytest.mark.asyncio
async def test_buku_besar_as_of_eksplisit_tak_berubah(jam, monkeypatch):
    from app.routers import ledger

    jam(KASUS[0][0])
    c = _Conn()

    async def _pool():
        return _Pool(c)

    monkeypatch.setattr(ledger, "get_pool", _pool)
    monkeypatch.setattr(ledger, "get_user_context", lambda r: {"tenant_id": TENANT})
    res = await ledger.list_ledger_accounts(request=None, as_of_date=date(2026, 8, 31),
                                            account_type="asset", include_zero=True)
    assert res.as_of_date == date(2026, 8, 31)
    assert c.panggilan[0][1] == (TENANT, date(2026, 8, 31), "ASSET")


KOMBINASI_BILLS = list(itertools.product(
    ["all", "active", "unpaid", "overdue"],
    [None, "kain", "kain biru"],
    [[("created_at", "desc")], [("status", "asc")]],
    [False, True],
))


@pytest.mark.parametrize("status,search,sort,filter_", KOMBINASI_BILLS)
@pytest.mark.asyncio
async def test_list_bills_nomor_parameter(jam, status, search, sort, filter_):
    """Fragmen status dinamis + ORDER BY status + count: $N hari_ini harus pas di tiap kueri."""
    from uuid import UUID
    from app.services.bills_service import BillsService

    jam(KASUS[0][0])
    c = _Conn()
    extra = dict(due_date_from=date(2026, 9, 1), due_date_to=date(2026, 9, 30),
                 vendor_id=UUID(int=1), amount_min=1, amount_max=9) if filter_ else {}
    await BillsService(_Pool(c)).list_bills(TENANT, status=status, search=search,
                                            sort_fields=sort, **extra)
    assert c.panggilan, "tak ada kueri tereksekusi"
    pakai_tanggal = [a for q, a in c.panggilan if "::date" in q]
    assert pakai_tanggal and all(KASUS[0][1] in a for a in pakai_tanggal)


@pytest.mark.parametrize("period", ["current_month", "last_month", "current_year", "2026-08"])
@pytest.mark.asyncio
async def test_bills_summary_bulan_bisnis(jam, period):
    """Tanggal 1 Okt 00–07 WIB: 'bulan ini' = Oktober, bukan September."""
    from app.services.bills_service import BillsService

    jam(datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc))
    c = _Conn()
    try:
        await BillsService(_Pool(c)).get_summary(TENANT, period=period)
    except (TypeError, AttributeError, KeyError):
        pass  # hasil tiruan None/0 boleh gagal DIPROSES; kueri sudah dicek nomornya
    stats = c.panggilan[0][1]
    if period == "current_month":
        assert stats[1] == date(2026, 10, 1), stats


@pytest.mark.asyncio
async def test_bills_outstanding_nomor_parameter(jam):
    from app.services.bills_service import BillsService

    jam(KASUS[0][0])
    c = _Conn()
    try:
        await BillsService(_Pool(c)).get_outstanding_summary(TENANT)
    except (TypeError, AttributeError, KeyError):
        pass
    assert c.panggilan and c.panggilan[0][1] == (TENANT, KASUS[0][1])
