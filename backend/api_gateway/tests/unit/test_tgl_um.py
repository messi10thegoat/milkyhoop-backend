"""TGL-UM (2 Nov 2026; putusan LANGSUNG pemilik 2 Nov, pola NetSuite): tanggal penerapan uang muka.

SPEK:
- OTOMATIS (posting SI dari SO: DP; posting FPB dari PO: UMP): tanggal penerapan = max(tanggal faktur, tanggal uang muka).
- MANUAL (/apply DP + UMP dan pratinjaunya, penentu rencana_terapkan): tanggal < tanggal uang muka -> DITOLAK berkalimat,
  kode TANGGAL_SEBELUM_UANG_MUKA (DP: GalatBerkode field application_date; UMP: blok). Sama atau sesudah -> lolos.
- pratinjau DP membawa kode GalatBerkode ke blok (bukan APPLY_REJECTED umum).
"""
import asyncio
import inspect
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import tanggal_uang_muka as TUM
from app.services import uang_muka_rencana as UMR
from app.services import uang_muka_vendor_rencana as R
from app.services import ump_terap_otomatis as UTO
from app.services.galat_berkode import GalatBerkode
from app.routers import customer_deposits as CD
from app.routers import sales_invoices as SI
from app.routers import vendor_deposits as RV

T = "tenant-uji"
D1, D2, D3 = date(2026, 10, 1), date(2026, 10, 5), date(2026, 10, 9)


def _j(c):
    return asyncio.run(c)


def test_helper_max_dan_kalimat():
    assert TUM.tanggal_terap_otomatis(D1, D2) == D2
    assert TUM.tanggal_terap_otomatis(D2, D1) == D2
    assert TUM.tanggal_terap_otomatis(D2, "2026-10-09") == D3  # perencana mengirim ISO string
    assert TUM.tanggal_terap_otomatis(D2, None) == D2
    assert TUM.sebelum_uang_muka(D1, D2) and not TUM.sebelum_uang_muka(D2, D2) and not TUM.sebelum_uang_muka(D3, D2)
    assert not TUM.sebelum_uang_muka(D1, None)
    assert TUM.kalimat("DEP-1", D1, D2) == "Tanggal penerapan 1 Okt 2026 tidak boleh sebelum tanggal uang muka DEP-1 (5 Okt 2026)."
    assert TUM.KODE == "TANGGAL_SEBELUM_UANG_MUKA"


# ---- OTOMATIS ----
def _si_auto(monkeypatch, tgl_dp):
    dipanggil = []

    async def sisa(conn, tid, iid):
        return Decimal("1000")

    async def rencana(conn, tid, iid, out, skip=()):
        return [{"deposit_id": str(uuid.uuid4()), "deposit_number": "DEP-1", "deposit_date": tgl_dp,
                 "remaining": Decimal("100"), "planned_amount": Decimal("100"), "skip_reason": None}]

    async def inti(conn, ctx, did, body):
        dipanggil.append(body.application_date)
        return {"data": {"applications": [{"application_id": "a1"}]}}
    monkeypatch.setattr(CD, "get_invoice_remaining_from_journal", sisa)
    monkeypatch.setattr(CD, "plan_so_deposit_application", rencana)
    monkeypatch.setattr(CD, "apply_deposit_core", inti)
    _j(SI._auto_apply_so_deposits(None, {"tenant_id": T, "user_id": None}, uuid.uuid4(), D2))
    return dipanggil


def test_si_otomatis_memakai_max(monkeypatch):
    assert _si_auto(monkeypatch, D3.isoformat()) == [D3]  # DP sesudah faktur -> tanggal DP
    assert _si_auto(monkeypatch, D1.isoformat()) == [D2]  # DP sebelum faktur -> tanggal faktur
    assert _si_auto(monkeypatch, None) == [D2]


class ConnUTO:
    async def fetch(self, sql, *a):
        return [{"po_id": uuid.uuid4()}]

    async def fetchval(self, sql, *a):
        return D2


def _uto_auto(monkeypatch, tgl_um):
    dipanggil = []

    async def po(conn, tid, bid):
        return [uuid.uuid4()]

    async def rencana(conn, tid, bid, out, skip=()):
        return [{"deposit_id": str(uuid.uuid4()), "deposit_number": "VD-1", "deposit_date": tgl_um,
                 "remaining": Decimal("100"), "planned_amount": Decimal("100"), "skip_reason": None}]

    async def sisa(conn, tid, bid):
        return Decimal("1000")

    class Res:
        application_id, journal_id = uuid.uuid4(), uuid.uuid4()

    async def inti(conn, ctx, did, data):
        dipanggil.append(data.applied_date)
        return Res()
    monkeypatch.setattr(UTO, "po_tagihan", po)
    monkeypatch.setattr(UTO, "rencana", rencana)
    monkeypatch.setattr(RV, "get_bill_remaining_from_journal", sisa)
    monkeypatch.setattr(RV, "terapkan_uang_muka_inti", inti)
    _j(UTO.terapkan_otomatis(ConnUTO(), {"tenant_id": T, "user_id": None}, uuid.uuid4(), D2))
    return dipanggil


