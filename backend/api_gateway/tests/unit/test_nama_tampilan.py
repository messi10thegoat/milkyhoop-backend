"""Nama aktor/pengguna untuk TAMPILAN = SATU rantai (7 Okt 2026): user_profiles.display_name -> fullname -> name (-> surel untuk tampilan riwayat).
Latar: riwayat SO/uang muka karyawan memakai fullname dulu, tampilan faktur/bill/lampiran memakai name dulu, dropdown/me memakai profil dulu ->
satu orang tampil tiga nama. Ditambah pagar: penerima izin cabang harus anggota AKTIF tenant (Law 24)."""
import asyncio
import ast
import json
import os
import re
import uuid

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest
from fastapi import HTTPException

from app.routers import branches as BR
from app.schemas.branches import CreateBranchPermissionRequest
from app.services import nama_pengguna as NP

APP = os.path.join(os.path.dirname(__file__), "..", "..", "app")


def _jalan(c): return asyncio.run(c)


class _K:
    def __init__(self, baris): self.baris, self.sql = baris, []

    async def fetch(self, sql, *a):
        self.sql.append(" ".join(sql.split()))
        return self.baris


def test_nama_untuk_satu_kueri_rantai_dan_cadangan_surel():
    k = _K([{"id": "u1", "nama": "Grapgrap Clothing"}])
    assert _jalan(NP.nama_untuk(k, ["u1", None, "u1", ""])) == {"u1": "Grapgrap Clothing"}
    assert len(k.sql) == 1 and "LEFT JOIN user_profiles p ON p.user_id = u.id" in k.sql[0]
    s = k.sql[0]
    assert s.index("p.display_name") < s.index("u.fullname") < s.index("u.name") < s.index("u.email")   # surel TERAKHIR
    k2 = _K([])
    _jalan(NP.nama_untuk(k2, ["u1"], cadangan_email=False))
    assert "u.email" not in k2.sql[0]
    assert _jalan(NP.nama_untuk(_K([]), [None, ""])) == {}   # tanpa id -> tanpa kueri


def _semua_py():
    for akar, _, berkas in os.walk(APP):
        for f in berkas:
            if f.endswith(".py"):
                yield os.path.join(akar, f)


def test_tak_ada_lagi_rantai_nama_mentah_di_aplikasi():
    """Penjaga EKSHAUSTIF: pembaca nama pengguna untuk tampilan wajib lewat services/nama_pengguna."""
    pola = [re.compile(r"COALESCE\((\w+)\.name,\s*\1\.fullname"),               # name dulu (bills/faktur/lampiran/deposit)
            re.compile(r"COALESCE\(NULLIF\(fullname,\s*''\),\s*NULLIF\(name,"),   # riwayat SO / uang muka karyawan
            re.compile(r'\["\w*fullname"\]\s+or\s+\w+\["\w*name"\]')]            # python: fullname or name
    salah = []
    for p in _semua_py():
        if p.endswith(os.path.join("services", "nama_pengguna.py")):
            continue
        teks = open(p, encoding="utf-8").read()
        for r in pola:
            if r.search(teks):
                salah.append((os.path.relpath(p, APP), r.pattern))
    assert not salah, salah


def test_pembaca_tampilan_memakai_fungsi_bersama():
    f = lambda rel: open(os.path.join(APP, rel), encoding="utf-8").read()
    assert "nama_untuk(conn, ids)" in f("services/so_riwayat.py")
    assert "nama_untuk(conn, ids)" in f("routers/employee_advances.py")
    assert "nama_untuk(" in f("routers/bills.py") and "nama_pengguna(" in f("routers/bills.py")
    assert "nama_untuk(" in f("routers/sales_invoices.py")
    for rel, kolom in (("services/bills_service.py", "created_by_name"), ("routers/sales_invoices.py", "created_by_name"),
                       ("routers/customer_deposits.py", "uploaded_by_name"), ("routers/bills.py", "uploaded_by_name")):
        teks = f(rel)
        assert NP.nama_pengguna_sql("up", "u", True) in teks or NP.nama_pengguna_sql("up_c", "u_created", True) in teks, rel
        assert "LEFT JOIN user_profiles" in teks and kolom in teks


