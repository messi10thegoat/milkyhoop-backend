"""Kasbon (employee_advances) + cermin bank (FIX_R9_KASBON_MIRROR, 30 Sep 2026).

Dulu grant/void kasbon mengkredit/mendebit akun kas-bank TANPA bank_transactions -> gap R9 (grapgrap BCA
Pengeluaran -1.600.000, diperbaiki data 30 Sep). Kini: akun sumber tertaut rekening -> cermin withdrawal
(grant) / pembalik (void) lewat helper bank_sync, di transaksi yang sama. Kasbon SALDO AWAL (opening_balance)
= Cr Modal Saldo Awal, tanpa rekening, tanpa cermin. principal Decimal (Law 25).
"""
import os
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from app.routers import employee_advances as EA  # noqa: E402

T = "grapgrap-manado"
EMP = UUID("11111111-0000-0000-0000-000000000001")
KASBON = UUID("22222222-0000-0000-0000-000000000002")     # akun EMPLOYEE_ADVANCE
EKUITAS = UUID("33333333-0000-0000-0000-000000000003")    # EQUITY_OPENING_BALANCE
BANK_COA = UUID("44444444-0000-0000-0000-000000000004")   # 1-10202, tertaut bank_accounts
KAS_COA = UUID("55555555-0000-0000-0000-000000000005")    # kas tanpa baris bank_accounts
BA = UUID("66666666-0000-0000-0000-000000000006")
ADV = UUID("77777777-0000-0000-0000-000000000007")
GRANT_J = UUID("88888888-0000-0000-0000-000000000008")
BT = UUID("99999999-0000-0000-0000-000000000009")


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *e):
        return False


class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *e):
        return False


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        return _Acq(self.c)


class _C:
    def __init__(self, grant_bercermin=True, sumber=BANK_COA):
        self.grant_bercermin, self.sumber = grant_bercermin, sumber
        self.baris = []

    def transaction(self):
        return _Tx()

    async def execute(self, q, *a):
        if "INSERT INTO journal_lines" in q:
            # sisi dari SQL: "...,$4,0,$5)" = debit; "...,0,$4,$5)" = kredit. baris = (akun, debit, kredit, memo)
            q1 = " ".join(q.split())
            debit = "$4,0,$5)" in q1
            assert debit or "0,$4,$5)" in q1, q1
            self.baris.append((a[2], a[3] if debit else 0, 0 if debit else a[3], a[4]))

    async def fetchrow(self, q, *a):
        if "FROM chart_of_accounts" in q:
            return {"id": a[0], "name": "akun", "account_code": "1-10202"}
        if "FROM employees" in q:
            return {"id": EMP, "name": "Suryani"}
        if "FROM bank_accounts WHERE coa_id" in q:
            return {"id": BA} if a[0] == BANK_COA else None
        if "FROM employee_advances" in q:
            return {"id": ADV, "employee_id": EMP, "principal": Decimal("500000.00"), "status": "active",
                    "grant_journal_id": GRANT_J, "source_account_id": self.sumber, "granted_date": date(2026, 9, 1),
                    "advance_number": "KSB-2609-0001"}
        if "FROM bank_transactions" in q:
            assert a == (GRANT_J, T)
            return ({"id": BT, "bank_account_id": BA, "amount": Decimal("-500000.00"), "account_name": "BCA"}
                    if self.grant_bercermin else None)
        raise AssertionError(q[:60])

    async def fetchval(self, q, *a):
        if "employee_advance_balance" in q:
            return Decimal("500000.00")
        if "SUM(amount)" in q and "employee_advance_movements" in q:
            return Decimal("0")  # sisa kasbon karyawan sebelum (rencana grant, V334)
        if "employee_advance_movements" in q:
            return UUID(int=1)
        if "generate_employee_advance_number" in q:
            return "KSB-2609-0009"
        raise AssertionError(q[:60])


@pytest.fixture
def pasang(monkeypatch):
    rekam = {"cermin": [], "balik": []}

    def _p(c):
        async def pool():
            return _Pool(c)
        monkeypatch.setattr(EA, "get_pool", pool)

        async def peran(conn, tid, role):
            return {"EMPLOYEE_ADVANCE": KASBON, "EQUITY_OPENING_BALANCE": EKUITAS}[role]
        monkeypatch.setattr(EA, "resolve_account_id_by_role", peran)

        async def cakupan(*a, **k):
            return True
        import app.services.pay_group_access as PGA
        monkeypatch.setattr(PGA, "employee_in_scope", cakupan)

        async def nop(*a, **k):
            return None
        monkeypatch.setattr(EA, "check_period_is_open", nop)

        async def hari(*a, **k):
            return date(2026, 9, 30)
        monkeypatch.setattr(EA, "tanggal_dokumen", hari)

        async def cermin(conn, **kw):
            rekam["cermin"].append(kw)
            return UUID(int=2)
        monkeypatch.setattr(EA, "create_bank_transaction_for_journal", cermin)

        async def balik(conn, **kw):
            rekam["balik"].append(kw)
            return UUID(int=3)
        monkeypatch.setattr(EA, "create_reversal_bank_transaction", balik)
        return c
    _p.rekam = rekam
    return _p


