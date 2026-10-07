"""Audit setelan akuntansi (7 Okt 2026): PATCH/POST /api/settings/accounting meninggalkan jejak audit_logs, atomik dengan tulisannya.
Isi teks bebas TIDAK dicatat (hanya nama medan); id/angka/flag/enum dicatat lama->baru. PATCH tanpa perubahan nyata = tanpa baris."""
import asyncio
import inspect
import json
import os
import uuid
from datetime import datetime
from decimal import Decimal

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest
from starlette.routing import Match

from app.routers import accounting_settings as AS
from app.services import audit_setelan as AU

SID = "610da610-c6df-4013-af7f-601554e2182b"
T = "grapgrap-manado"
SETEL_ID = str(uuid.uuid4())


# ---------------- murni ----------------
def test_tanpa_perubahan_none_dan_abaikan_waktu():
    a = {"id": 1, "tenant_id": T, "updated_at": 1, "created_at": 1, "x": 5}
    b = {"id": 1, "tenant_id": T, "updated_at": 2, "created_at": 1, "x": 5}
    assert AU.ringkas_perubahan(a, b) is None  # hanya updated_at beda -> bukan perubahan


def test_id_angka_flag_dicatat_lama_baru_teks_hanya_nama():
    lama = {"default_quote_signer_user_id": SID, "default_dp_percent": Decimal("30.00"), "flag": True,
            "default_quote_terms": "S&K lama yang panjang", "default_quote_signer_phone": "0812"}
    baru = {"default_quote_signer_user_id": None, "default_dp_percent": Decimal("40.00"), "flag": False,
            "default_quote_terms": "S&K baru", "default_quote_signer_phone": "0899"}
    r = AU.ringkas_perubahan(lama, baru, AS.TEKS_BEBAS_AUDIT)
    assert r["changed"] == sorted(baru)
    assert r["diff"] == {"default_quote_signer_user_id": {"lama": SID, "baru": None},
                         "default_dp_percent": {"lama": "30.00", "baru": "40.00"}, "flag": {"lama": True, "baru": False}}
    assert "S&K" not in json.dumps(r) and "0812" not in json.dumps(r) and "0899" not in json.dumps(r)  # isi teks tak bocor


def test_uuid_tanggal_dijadikan_str_dan_lama_none():
    u = uuid.UUID(SID)
    r = AU.ringkas_perubahan(None, {"k": u, "t": datetime(2026, 10, 7)})
    assert r["diff"]["k"] == {"lama": None, "baru": SID} and r["diff"]["t"]["baru"].startswith("2026-10-07")
    json.dumps(r)  # bisa diserialisasi ke jsonb


def test_teks_bebas_audit_mencakup_semua_medan_teks_setelan():
    assert set(AS.TEKS_BEBAS_AUDIT) == {"default_quote_opening_text", "default_quote_closing_text", "default_quote_notes",
                                        "default_quote_terms", "default_quote_signer_title", "default_quote_signer_phone"}


# ---------------- handler (koneksi palsu) ----------------
def _baris(**ubah):
    r = {"id": SETEL_ID, "tenant_id": T, "default_report_basis": "accrual", "fiscal_year_start_month": 1,
         "base_currency_code": "IDR", "decimal_places": 0, "thousand_separator": ".", "decimal_separator": ",",
         "date_format": "DD/MM/YYYY", "default_dp_percent": None, "default_uang_muka_account_id": None,
         "default_quote_opening_text": "Dengan Hormat,", "default_quote_closing_text": None, "default_quote_notes": None,
         "default_quote_terms": None, "default_quote_signer_user_id": uuid.UUID(SID), "default_quote_signer_title": None,
         "default_quote_signer_phone": None, "created_at": datetime(2026, 9, 1), "updated_at": datetime(2026, 10, 6)}
    r.update(ubah)
    return r


class _Tx:
    def __init__(self, k): self.k = k

    async def __aenter__(self): self.k.log.append("tx-mulai")

    async def __aexit__(self, et, e, tb):
        self.k.log.append("tx-batal" if et else "tx-selesai")
        return False