# ---------------- pagar anggota cabang ----------------
T, U = "t-uji", "0bccdb25-fdf0-4e99-9024-b9a20846f76c"
BR_ID, UID = uuid.uuid4(), uuid.uuid4()


class _Tx:
    def __init__(self, k): self.k = k
    async def __aenter__(self): self.k.log.append("tx")
    async def __aexit__(self, *e): return False


class _KB:
    def __init__(self, anggota):
        self.anggota, self.log, self.tulis, self.audit = anggota, [], [], []
        self.nilai = iter([1])   # exists-cabang

    def transaction(self): return _Tx(self)

    async def fetchval(self, sql, *a):
        s = " ".join(sql.split())
        if "user_tenant_roles" in s:
            assert a[0] == T and str(a[1]) == str(UID)   # tenant EKSPLISIT, user penerima
            return 1 if self.anggota else None
        if "FROM branches" in s:
            return 1
        return uuid.uuid4()   # RETURNING id

    async def fetchrow(self, sql, *a): return None

    async def execute(self, sql, *a):
        s = " ".join(sql.split())
        self.tulis.append(s)
        return "OK"


class _Pool:
    def __init__(self, k): self.k = k

    def acquire(self):
        k = self.k

        class A:
            async def __aenter__(s): return k
            async def __aexit__(s, *e): return False
        return A()


class _Req:
    def __init__(self): self.state = type("S", (), {"user": {"tenant_id": T, "user_id": U}})(); self.headers = {}


def _pasang(monkeypatch, k):
    async def gp(): return _Pool(k)
    monkeypatch.setattr(BR, "get_pool", gp)
    monkeypatch.setattr(BR, "get_user_context", lambda r: {"tenant_id": T, "user_id": U})


def test_izin_cabang_ke_bukan_anggota_ditolak_tanpa_tulis(monkeypatch):
    k = _KB(anggota=False)
    _pasang(monkeypatch, k)
    with pytest.raises(HTTPException) as e:
        _jalan(BR.grant_permission(_Req(), BR_ID, CreateBranchPermissionRequest(user_id=UID, can_edit=True)))
    assert e.value.status_code == 422 and "bukan anggota aktif usaha ini" in e.value.detail
    assert not k.tulis and "tx" not in k.log   # ditolak SEBELUM transaksi/tulis


def test_izin_cabang_ke_anggota_aktif_lanjut(monkeypatch):
    k = _KB(anggota=True)
    _pasang(monkeypatch, k)
    out = _jalan(BR.grant_permission(_Req(), BR_ID, CreateBranchPermissionRequest(user_id=UID, can_edit=True)))
    assert out["success"] is True and any(s.startswith("INSERT INTO audit_logs") for s in k.tulis)


def test_anggota_aktif_memfilter_tenant_dan_status_di_sql():
    from app.services import penawaran_surat as PS
    import inspect
    s = " ".join(inspect.getsource(PS.anggota_aktif).split())
    assert "tenant_id = $1" in s and "user_id = $2::uuid" in s and "upper(COALESCE(status, 'ACTIVE')) = 'ACTIVE'" in s


def test_tak_ada_literal_kosong_yang_hilang_di_sql_rantai():
    """Kutip '' di string SQL berkutip-tunggal hilang diam-diam (tertangkap hanya oleh validasi skema NYATA, 7 Okt): `NULLIF(trim(x), )`."""
    salah = []
    for p in _semua_py():
        if re.search(r"NULLIF\(trim\([^)]*\), \)", open(p, encoding="utf-8").read()):
            salah.append(os.path.relpath(p, APP))
    assert not salah, salah
    # dan rantai dari fungsi bersama benar-benar memuat '' (bukan hilang)
    assert "NULLIF(trim(u.name), '')" in NP.nama_pengguna_sql("p", "u", True) and NP.nama_pengguna_sql("p", "u").endswith("''))")
