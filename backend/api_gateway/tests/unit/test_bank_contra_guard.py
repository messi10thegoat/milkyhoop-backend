"""Gerbang dua sisi (a)+(ii). RED pada kode belum-dipatch, GREEN sesudah.

(a) create_manual_transaction: contra == CoA bank -> HTTP 400 (RED-discriminating);
    contra != bank -> lewat guard, sampai fetchrow fiscal_periods -> _Sentinel (GREEN).
(ii) adjust_bank_balance: 400 sementara SEBELUM get_pool (get_pool diganti agar meledak
    jika tercapai -> membuktikan 400 mendahului kerja DB).
"""
import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import bank_accounts as ba_mod
from app.routers.bank_accounts import adjust_bank_balance, create_manual_transaction
from app.schemas.bank_accounts import AdjustBalanceRequest, CreateManualTransactionRequest

TENANT = "gate-tenant"
USER = str(uuid.uuid4())
BANK_ACCT_ID = uuid.uuid4()
BANK_COA = uuid.uuid4()
OTHER_COA = uuid.uuid4()


def _req():
    return SimpleNamespace(
        state=SimpleNamespace(user={"tenant_id": TENANT, "user_id": USER})
    )


class _Sentinel(Exception):
    pass


class _Txn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def transaction(self):
        return _Txn()

    async def execute(self, *a, **k):
        return None

    async def fetchrow(self, sql, *args):
        s = " ".join(str(sql).split())
        if "FROM bank_accounts" in s:
            return {
                "id": args[0],
                "coa_id": BANK_COA,
                "account_name": "BCA Gate",
                "is_active": True,
            }
        if "chart_of_accounts" in s:
            return {"id": args[0], "account_type": "EQUITY"}  # lolos Law 29
        if "fiscal_periods" in s:
            # panggilan DB pertama SESUDAH guard -> sampai sini = guard dilewati.
            # HTTPException 418 lolos lewat `except HTTPException: raise` handler
            # (Exception biasa akan dibungkus jadi 500 oleh except-all).
            raise HTTPException(status_code=418, detail="PAST_GUARD")
        return None

    async def fetchval(self, *a, **k):
        raise HTTPException(status_code=418, detail="PAST_GUARD")


class _Acq:
    async def __aenter__(self):
        return _Conn()

    async def __aexit__(self, *a):
        return False


class _Pool:
    def acquire(self):
        return _Acq()


def _patch_pool(monkeypatch):
    async def fake_get_pool():
        return _Pool()

    monkeypatch.setattr(ba_mod, "get_pool", fake_get_pool)


def _body(contra_id):
    return CreateManualTransactionRequest(
        direction="in",
        amount=1000,
        transaction_date=date(2026, 9, 15),
        description="gate",
        contra_account_id=str(contra_id),
    )


@pytest.mark.asyncio
async def test_a_red_contra_equals_bank(monkeypatch):
    _patch_pool(monkeypatch)
    with pytest.raises(HTTPException) as ei:
        await create_manual_transaction(_req(), BANK_ACCT_ID, _body(BANK_COA))
    assert ei.value.status_code == 400
    assert "tidak boleh sama" in ei.value.detail


@pytest.mark.asyncio
async def test_a_green_contra_differs(monkeypatch):
    _patch_pool(monkeypatch)
    # contra != bank -> harus LEWAT guard, mencapai fetchrow fiscal_periods (418),
    # BUKAN 400 "tidak boleh sama". 418 membuktikan guard tidak salah-picu.
    with pytest.raises(HTTPException) as ei:
        await create_manual_transaction(_req(), BANK_ACCT_ID, _body(OTHER_COA))
    assert ei.value.status_code == 418


@pytest.mark.asyncio
async def test_ii_adjust_rejected_before_pool(monkeypatch):
    async def boom():
        raise AssertionError("get_pool tak boleh tercapai; 400 harus mendahului DB")

    monkeypatch.setattr(ba_mod, "get_pool", boom)
    body = AdjustBalanceRequest(
        adjustment_date=date(2026, 9, 15), adjustment_amount=1000, reason="gate"
    )
    with pytest.raises(HTTPException) as ei:
        await adjust_bank_balance(_req(), BANK_ACCT_ID, body)
    assert ei.value.status_code == 400
    assert "sementara tidak tersedia" in ei.value.detail


def test_transfer_same_bank_rejected():
    """Transfer sumber==tujuan sudah dijaga di skema (model_validator) -> ValidationError.
    Kontrol hijau: sumber != tujuan valid (tak meledak)."""
    from pydantic import ValidationError

    from app.schemas.bank_transfers import CreateBankTransferRequest

    same = str(uuid.uuid4())
    with pytest.raises(ValidationError):
        CreateBankTransferRequest(
            from_bank_id=same,
            to_bank_id=same,
            amount=1000,
            transfer_date=date(2026, 9, 15),
        )
    ok = CreateBankTransferRequest(
        from_bank_id=str(uuid.uuid4()),
        to_bank_id=str(uuid.uuid4()),
        amount=1000,
        transfer_date=date(2026, 9, 15),
    )
    assert ok.from_bank_id != ok.to_bank_id
