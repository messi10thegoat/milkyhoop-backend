"""Audit setelan gelombang 2 (7 Okt 2026, MASTER GO): salary-config (angka TAK masuk metadata), izin cabang grant/revoke, profil usaha + logo.
Pola = accounting: satu transaksi dengan tulisannya, tanpa baris bila tak ada perubahan nyata, teks identitas hanya NAMA medan."""
import asyncio
import json
import os
import uuid
from datetime import date, datetime
from decimal import Decimal

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest
from fastapi import HTTPException

from app.routers import branches as BR
from app.routers import employees as EM
from app.routers import tenant_profile as TP
from app.schemas.branches import CreateBranchPermissionRequest
from app.schemas.employees import SalaryConfigItem, SetSalaryConfigRequest
from app.services import audit_setelan as AU

T = "t-uji"
U = "0bccdb25-fdf0-4e99-9024-b9a20846f76c"
EMP = uuid.uuid4()
KOMP = uuid.uuid4()
TGL = date(2026, 10, 1)


class _Tx:
    def __init__(self, k): self.k = k

    async def __aenter__(self): self.k.log.append("tx-mulai")

    async def __aexit__(self, et, e, tb):
        self.k.log.append("tx-batal" if et else "tx-selesai")
        return False


class _K:
    """Koneksi palsu. baca = antrean hasil fetchrow (urut panggilan); fetchval -> nilai tetap."""
    def __init__(self, baca=(), fetchval=None, hapus="DELETE 1"):
        self.baca, self.fv, self.hapus = list(baca), fetchval, hapus
        self.log, self.audit, self.tulis = [], [], []

    def transaction(self): return _Tx(self)

    async def fetchrow(self, sql, *a):
        self.log.append("baca")
        return self.baca.pop(0) if self.baca else None

    async def fetchval(self, sql, *a):
        return self.fv

    async def execute(self, sql, *a):
        s = " ".join(sql.split())
        if "INSERT INTO audit_logs" in s:
            assert "tx-mulai" in self.log and "tx-selesai" not in self.log  # audit DI DALAM transaksi
            self.audit.append(a)
            self.log.append("audit")
            return "INSERT 0 1"
        if s.startswith(("INSERT", "UPDATE", "DELETE")):
            self.tulis.append(s[:40])
            self.log.append("tulis")
            return self.hapus if s.startswith("DELETE") else "OK"
        return "OK"

    async def close(self): pass


class _Pool:
    def __init__(self, k): self.k = k

    def acquire(self):
        k = self.k

        class _A:
            async def __aenter__(s): return k

            async def __aexit__(s, *e): return False
        return _A()


class _Req:
    def __init__(self):
        self.state = type("S", (), {"user": {"tenant_id": T, "user_id": U}})()
        self.headers = {}


def _meta(k, i=0):
    ev, et, eid, tenant, meta, uid = k.audit[i]
    return ev, et, eid, tenant, json.loads(meta), uid


# =============== salary-config ===============
def _gaji(monkeypatch, k):
    async def gp(): return _Pool(k)

    async def dalam(conn, tenant, user, emp): return True
    monkeypatch.setattr(EM, "get_pool", gp)
    monkeypatch.setattr(EM, "get_user_context", lambda r: {"tenant_id": T, "user_id": U})
    import app.services.pay_group_access as PG
    monkeypatch.setattr(PG, "employee_in_scope", dalam)


def _put_gaji(k, *configs):
    body = SetSalaryConfigRequest(configs=[SalaryConfigItem(component_id=KOMP, amount=a, percentage=p, effective_date=TGL) for a, p in configs])
    return asyncio.run(EM.set_salary_config(_Req(), EMP, body))


def test_gaji_ubah_angka_audit_nama_medan_tanpa_angka(monkeypatch):
    k = _K(baca=[{"id": EMP}, {"amount": Decimal("5000000.00"), "percentage": None}])
    _gaji(monkeypatch, k)
    _put_gaji(k, (6500000, None))
    ev, et, eid, tenant, m, uid = _meta(k)
    assert (ev, et, eid, tenant, uid) == ("SETTINGS_SALARY_CONFIG_UPDATED", "employee", str(EMP), T, U)
    assert m["configs"] == [{"component_id": str(KOMP), "effective_date": "2026-10-01", "aksi": "ubah", "medan": ["amount"]}]
    jejak = json.dumps(m)
    assert "6500000" not in jejak and "5000000" not in jejak  # ANGKA GAJI tak pernah di metadata
    assert k.log.index("tulis") < k.log.index("audit") < k.log.index("tx-selesai")


