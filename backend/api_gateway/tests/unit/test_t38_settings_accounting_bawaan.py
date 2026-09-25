"""#38 (QA Cowork BUG-003) -- /api/settings/accounting untuk tenant TANPA baris accounting_settings.

Diukur 25 Sep: 5/6 tenant (termasuk grapgrap) tanpa baris. Dulu:
  GET   -> 404 (form penawaran grapgrap membuka dengan galat yang ditelan FE)
  PATCH -> 404 (halaman "Default Penawaran" gagal simpan DIAM-DIAM)
Kini GET 200 + BAWAAN (= DEFAULT kolom, bentuk sama, is_default, TANPA tulis); PATCH membuat baris
(ON CONFLICT DO NOTHING) lalu memperbarui. Handler dipanggil utuh di atas koneksi palsu.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.routers import accounting_settings as AS

TENANT = "tenant-uji"


class Conn:
    def __init__(self, row=None):
        self.row = row
        self.tulis = []

    async def fetchrow(self, sql, *a):
        s = " ".join(sql.split())
        if s.startswith("SELECT id FROM accounting_settings"):
            return {"id": self.row["id"]} if self.row else None
        if s.startswith("SELECT * FROM accounting_settings"):
            return self.row
        raise AssertionError(f"fetchrow tak dikenal: {s[:60]}")

    async def execute(self, sql, *a):
        s = " ".join(sql.split())
        self.tulis.append(s)
        if s.startswith("INSERT INTO accounting_settings"):
            assert "ON CONFLICT (tenant_id) DO NOTHING" in s
            now = datetime(2026, 9, 25, tzinfo=timezone.utc)
            self.row = {"id": a[0], "tenant_id": a[1], **AS.BAWAAN, "created_at": now, "updated_at": now}
        elif s.startswith("UPDATE accounting_settings"):
            assert self.row is not None, "UPDATE tanpa baris = simpan hilang"
            # cukup untuk tes: kolom teks pembuka
            if "default_quote_opening_text" in s:
                self.row = {**self.row, "default_quote_opening_text": a[1]}
        return "OK"

    async def close(self):
        pass


@pytest.fixture
def pasang(monkeypatch):
    def _p(conn):
        async def _c():
            return conn
        monkeypatch.setattr(AS, "get_db_connection", _c)
        return conn
    return _p


def _req():
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": TENANT, "user_id": "u"}))


@pytest.mark.asyncio
async def test_get_tanpa_baris_200_bawaan_tanpa_tulis(pasang):
    c = pasang(Conn())
    r = await AS.get_accounting_settings(_req())
    d = r.data.model_dump()
    assert d["is_default"] is True and d["id"] is None and d["tenant_id"] == TENANT
    assert {k: d[k] for k in AS.BAWAAN} == AS.BAWAAN
    assert c.tulis == []


def test_bawaan_sama_dengan_default_kolom_terukur():
    # information_schema.columns accounting_settings, prod 25 Sep 2026
    assert AS.BAWAAN == {
        "default_report_basis": "accrual", "fiscal_year_start_month": 1, "base_currency_code": "IDR",
        "decimal_places": 0, "thousand_separator": ".", "decimal_separator": ",",
        "date_format": "DD/MM/YYYY", "default_dp_percent": None, "default_uang_muka_account_id": None,
        "default_quote_opening_text": None, "default_quote_closing_text": None,
    }


@pytest.mark.asyncio
async def test_get_dengan_baris_tak_berubah(pasang):
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    pasang(Conn({"id": "abc", "tenant_id": TENANT, **AS.BAWAAN, "default_quote_opening_text": "Halo",
                 "created_at": now, "updated_at": now}))
    d = (await AS.get_accounting_settings(_req())).data.model_dump()
    assert d["is_default"] is False and d["id"] == "abc" and d["default_quote_opening_text"] == "Halo"


@pytest.mark.asyncio
async def test_patch_tanpa_baris_membuat_lalu_menyimpan(pasang):
    c = pasang(Conn())
    r = await AS.update_accounting_settings(
        _req(), AS.UpdateAccountingSettingsRequest(default_quote_opening_text="Dengan hormat")
    )
    assert r.data.default_quote_opening_text == "Dengan hormat"
    assert r.data.is_default is False and r.data.id
    assert [t.split(" ")[0] for t in c.tulis] == ["INSERT", "UPDATE"]
