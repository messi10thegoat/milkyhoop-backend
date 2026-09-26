"""Uang muka ber-proforma_id divalidasi (26 Sep 2026; prasyarat CW Terima DP).

Dulu POST /api/customer-deposits hanya bergantung FK: proforma SO lain / pelanggan lain / draf / batal / TENANT
LAIN diterima & diatribusikan eksplisit (proforma tenant lain -> SO acuan None -> pagar plafon dilewati).
Kini 422 kecuali: tenant sama (filter SQL eksplisit), SO sama (bila dikirim), pelanggan sama, status issued.
Koneksi tiruan MENERAPKAN predikat tenant hanya bila SQL memuatnya -> menghapus filter = baris tenant lain lolos.
Prod 26 Sep (baca-saja): 4 DP ber-proforma (kaos), 0 beda tenant/SO/pelanggan; 3 proforma kini batal, DP-nya void.
"""
import ast
import uuid

import pytest
from fastapi import HTTPException

import app.routers.customer_deposits as CD
from app.schemas.customer_deposits import UpdateCustomerDepositRequest

T, LAIN = "kaos-biru-konveksi", "grapgrap-manado"
SO, SO2 = uuid.uuid4(), uuid.uuid4()
CUST, CUST2 = uuid.uuid4(), uuid.uuid4()
PF, PF_LAIN = uuid.uuid4(), uuid.uuid4()


class Conn:
    def __init__(self, status="issued"):
        self.rows = {PF: {"tenant_id": T, "sales_order_id": SO, "customer_id": CUST, "status": status},
                     PF_LAIN: {"tenant_id": LAIN, "sales_order_id": SO2, "customer_id": CUST2, "status": "issued"}}
        self.sql = []

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        assert "FROM proformas" in sql
        r = self.rows.get(a[0])
        if r is None:
            return None
        if "tenant_id = $2" in " ".join(sql.split()) and r["tenant_id"] != a[1]:
            return None
        return {k: v for k, v in r.items() if k != "tenant_id"}


async def _v(conn, pid, so=None, cust=str(CUST)):
    return await CD.validasi_proforma_dp(conn, T, pid, so, cust)


@pytest.mark.asyncio
async def test_proforma_sah_lolos():
    await _v(Conn(), str(PF), str(SO))
    await _v(Conn(), str(PF), None)           # SO diturunkan dari proforma
    await _v(Conn(), None, None, None)        # tanpa proforma = perilaku lama


@pytest.mark.asyncio
@pytest.mark.parametrize("kasus,kode", [
    ("tenant_lain", "PROFORMA_TIDAK_VALID"),
    ("tak_ada", "PROFORMA_TIDAK_VALID"),
    ("bukan_uuid", "PROFORMA_TIDAK_VALID"),
    ("draf", "PROFORMA_BUKAN_TERBIT"),
    ("batal", "PROFORMA_BUKAN_TERBIT"),
    ("so_lain", "PROFORMA_BEDA_PESANAN"),
    ("pelanggan_lain", "PROFORMA_BEDA_PELANGGAN"),
    ("tanpa_pelanggan", "PROFORMA_BEDA_PELANGGAN"),
])
async def test_proforma_tak_sah_422(kasus, kode):
    c = Conn(status={"draf": "draft", "batal": "cancelled"}.get(kasus, "issued"))
    arg = {"tenant_lain": (str(PF_LAIN), None, str(CUST2)), "tak_ada": (str(uuid.uuid4()), None, str(CUST)),
           "bukan_uuid": ("PRO-2609-0059", None, str(CUST)), "so_lain": (str(PF), str(SO2), str(CUST)),
           "pelanggan_lain": (str(PF), str(SO), str(CUST2)), "tanpa_pelanggan": (str(PF), str(SO), None)}.get(
        kasus, (str(PF), str(SO), str(CUST)))
    with pytest.raises(HTTPException) as e:
        await CD.validasi_proforma_dp(c, T, *arg)
    assert e.value.status_code == 422 and e.value.detail["code"] == kode


@pytest.mark.asyncio
async def test_tenant_lain_tak_dibedakan_dari_tak_ada():
    pesan = []
    for pid in (str(PF_LAIN), str(uuid.uuid4())):
        with pytest.raises(HTTPException) as e:
            await CD.validasi_proforma_dp(Conn(), T, pid, None, str(CUST2))
        pesan.append(e.value.detail)
    assert pesan[0] == pesan[1]


def _fn(nama):
    for n in ast.walk(ast.parse(open(CD.__file__, encoding="utf-8").read())):
        if isinstance(n, ast.AsyncFunctionDef) and n.name == nama:
            return n
    raise AssertionError(nama)


def test_create_memanggil_validasi_sebelum_insert():
    fn = _fn("create_customer_deposit")
    p = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and getattr(c.func, "id", None) == "validasi_proforma_dp"]
    assert len(p) == 1
    assert [ast.unparse(x) for x in p[0].args] == ["conn", "ctx['tenant_id']", "body.proforma_id",
                                                  "body.sales_order_id", "pelanggan_dp"]
    ins = min(c.lineno for c in ast.walk(fn) if isinstance(c, ast.Call) and c.args
              and isinstance(c.args[0], ast.Constant) and "INSERT INTO customer_deposits" in str(c.args[0].value))
    assert p[0].lineno < ins


def test_patch_tak_menerima_proforma_id():
    assert "proforma_id" not in UpdateCustomerDepositRequest.model_fields
    assert "sales_order_id" not in UpdateCustomerDepositRequest.model_fields