class _Koneksi:
    def __init__(self, baris_baca):
        self.baris = list(baris_baca)   # urutan fetchrow: existing(id), lama, baru
        self.log, self.audit = [], []

    def transaction(self): return _Tx(self)

    async def fetchrow(self, sql, *a):
        self.log.append("baca")
        return self.baris.pop(0)

    async def execute(self, sql, *a):
        s = " ".join(sql.split())
        if "INSERT INTO audit_logs" in s:
            assert "tx-mulai" in self.log and "tx-selesai" not in self.log  # audit DI DALAM transaksi
            self.audit.append(a)
            self.log.append("audit")
        elif s.startswith("UPDATE accounting_settings"):
            assert "tx-mulai" in self.log
            self.log.append("update")
        else:
            self.log.append("lain")

    async def close(self): self.log.append("tutup")


class _Req:
    def __init__(self): self.state = type("S", (), {"user": {"tenant_id": T, "user_id": "u-pemilik"}})()


def _patch(monkeypatch, koneksi, **badan):
    async def konek(): return koneksi
    monkeypatch.setattr(AS, "get_db_connection", konek)
    return asyncio.run(AS.update_accounting_settings(_Req(), AS.UpdateAccountingSettingsRequest(**badan)))


def test_patch_kosongkan_penanda_tangan_mencatat_audit_lama_baru(monkeypatch):
    k = _Koneksi([{"id": SETEL_ID}, _baris(), _baris(default_quote_signer_user_id=None)])
    _patch(monkeypatch, k, default_quote_signer_user_id=None)
    assert k.log.count("audit") == 1 and k.log.index("update") < k.log.index("audit") < k.log.index("tx-selesai")
    ev, et, eid, tenant, meta, uid = k.audit[0]
    assert (ev, et, eid, tenant, uid) == ("SETTINGS_ACCOUNTING_UPDATED", "accounting_settings", SETEL_ID, T, "u-pemilik")
    m = json.loads(meta)
    assert m["changed"] == ["default_quote_signer_user_id"]
    assert m["diff"] == {"default_quote_signer_user_id": {"lama": SID, "baru": None}}


def test_patch_teks_hanya_nama_medan(monkeypatch):
    k = _Koneksi([{"id": SETEL_ID}, _baris(), _baris(default_quote_opening_text="Salam hangat, ini rahasia")])
    _patch(monkeypatch, k, default_quote_opening_text="Salam hangat, ini rahasia")
    m = json.loads(k.audit[0][4])
    assert m["changed"] == ["default_quote_opening_text"] and m["diff"] == {} and "rahasia" not in k.audit[0][4]


def test_patch_tanpa_perubahan_nyata_tanpa_baris_audit(monkeypatch):
    k = _Koneksi([{"id": SETEL_ID}, _baris(), _baris()])
    _patch(monkeypatch, k)  # badan kosong
    assert "audit" not in k.log and "update" not in k.log


def test_patch_gagal_menulis_audit_membatalkan_transaksi(monkeypatch):
    k = _Koneksi([{"id": SETEL_ID}, _baris(), _baris(default_quote_signer_user_id=None)])
    async def rusak(*a, **kw): raise RuntimeError("audit gagal")
    monkeypatch.setattr(AS, "catat_audit_setelan", rusak)
    with pytest.raises(Exception):
        _patch(monkeypatch, k, default_quote_signer_user_id=None)
    assert "tx-batal" in k.log and "tx-selesai" not in k.log  # tulisan tak tersisa tanpa jejak


def test_post_buat_setelan_mencatat_audit_dibuat(monkeypatch):
    k = _Koneksi([None, _baris()])
    async def konek(): return k
    monkeypatch.setattr(AS, "get_db_connection", konek)
    asyncio.run(AS.create_accounting_settings(_Req(), AS.CreateAccountingSettingsRequest(
        default_report_basis="accrual", fiscal_year_start_month=1, base_currency_code="IDR")))
    assert k.audit and k.audit[0][0] == "SETTINGS_ACCOUNTING_CREATED"


def test_rute_tetap_dan_tak_ada_rute_baru_lewat_tabel_rute():
    from app.main import app

    def pemenang(path, metode):
        scope = {"type": "http", "method": metode, "path": path, "root_path": "", "headers": []}
        for r in app.routes:
            m, _ = r.matches(scope)
            if m == Match.FULL:
                return r.endpoint
        return None
    assert pemenang("/api/settings/accounting", "PATCH") is AS.update_accounting_settings
    assert pemenang("/api/settings/accounting", "POST") is AS.create_accounting_settings
