"""Gerbang dua sisi (a)+(ii). RED pada kode belum-dipatch, GREEN sesudah.

(a) create_manual_transaction: contra == CoA bank -> HTTP 400 (RED-discriminating);
    contra != bank -> lewat guard, sampai fetchrow fiscal_periods -> _Sentinel (GREEN).
(ii) adjust_bank_balance (dihidupkan lagi 70434814): selisih lebih Dr Bank / Cr GAIN,
    selisih kurang Dr LOSS / Cr Bank, tak pernah nol-bersih akun sama, peran tak terpetakan
    -> 400 tanpa jurnal. (Dulu memaku saklar-mati sementara 15 Sep.)
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


# (ii) /adjust DIHIDUPKAN LAGI di 70434814 (20 Sep, V274): akun lawan = peran per
# tenant BANK_ADJUSTMENT_GAIN (selisih lebih) / BANK_ADJUSTMENT_LOSS (selisih kurang).
# Tes lama memaku SAKLAR-MATI sementara 15 Sep (400 "sementara tidak tersedia") dan
# merah sejak jalur uang ini hidup kembali -- jalur itu berjalan TANPA satu pun tes.
# Tes di bawah memanggil handler SESUNGGUHNYA dan membaca baris jurnal yang ditulisnya.

GAIN_COA = uuid.uuid4()
LOSS_COA = uuid.uuid4()


class _AdjConn:
    """Merekam INSERT; baris jurnal dibaca sisi debit/kredit dari pola VALUES-nya."""

    def __init__(self, ledger_balance):
        self.ledger_balance = ledger_balance
        self.lines = []      # (side, account_id, amount)
        self.headers = []    # (total_debit, total_credit)

    def transaction(self):
        return _Txn()

    async def fetchrow(self, sql, *args):
        return {"id": args[0], "coa_id": BANK_COA, "coa_account_id": BANK_COA,
                "account_name": "BCA Gate", "is_active": True,
                "ledger_balance": self.ledger_balance}

    async def execute(self, sql, *args):
        s = " ".join(str(sql).split())
        if "INSERT INTO journal_entries" in s:
            self.headers.append((args[7], args[7]))
        elif "INSERT INTO journal_lines" in s:
            side = "Dr" if ", $4, 0, $5)" in s else "Cr" if ", 0, $4, $5)" in s else "?"
            self.lines.append((side, args[2], args[3]))
        return None


def _adj_wire(monkeypatch, conn, roles_asked, unmapped=False):
    class _P:
        def acquire(self):
            class _A:
                async def __aenter__(self_):
                    return conn

                async def __aexit__(self_, *a):
                    return False
            return _A()

    async def _pool():
        return _P()

    async def _resolve(c, tenant, role):
        roles_asked.append(role)
        if unmapped:
            raise ba_mod.AccountRoleUnmappedError(role)
        return GAIN_COA if role == ba_mod.AccountRole.BANK_ADJUSTMENT_GAIN else LOSS_COA

    monkeypatch.setattr(ba_mod, "get_pool", _pool)
    monkeypatch.setattr(ba_mod, "resolve_account_id_by_role", _resolve)


def _adj_body(amount):
    return AdjustBalanceRequest(adjustment_date=date(2026, 9, 15),
                                adjustment_amount=amount, reason="gate")


@pytest.mark.asyncio
async def test_ii_adjust_surplus_dr_bank_cr_gain(monkeypatch):
    conn, roles = _AdjConn(ledger_balance=0), []
    _adj_wire(monkeypatch, conn, roles)
    res = await adjust_bank_balance(_req(), BANK_ACCT_ID, _adj_body(1000))
    assert res["success"] is True
    assert roles == [ba_mod.AccountRole.BANK_ADJUSTMENT_GAIN]
    assert conn.lines == [("Dr", BANK_COA, 1000), ("Cr", GAIN_COA, 1000)]
    assert conn.headers == [(1000, 1000)]


@pytest.mark.asyncio
async def test_ii_adjust_shortfall_dr_loss_cr_bank(monkeypatch):
    conn, roles = _AdjConn(ledger_balance=5000), []
    _adj_wire(monkeypatch, conn, roles)
    await adjust_bank_balance(_req(), BANK_ACCT_ID, _adj_body(-1000))
    assert roles == [ba_mod.AccountRole.BANK_ADJUSTMENT_LOSS]
    assert conn.lines == [("Dr", LOSS_COA, 1000), ("Cr", BANK_COA, 1000)]


@pytest.mark.asyncio
async def test_ii_adjust_never_net_zero_same_account(monkeypatch):
    """Cacat yang membuat /adjust dimatikan 15 Sep: Dr bank / Cr bank (nol bersih)."""
    for amt, bal in ((1000, 0), (-1000, 5000)):
        conn, roles = _AdjConn(ledger_balance=bal), []
        _adj_wire(monkeypatch, conn, roles)
        await adjust_bank_balance(_req(), BANK_ACCT_ID, _adj_body(amt))
        accts = [a for _, a, _ in conn.lines]
        assert len(set(accts)) == 2, conn.lines
        assert sum(x for sd, _, x in conn.lines if sd == "Dr") == sum(
            x for sd, _, x in conn.lines if sd == "Cr")


@pytest.mark.asyncio
async def test_ii_adjust_unmapped_role_400_no_journal(monkeypatch):
    conn, roles = _AdjConn(ledger_balance=0), []
    _adj_wire(monkeypatch, conn, roles, unmapped=True)
    with pytest.raises(HTTPException) as ei:
        await adjust_bank_balance(_req(), BANK_ACCT_ID, _adj_body(1000))
    assert ei.value.status_code == 400
    assert "belum diatur" in ei.value.detail
    assert conn.lines == [] and conn.headers == []


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
