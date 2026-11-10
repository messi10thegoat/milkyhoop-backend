"""LAMPIRAN-BACA-KASBANK (10 Nov 2026, tiket MASTER): GET /api/lampiran/{bank_transaction|bank_transfer|employee_advance}/{id} ->
FE punya SATU pembaca lampiran. Dijaga: izin rute (kas_bank R / payroll R), url grup = rute unduh pabrik, SQL bertenant di tiap tabel
dan entity_type = spesifikasi pabrik, induk/pagar SAMA dengan pabrik (lintas tenant 404, kasbon luar pay-group 404 TANPA kueri baris),
bentuk keluaran sama (mode dokumen), jenis lama tak berubah."""
import asyncio
import os
import re
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

from app.middleware import permission_middleware as pm
from app.routers import lampiran_dokumen as LD
from app.routers import lampiran_induk as LI
from app.routers import sales_order_lampiran as SL
from tests.unit.test_lampiran_so_keluarga import PEPalsu, _pemenang, baris

T = "kaos-biru-konveksi"
DOK = uuid.uuid4()
TIGA = ("bank_transaction", "bank_transfer", "employee_advance")


@pytest.mark.parametrize("jenis,harap", [("bank_transaction", ("kas_bank", "R")), ("bank_transfer", ("kas_bank", "R")),
                                         ("employee_advance", ("payroll", "R"))])
def test_izin_rute_dan_izin_grup(jenis, harap):
    assert pm.izin_untuk_jalur(f"/api/lampiran/{jenis}/{DOK}") == harap
    assert pm.izin_untuk_jalur(SL.jalur_izin_jenis(jenis)) == harap


@pytest.mark.parametrize("jenis,entitas", [("bank_transaction", "bank_transaction"), ("bank_transfer", "bank_transfer"),
                                           ("employee_advance", "employee_advance")])
def test_url_grup_ke_rute_unduh_pabrik(jenis, entitas):
    r = _pemenang(SL.url_unduh_lampiran(SL.JENIS_KELUARGA[jenis][0], uuid.uuid4(), uuid.uuid4()))
    assert r is not None and r.endpoint.__module__.rsplit(".", 1)[-1] == "lampiran_induk"
    assert r.endpoint.__name__ == f"unduh_lampiran_{entitas}"


def test_spesifikasi_sama_dengan_pabrik():
    assert {j: s for j, (s, _) in LD.JENIS_TUNGGAL.items()} == {
        "bank_transaction": LI.SPEC_MUTASI_BANK, "bank_transfer": LI.SPEC_TRANSFER_BANK, "employee_advance": LI.SPEC_KASBON}
    assert LD.JENIS_TUNGGAL["employee_advance"][0].pra_cek_dok is LI._kasbon_dalam_pay_group
    assert not set(LD.JENIS_TUNGGAL) & (set(LD.JENIS_DOKUMEN) | set(LD.JENIS_PEMBELIAN))


@pytest.mark.parametrize("jenis", TIGA)
def test_sql_bertenant_tiap_tabel(jenis):
    s, tgl = LD.JENIS_TUNGGAL[jenis]
    q = " ".join(LD.sql_tunggal(jenis).split())
    assert f"WHERE {s.tabel}.id = $1 AND {s.tabel}.tenant_id = $2" in q
    assert f"da.entity_type = '{s.entitas}' AND da.entity_id = {s.tabel}.id AND da.tenant_id = $2" in q
    assert "d.tenant_id = $2 AND d.deleted_at IS NULL" in q and f"{s.tabel}.{tgl}::date AS tanggal" in q
    assert f"FROM {s.tabel} JOIN document_attachments da" in q  # tanpa alias: NOMOR_MUTASI_BANK merujuk nama tabel
    # setiap tabel yang dibaca (termasuk subkueri nomor dari spesifikasi pabrik, mis. journal_entries) bertenant
    for tabel, alias in re.findall(r"\bFROM\s+(\w+)(?:\s+(\w+))?\s+WHERE", q):
        assert re.search(rf"\b{alias or tabel}\.tenant_id = (\$2|{s.tabel}\.tenant_id)", q), (tabel, q)


class Kon:
    def __init__(self, induk=True, dalam_pg=True, rows=()):
        self.induk, self.dalam_pg, self.rows, self.kueri = induk, dalam_pg, list(rows), []

    def transaction(self):
        kon = self

        class Tx:
            async def __aenter__(self):
                return kon

            async def __aexit__(self, *e):
                return False
        return Tx()

    async def execute(self, sql, *a):
        return None

    async def fetchrow(self, sql, *a):
        self.kueri.append(("induk", sql, a))
        return {"id": a[0], "nomor": "KB-0001"} if self.induk else None

    async def fetch(self, sql, *a):
        self.kueri.append(("baris", sql, a))
        return self.rows