def _req():
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": "00000000-0000-0000-0000-0000000000a1", "tenant_id": T}),
                           headers={})


async def _beri(**kw):
    d = dict(employee_id=EMP, principal=Decimal("500000"), granted_date=date(2026, 9, 1), source_account_id=BANK_COA)
    d.update(kw)
    return await EA.grant_advance(_req(), EA.GrantAdvanceRequest(**d))


@pytest.mark.asyncio
async def test_grant_dari_rekening_bercermin_withdrawal(pasang):
    c = pasang(_C())
    r = await _beri(principal=Decimal("150000.50"))
    [k] = pasang.rekam["cermin"]
    assert k["bank_account_id"] == BA and k["transaction_type"] == "withdrawal"
    assert k["amount"] == Decimal("-150000.50") and isinstance(k["amount"], Decimal)
    assert k["reference_type"] == "employee_advance" and k["reference_id"] == UUID(r["advance_id"])
    # V334: nomor rujukan mutasi = nomor dokumen kasbon (KSB-…), bukan nomor jurnal internal
    assert str(k["journal_id"]) == r["journal_id"] and k["reference_number"] == r["advance_number"] == "KSB-2609-0009"
    assert k["payee_payer"] == "Suryani" and k["transaction_date"] == date(2026, 9, 1)
    # baris jurnal: Dr kasbon / Cr akun bank, jumlah Decimal PERSIS
    assert [b[:3] for b in c.baris] == [(KASBON, Decimal("150000.50"), 0), (BANK_COA, 0, Decimal("150000.50"))]
    assert r["principal"] == 150000.5


@pytest.mark.asyncio
async def test_grant_kas_tanpa_rekening_tanpa_cermin(pasang):
    pasang(_C())
    await _beri(source_account_id=KAS_COA)
    assert pasang.rekam["cermin"] == []


@pytest.mark.asyncio
async def test_kasbon_saldo_awal_ke_modal_saldo_awal_tanpa_cermin(pasang):
    c = pasang(_C())
    await _beri(source_account_id=None, opening_balance=True)
    assert pasang.rekam["cermin"] == []
    assert c.baris[1] == (EKUITAS, 0, Decimal("500000.00"), "Kasbon saldo awal")


@pytest.mark.asyncio
async def test_saldo_awal_dengan_akun_sumber_ditolak_dan_tanpa_sumber_wajib(pasang):
    pasang(_C())
    with pytest.raises(HTTPException) as e:
        await _beri(opening_balance=True)
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        await _beri(source_account_id=None)
    assert e.value.status_code == 422


def test_principal_decimal_bukan_float():
    b = EA.GrantAdvanceRequest(employee_id=EMP, principal="100000.005", granted_date=date(2026, 9, 1), source_account_id=BANK_COA)
    assert isinstance(b.principal, Decimal)
    with pytest.raises(ValidationError):
        EA.GrantAdvanceRequest(employee_id=EMP, principal="0", granted_date=date(2026, 9, 1), source_account_id=BANK_COA)


@pytest.mark.asyncio
async def test_pembulatan_half_up_ke_sen(pasang):
    c = pasang(_C())
    await _beri(principal=Decimal("100000.005"))
    assert c.baris[0][1] == Decimal("100000.01") and pasang.rekam["cermin"][0]["amount"] == Decimal("-100000.01")


@pytest.mark.asyncio
async def test_void_grant_bercermin_membuat_pembalik(pasang):
    pasang(_C(grant_bercermin=True))
    r = await EA.void_advance(_req(), ADV, EA.VoidAdvanceRequest(reason="salah input"))
    [k] = pasang.rekam["balik"]
    assert k["original_bank_transaction_id"] == BT and str(k["reversal_journal_id"]) == r["reversal_journal_id"]
    assert k["tenant_id"] == T and k["description_prefix"] == "[VOID]"


@pytest.mark.asyncio
async def test_void_grant_lama_tanpa_cermin_tanpa_pembalik(pasang):
    c = pasang(_C(grant_bercermin=False))
    await EA.void_advance(_req(), ADV, EA.VoidAdvanceRequest(reason="x"))
    assert pasang.rekam["balik"] == []
    assert [b[:3] for b in c.baris] == [(BANK_COA, Decimal("500000.00"), 0), (KASBON, 0, Decimal("500000.00"))]
