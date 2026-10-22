"""Peta modul izin uang muka EKSPLISIT (22 Okt 2026, MASTER). Dulu vendor_deposit/customer_deposit hanya benar karena fallback
module.upper() kebetulan sama dengan kode role_permissions; mengubah fallback (mis. ke nilai asing / tolak) diam-diam memutus izin."""
from app.services import policy_engine_client as PE


def test_pemetaan_eksplisit_uang_muka():
    assert PE.MODULE_NAME_MAPPING["vendor_deposit"] == "VENDOR_DEPOSIT"
    assert PE.MODULE_NAME_MAPPING["customer_deposit"] == "CUSTOMER_DEPOSIT"


def test_tak_bergantung_pada_fallback(monkeypatch):
    # kontrol: fallback diganti -> hasil tetap benar HANYA bila pemetaan eksplisit ada
    monkeypatch.setattr(PE, "normalize_module_name", lambda m: PE.MODULE_NAME_MAPPING.get(m.lower(), "__TAK_DIPETAKAN__"))
    assert PE.normalize_module_name("vendor_deposit") == "VENDOR_DEPOSIT"
    assert PE.normalize_module_name("customer_deposit") == "CUSTOMER_DEPOSIT"