def test_gaji_komponen_baru_aksi_tambah(monkeypatch):
    k = _K(baca=[{"id": EMP}, None])
    _gaji(monkeypatch, k)
    _put_gaji(k, (1000000, None))
    assert _meta(k)[4]["configs"][0]["aksi"] == "tambah"


def test_gaji_tanpa_perubahan_tanpa_audit(monkeypatch):
    k = _K(baca=[{"id": EMP}, {"amount": Decimal("5000000.00"), "percentage": None}])
    _gaji(monkeypatch, k)
    _put_gaji(k, (5000000, None))
    assert not k.audit


def test_gaji_karyawan_di_luar_pay_group_tetap_404_tanpa_audit(monkeypatch):
    k = _K(baca=[{"id": EMP}])
    _gaji(monkeypatch, k)

    async def luar(conn, tenant, user, emp): return False
    import app.services.pay_group_access as PG
    monkeypatch.setattr(PG, "employee_in_scope", luar)
    with pytest.raises(HTTPException) as e:
        _put_gaji(k, (1, None))
    assert e.value.status_code == 404 and not k.audit and not k.tulis


# =============== izin cabang ===============
def _cabang(monkeypatch, k):
    async def gp(): return _Pool(k)
    monkeypatch.setattr(BR, "get_pool", gp)
    monkeypatch.setattr(BR, "get_user_context", lambda r: {"tenant_id": T, "user_id": U})


BR_ID, PERM_ID, UID_TARGET = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _flag(**u):
    d = {"can_view": True, "can_create": False, "can_edit": False, "can_delete": False, "can_approve": False, "is_default": False}
    d.update(u)
    return d


def test_grant_baru_dan_ubah_dicatat_lama_baru(monkeypatch):
    k = _K(fetchval=1, baca=[None])
    k.fv = 1
    # fetchval dipakai dua kali: exists-cabang (1) lalu perm_id (RETURNING id)
    vals = iter([1, 1, PERM_ID])  # exists-cabang, anggota aktif, RETURNING id
    async def fv(sql, *a): return next(vals)
    k.fetchval = fv
    _cabang(monkeypatch, k)
    body = CreateBranchPermissionRequest(user_id=UID_TARGET, can_view=True, can_edit=True)
    asyncio.run(BR.grant_permission(_Req(), BR_ID, body))
    ev, et, eid, tenant, m, uid = _meta(k)
    assert (ev, et, eid) == ("SETTINGS_BRANCH_PERMISSION_GRANTED", "branch_permission", str(PERM_ID))
    assert m["user_id"] == str(UID_TARGET) and m["branch_id"] == str(BR_ID)
    assert m["diff"]["can_edit"] == {"lama": None, "baru": True}

    k2 = _K(baca=[_flag()])
    vals2 = iter([1, 1, PERM_ID])
    async def fv2(sql, *a): return next(vals2)
    k2.fetchval = fv2
    _cabang(monkeypatch, k2)
    asyncio.run(BR.grant_permission(_Req(), BR_ID, CreateBranchPermissionRequest(user_id=UID_TARGET, can_edit=True)))
    m2 = _meta(k2)[4]
    assert m2["changed"] == ["can_edit"] and m2["diff"] == {"can_edit": {"lama": False, "baru": True}}


def test_grant_identik_tanpa_audit(monkeypatch):
    k = _K(baca=[_flag()])
    vals = iter([1, 1, PERM_ID])  # exists-cabang, anggota aktif, RETURNING id
    async def fv(sql, *a): return next(vals)
    k.fetchval = fv
    _cabang(monkeypatch, k)
    asyncio.run(BR.grant_permission(_Req(), BR_ID, CreateBranchPermissionRequest(user_id=UID_TARGET)))
    assert not k.audit


def test_revoke_dicatat_dan_404_tanpa_audit(monkeypatch):
    lama = dict(_flag(can_edit=True), user_id=UID_TARGET, branch_id=BR_ID)
    k = _K(baca=[lama])
    _cabang(monkeypatch, k)
    asyncio.run(BR.revoke_permission(_Req(), PERM_ID))
    ev, et, eid, tenant, m, uid = _meta(k)
    assert (ev, et, eid) == ("SETTINGS_BRANCH_PERMISSION_REVOKED", "branch_permission", str(PERM_ID))
    assert m["user_id"] == str(UID_TARGET) and m["diff"]["can_edit"] == {"lama": True, "baru": None}
    k404 = _K(baca=[lama], hapus="DELETE 0")
    _cabang(monkeypatch, k404)
    with pytest.raises(HTTPException) as e:
        asyncio.run(BR.revoke_permission(_Req(), PERM_ID))
    assert e.value.status_code == 404 and not k404.audit and "tx-batal" in k404.log


