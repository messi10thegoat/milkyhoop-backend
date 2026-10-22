"""T1-BE "ANGKA BENAR" (22 Okt 2026): /api/dashboard/all dan /v2/summary memakai SATU penentu (services/dashboard_angka, jurnal EFEKTIF).
Paritas angka pada data nyata: scripts/ops/uji-nyata-dashboard-paritas.sh (MERAH=1 -> kaos kas +65.218)."""
import inspect
from datetime import date
from decimal import Decimal

from app.routers import dashboard as DB
from app.services import dashboard_angka as DA
from app.services import dashboard_v2 as V2
from app.services.report_engine import ledger as L


def test_filter_efektif_opt_in():
    c, _ = L._build_where("t", {"is_cash": True, "efektif": True}, None, None)
    assert "is_effective_journal(je.id)" in c
    c2, _ = L._build_where("t", {"is_cash": True}, None, None)
    assert "is_effective_journal(je.id)" not in c2  # laporan tetap POSTED-saja


def test_hari_periode_dan_periode_sebelumnya():
    assert DA.hari_periode(date(2026, 10, 1), date(2026, 10, 31)) == 31
    assert DA.periode_sebelumnya(date(2026, 10, 1), date(2026, 10, 31)) == (date(2026, 8, 31), date(2026, 9, 30))


def test_dpo_dso_baku():
    assert DA.dpo(Decimal("1800000"), Decimal("0"), 31) == 0.0  # HPP 0 -> tak terdefinisi, bukan dari beban total
    assert DA.dpo(Decimal("3100"), Decimal("3100"), 31) == 31.0
    assert DA.dso(Decimal("100"), Decimal("3100"), 31) == 1.0


def test_satu_penentu_dua_endpoint():
    v2 = inspect.getsource(V2.kas_bank) + inspect.getsource(V2.laba_rugi_dan_beban) + inspect.getsource(V2.arus_kas)
    assert "DA.kas_per_akun(" in v2 and "DA.laba_rugi(" in v2 and "DA.arus_kas_harian(" in v2
    s = inspect.getsource(DB.get_dashboard_summary)
    assert "DA.laba_rugi(" in s and "DA.kas_per_akun(" in s and "DA.saldo_kontrol(" in s and "get_revenue_by_basis" not in s.split("Pure Ledger")[-1]
    assert "DA.arus_kas_harian(" in inspect.getsource(DB.get_cash_flow_trends)
    te = inspect.getsource(DB.get_top_expenses)
    assert "DA.laba_rugi(" in te and "LIKE '5-" not in te


def test_basis_kas_jujur_dan_errors_widget():
    s = inspect.getsource(DB.get_dashboard_summary)
    assert 'errors["basis"] = "belum_tersedia"' in s
    a = inspect.getsource(DB.get_dashboard_all)
    assert 'errors[label] = "gagal_hitung"' in a and 'response["errors"] = errors' in a


def test_terdekat_belum_lewat():
    assert "WHERE outstanding > 0 AND due_date >= $2::date" in inspect.getsource(DB.get_dashboard_summary)
