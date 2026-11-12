"""BUG-190: dry-run/nyata month_end_reconcile mengirim TEKS ARAH dari server per baris (labor, overhead, sisa WIP per WO) dan total `selisih_produksi` (+ penjelasan).
nilai > 0 = merugikan (Debit akun selisih); < 0 = menguntungkan (Kredit); contoh TESTER D7: applied 95.000 vs aktual 0 -> -95.000 -> 'Menguntungkan Rp 95.000 → 5-90200 Kredit'."""
import inspect
from decimal import Decimal as D

import pytest

import app.routers.production as P
from app.services import produksi_wip_sisa as PW


@pytest.mark.parametrize("nilai,arah,teks", [
    (D("-95000"), "menguntungkan", "Menguntungkan Rp 95.000 → 5-90200 Kredit"),
    (D("95000"), "merugikan", "Merugikan Rp 95.000 → 5-90200 Debit"),
    (D("0"), "nol", "Tidak ada selisih"),
    (D("0.004"), "nol", "Tidak ada selisih"),                       # < setengah sen = nol
    (D("-1250.50"), "menguntungkan", "Menguntungkan Rp 1.250,50 → 5-90200 Kredit"),
])
def test_arah_selisih(nilai, arah, teks):
    h = PW.arah_selisih(nilai, "5-90200")
    assert h["arah"] == arah and h["teks"] == teks and D(h["nilai"]) == nilai.quantize(D("0.01"))


def test_tanda_nilai_dipertahankan_dan_penjelasan():
    assert PW.arah_selisih(D("-95000"), "5-90200")["nilai"] == "-95000.00"
    assert "Positif = merugikan" in PW.PENJELASAN_SELISIH and "negatif = menguntungkan" in PW.PENJELASAN_SELISIH


def test_respons_dry_run_dan_nyata_memuat_arah_per_baris_dan_total():
    s = inspect.getsource(P.month_end_reconcile)
    assert s.count("PW.arah_selisih(") >= 8
    assert '"selisih_produksi": PW.arah_selisih(var_labor_dr + var_oh_dr + sum(' in s and '"selisih_produksi": PW.arah_selisih(var_labor + var_oh + sum(' in s
    assert s.count('"penjelasan_selisih": PW.PENJELASAN_SELISIH') == 2
    assert '"arah": PW.arah_selisih(var_labor_dr, _kode_var)' in s and '"arah": PW.arah_selisih(var_oh_dr, _kode_var)' in s
    assert 'FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2", variance_id, tenant_id' in s          # kode akun dari CoA tenant (bukan teks tetap)