def test_ump_otomatis_memakai_max(monkeypatch):
    assert _uto_auto(monkeypatch, D3.isoformat()) == [D3]
    assert _uto_auto(monkeypatch, D1.isoformat()) == [D2]


# ---- MANUAL DP (penentu SATU tulis + pratinjau) ----
PELANGGAN = str(uuid.uuid4())


class ConnDP:
    async def fetchrow(self, sql, *a):
        return {"invoice_number": "INV-1", "customer_id": PELANGGAN}

    async def fetchval(self, sql, *a):
        return None


def _dp_manual(monkeypatch, tgl_terap):
    dep = {"id": uuid.uuid4(), "status": "posted", "customer_id": PELANGGAN, "deposit_number": "DEP-1", "deposit_date": D2,
           "amount_applied": 0, "amount_refunded": 0}

    async def um(conn, tid, did):
        return dep

    async def sisa(conn, tid, did):
        return Decimal("1000")

    async def akun(*a, **k):
        raise RuntimeError("LEWAT-PENJAGA-TANGGAL")
    monkeypatch.setattr(UMR, "_uang_muka", um)
    monkeypatch.setattr(CD, "compute_deposit_remaining", sisa)
    monkeypatch.setattr(UMR, "resolve_account_id_by_role", akun)
    body = SimpleNamespace(applications=[SimpleNamespace(invoice_id=str(uuid.uuid4()), amount=Decimal("10"))], application_date=tgl_terap)
    return _j(UMR.rencana_terapkan(ConnDP(), {"tenant_id": T}, dep["id"], body))


def test_dp_manual_sebelum_tanggal_dp_ditolak_berkode(monkeypatch):
    with pytest.raises(GalatBerkode) as e:
        _dp_manual(monkeypatch, D1)
    assert e.value.status_code == 400 and e.value.code == "TANGGAL_SEBELUM_UANG_MUKA" and e.value.field == "application_date"
    assert e.value.detail == "Tanggal penerapan 1 Okt 2026 tidak boleh sebelum tanggal uang muka DEP-1 (5 Okt 2026)."
    for ok in (D2, D3):  # sama / sesudah -> lolos penjaga tanggal (berhenti di langkah berikutnya)
        with pytest.raises(RuntimeError, match="LEWAT-PENJAGA-TANGGAL"):
            _dp_manual(monkeypatch, ok)


def test_pratinjau_dp_membawa_kode_galat():
    s = inspect.getsource(CD._pratinjau_dp)
    assert 'getattr(e, "code", None) or f"{aksi.upper()}_REJECTED"' in s


# ---- MANUAL UMP ----
class ConnUMP:
    async def fetchrow(self, sql, *a):
        return {"id": uuid.uuid4(), "invoice_number": "FPB-1", "status": "posted"}


def _ump_manual(monkeypatch, tgl_terap):
    async def sisa(*a, **k):
        return Decimal("1000")

    async def akun(conn, tid, peran, bloks):
        return uuid.uuid4()

    async def periode(conn, tid, tgl, bloks):
        return None

    async def baris(conn, tid, a, d, k):
        return {}
    monkeypatch.setattr(R.UMV, "sisa_jurnal", sisa)
    monkeypatch.setattr(R, "sisa_tagihan", sisa)
    monkeypatch.setattr(R, "_akun", akun)
    monkeypatch.setattr(R, "_periode", periode)
    monkeypatch.setattr(R, "_baris", baris)
    vd = {"id": uuid.uuid4(), "status": "posted", "vendor_id": uuid.uuid4(), "deposit_number": "VD-1", "deposit_date": D2}
    body = SimpleNamespace(bill_id=uuid.uuid4(), amount=Decimal("10"), applied_date=tgl_terap)
    return _j(R.rencana_terapkan(ConnUMP(), {"tenant_id": T}, vd, body, D3))["blocks"]


def test_ump_manual_sebelum_tanggal_ump_diblok(monkeypatch):
    assert _ump_manual(monkeypatch, D1) == [{"code": "TANGGAL_SEBELUM_UANG_MUKA", "status": 400,
                                             "message": "Tanggal penerapan 1 Okt 2026 tidak boleh sebelum tanggal uang muka VD-1 (5 Okt 2026)."}]
    assert _ump_manual(monkeypatch, D2) == [] and _ump_manual(monkeypatch, D3) == []
    assert _ump_manual(monkeypatch, None) == []  # kosong -> hari_ini (D3) >= D2