def jalankan(monkeypatch, kon, jenis, dalam_pg=True):
    async def gp():
        return SimpleNamespace(acquire=lambda: _Acq(kon))

    async def lingkup(conn, tid, uid, emp):
        return dalam_pg
    monkeypatch.setattr(SL, "get_pool", gp)
    monkeypatch.setattr(pm, "get_policy_engine", lambda: PEPalsu())
    monkeypatch.setattr("app.services.pay_group_access.employee_in_scope", lingkup)
    req = SimpleNamespace(state=SimpleNamespace(user={"user_id": "u1", "tenant_id": T, "role": "ADMIN"}))
    return asyncio.run(LD.lampiran_dokumen(req, jenis, str(DOK)))


class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


@pytest.mark.parametrize("jenis", TIGA)
def test_bentuk_keluaran_mode_dokumen(monkeypatch, jenis):
    a1, a2 = uuid.uuid4(), uuid.uuid4()
    kon = Kon(rows=[baris(jenis, DOK, "KB-0001", a1, "struk.png"), baris(jenis, DOK, "KB-0001", a2, "nota.pdf", storage="local")])
    kon.employee = uuid.uuid4()

    async def fetchval(sql, *a):  # kasbon: karyawan dokumen untuk pagar pay-group
        assert "FROM employee_advances WHERE id = $1 AND tenant_id = $2" in sql
        return kon.employee
    kon.fetchval = fetchval
    out = jalankan(monkeypatch, kon, jenis)
    s, _ = LD.JENIS_TUNGGAL[jenis]
    assert kon.kueri[0][1] == LI.sql_spec(s)["induk"] and kon.kueri[1][1] == LD.sql_tunggal(jenis)
    assert kon.kueri[1][2] == (DOK, T)
    assert out["sumber"] == {"jenis": jenis, "id": str(DOK), "nomor": "KB-0001", "mode": "dokumen", "sales_order": None}
    assert out["jumlah"] == 2 and len(out["data"]) == 1
    g = out["data"][0]
    assert g["dokumen"]["jenis"] == jenis and g["dokumen"]["nomor"] == "KB-0001"
    lp = {x["display_name"]: x for x in g["lampiran"]}
    assert lp["struk.png"]["url"] == f"/api/{s.modul_url}/{DOK}/attachments/{a1}/download" and lp["struk.png"]["tersedia"] is True
    assert lp["nota.pdf"]["url"] is None and lp["nota.pdf"]["tersedia"] is False


@pytest.mark.parametrize("jenis", TIGA)
def test_lintas_tenant_404_tanpa_kueri_baris(monkeypatch, jenis):
    kon = Kon(induk=False)
    with pytest.raises(HTTPException) as e:
        jalankan(monkeypatch, kon, jenis)
    assert e.value.status_code == 404 and [k for k, *_ in kon.kueri] == ["induk"]


def test_kasbon_luar_pay_group_404_tanpa_kueri_baris(monkeypatch):
    kon = Kon(rows=[baris("employee_advance", DOK, "KB-0001", uuid.uuid4(), "x.png")])

    async def fetchval(sql, *a):
        return uuid.uuid4()
    kon.fetchval = fetchval
    with pytest.raises(HTTPException) as e:
        jalankan(monkeypatch, kon, "employee_advance", dalam_pg=False)
    assert e.value.status_code == 404 and [k for k, *_ in kon.kueri] == ["induk"]


def test_izin_grup_dicabut_tersembunyi(monkeypatch):
    kon = Kon(rows=[baris("bank_transfer", DOK, "TRF-1", uuid.uuid4(), "bukti.png")])

    async def gp():
        return SimpleNamespace(acquire=lambda: _Acq(kon))
    monkeypatch.setattr(SL, "get_pool", gp)
    monkeypatch.setattr(pm, "get_policy_engine", lambda: PEPalsu(tolak={"kas_bank"}))
    req = SimpleNamespace(state=SimpleNamespace(user={"user_id": "u1", "tenant_id": T, "role": "ADMIN"}))
    out = asyncio.run(LD.lampiran_dokumen(req, "bank_transfer", str(DOK)))
    assert out["data"] == [{"dokumen": {"jenis": "bank_transfer"}, "tersembunyi_izin": True, "jumlah": 1}]
    assert "bukti.png" not in repr(out)


def test_penanda():
    assert LD.PENANDA_LAMPIRAN_KASBANK == "lampiran-baca-kasbank-kasbon-w20261110"