# =============== profil usaha ===============
def _baris_profil(**u):
    d = {"display_name": "Grapgrap Clothing", "address": "Jl. Rahasia 1", "phone": "0431-1", "tax_id": "01.234.567.8-901.000",
         "pdf_template": "a", "workshop_address": None, "signatory_name": None, "alias": "grapgrap-manado", "status": "ACTIVE",
         "timezone": "Asia/Makassar", "currency": "IDR", "logo_url": None}
    d.update(u)
    return d


def _profil(monkeypatch, k):
    async def konek(): return k
    monkeypatch.setattr(TP, "get_db_connection", konek)


def test_profil_npwp_nama_alamat_hanya_nama_medan_template_lama_baru(monkeypatch):
    k = _K(baca=[_baris_profil(), _baris_profil(tax_id="99.999.999.9-999.999", address="Jl. Baru 2", pdf_template="b")])
    _profil(monkeypatch, k)
    asyncio.run(TP.update_tenant_profile(TP.UpdateTenantProfileRequest(tax_id="99.999.999.9-999.999", address="Jl. Baru 2", pdf_template="b"), _Req()))
    ev, et, eid, tenant, m, uid = _meta(k)
    assert (ev, et, eid, tenant, uid) == ("SETTINGS_TENANT_PROFILE_UPDATED", "tenant", None, T, U)
    assert m["changed"] == ["address", "pdf_template", "tax_id"] and m["diff"] == {"pdf_template": {"lama": "a", "baru": "b"}}
    j = json.dumps(m)
    assert "01.234" not in j and "99.999" not in j and "Rahasia" not in j and "Jl. Baru" not in j  # NPWP/alamat tak bocor
    assert m["via"] == "profil" and m["tenant"] == T


def test_profil_tanpa_perubahan_nyata_tanpa_audit(monkeypatch):
    k = _K(baca=[_baris_profil(), _baris_profil()])
    _profil(monkeypatch, k)
    asyncio.run(TP.update_tenant_profile(TP.UpdateTenantProfileRequest(phone="0431-1"), _Req()))
    assert not k.audit


def test_logo_hapus_dicatat_lama_baru_via_logo(monkeypatch):
    k = _K(baca=[_baris_profil(logo_url="grap-1.png"), _baris_profil(logo_url=None)])
    _profil(monkeypatch, k)
    asyncio.run(TP.delete_tenant_logo(_Req()))
    m = _meta(k)[4]
    assert m["via"] == "logo" and m["diff"] == {"logo_url": {"lama": "grap-1.png", "baru": None}}


def test_profil_gagal_audit_membatalkan(monkeypatch):
    k = _K(baca=[_baris_profil(), _baris_profil(display_name="Baru")])
    _profil(monkeypatch, k)

    async def rusak(*a, **kw): raise RuntimeError("audit gagal")
    monkeypatch.setattr(TP, "catat_audit_setelan", rusak)
    with pytest.raises(HTTPException):
        asyncio.run(TP.update_tenant_profile(TP.UpdateTenantProfileRequest(display_name="Baru"), _Req()))
    assert "tx-batal" in k.log and "tx-selesai" not in k.log


# =============== helper murni ===============
def test_ringkas_konfigurasi_gaji_murni():
    c = SalaryConfigItem(component_id=KOMP, amount=100, percentage=None, effective_date=TGL)
    k = (str(KOMP), "2026-10-01")
    assert AU.ringkas_konfigurasi_gaji({k: {"amount": Decimal("100.00"), "percentage": None}}, [c]) is None
    assert AU.ringkas_konfigurasi_gaji({k: {"amount": Decimal("99"), "percentage": None}}, [c])["configs"][0]["medan"] == ["amount"]


def test_rute_tetap_dimenangkan_handler_benar_lewat_tabel_rute():
    from starlette.routing import Match

    from app.main import app

    def pemenang(path, metode):
        scope = {"type": "http", "method": metode, "path": path, "root_path": "", "headers": []}
        for r in app.routes:
            m, _ = r.matches(scope)
            if m == Match.FULL:
                return r.endpoint
        return None
    assert pemenang(f"/api/employees/{EMP}/salary-config", "PUT") is EM.set_salary_config
    assert pemenang(f"/api/branches/{BR_ID}/permissions", "POST") is BR.grant_permission
    assert pemenang(f"/api/branches/permissions/{PERM_ID}", "DELETE") is BR.revoke_permission
    assert pemenang("/api/tenant/profile", "PATCH") is TP.update_tenant_profile
    assert pemenang("/api/tenant/profile/logo", "DELETE") is TP.delete_tenant_logo
