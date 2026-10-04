"""U3b BE (4 Okt 2026, MASTER GO): void penerimaan = SATU penentu + SATU penulis untuk /void dan /void/preview,
GET /{id}/history, idempotensi X-Idempotency-Key di /void, kunci DEPOSIT dalam penentu. Tanpa DB; perilaku nyata
(paritas pratinjau <=> void atas 40 pembayaran kaos, replay, nol jejak, dua sesi kunci) = harness kaos rollback."""
import asyncio
import inspect
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.routers import receive_payments as RP

MW = Path(RP.__file__).parents[1] / "middleware" / "permission_middleware.py"
INV = Path(__file__).with_name("inventaris_rute_tulis.txt")


def test_rute_terpasang():
    nama = {(r.path, tuple(sorted(r.methods))): r.endpoint.__name__ for r in RP.router.routes}
    assert nama[("/{payment_id}/void", ("POST",))] == "void_receive_payment"
    assert nama[("/{payment_id}/void/preview", ("POST",))] == "preview_void_receive_payment"
    assert nama[("/{payment_id}/history", ("GET",))] == "get_receive_payment_history"


def test_izin_dipetakan():
    src = MW.read_text()
    assert re.search(r'\(r"\^/api/receive-payments/\[\^/\]\+/void/preview\$", \["POST"\], "receive_payment", "V"\)', src)
    assert re.search(r'\(r"\^/api/receive-payments/\[\^/\]\+/history\$", \["GET"\], "receive_payment", "R"\)', src)
    assert "POST /api/receive-payments/{payment_id}/void/preview" in INV.read_text().split("\n")


def test_satu_penentu_satu_penulis():
    st, sp = inspect.getsource(RP.void_receive_payment), inspect.getsource(RP.preview_void_receive_payment)
    for f in ("_rencana_void_pembayaran(", "_tulis_void_pembayaran("):
        assert f in st and f in sp, f
    for kata in ("INSERT INTO journal_entries", "UPDATE receive_payments", "create_reversal_bank_transaction"):
        assert kata not in st and kata not in sp, f"penulis harus satu tempat: {kata}"
    assert "INSERT INTO journal_entries" in inspect.getsource(RP._tulis_void_pembayaran)
    assert "await tr.rollback()" in sp and "finally" in sp, "pratinjau WAJIB rollback"


def test_idempotensi_sebelum_kunci_domain_dan_dicatat():
    src = inspect.getsource(RP.void_receive_payment)
    assert src.index("idem_mulai_aksi(") < src.index("RECEIVE_PAYMENT_VOID:") < src.index("_rencana_void_pembayaran(")
    assert src.index("_tulis_void_pembayaran(") < src.index("idem_simpan(")
    assert "return lama" in src


def test_penentu_mengunci_deposit_sebelum_membaca_pemakaian():
    src = inspect.getsource(RP._rencana_void_pembayaran)
    assert "sorted(" in src, "urutan kunci DEPOSIT terurut (anti-deadlock)"
    assert src.index('f"DEPOSIT:{did}"') < src.index("amount_applied, amount_refunded") < src.index("compute_deposit_remaining_many(")


class _Conn:
    def __init__(self, payment):
        self.payment = payment

    async def fetchrow(self, q, *a):
        return self.payment if "FROM receive_payments" in q else None

    async def fetch(self, q, *a):
        return []

    async def fetchval(self, q, *a):
        return None

    async def execute(self, *a):
        return None


def _patch(monkeypatch, rekon=None, periode=None):
    async def tolak(conn, tenant, pid):
        if rekon:
            raise HTTPException(status_code=409, detail={"code": "BANK_TX_RECONCILED", "message": "sudah rekon"})

    async def hari(conn, tenant):
        import datetime
        return datetime.date(2026, 10, 4)

    async def periode_ok(conn, tenant, tgl):
        if periode:
            raise HTTPException(status_code=400, detail="Periode ditutup")

    monkeypatch.setattr(RP, "tolak_void_bila_terekonsiliasi", tolak)
    monkeypatch.setattr(RP, "tanggal_dokumen", hari)
    monkeypatch.setattr(RP, "check_period_is_open", periode_ok)


CTX = {"tenant_id": "t", "user_id": "u"}


def _p(status, **kw):
    return {"status": status, "created_deposit_id": None, "source_type": "cash", "source_deposit_id": None,
            "journal_id": None, "payment_number": "RCV-1", **kw}


@pytest.mark.asyncio
async def test_blok_dikumpulkan_urutan_galat_lama(monkeypatch):
    _patch(monkeypatch, rekon=True, periode=True)
    r = await RP._rencana_void_pembayaran(_Conn(_p("voided")), CTX, "pid", None, True)
    assert [b["code"] for b in r["blocks"]] == ["VOID_REASON_REQUIRED", "BANK_RECONCILED", "PERIOD_CLOSED", "RP_ALREADY_VOIDED"]
    assert r["blocks"][1]["status"] == 409 and r["blocks"][1]["detail"] == {"code": "BANK_TX_RECONCILED", "message": "sudah rekon"}
    assert r["blocks"][3]["detail"] == "Penerimaan ini sudah dibatalkan."


@pytest.mark.asyncio
async def test_draft_dan_bersih(monkeypatch):
    _patch(monkeypatch)
    r = await RP._rencana_void_pembayaran(_Conn(_p("draft")), CTX, "pid", "x")
    assert [b["code"] for b in r["blocks"]] == ["RP_IS_DRAFT"] and r["blocks"][0]["detail"] == "Penerimaan berstatus Draf tidak dibatalkan — hapus saja."
    r = await RP._rencana_void_pembayaran(_Conn(_p("posted")), CTX, "pid", "x")
    assert r["blocks"] == []


@pytest.mark.asyncio
async def test_tidak_ada_404(monkeypatch):
    _patch(monkeypatch)
    with pytest.raises(HTTPException) as e:
        await RP._rencana_void_pembayaran(_Conn(None), CTX, "pid", "x")
    assert e.value.status_code == 404


def test_riwayat_ada_di_layanan_bersama():
    from app.services import so_riwayat as SR
    assert callable(SR.riwayat_pembayaran) and SR.MODUL_PEMBAYARAN["customer_deposit"] == "customer_deposit"
    assert "riwayat_pembayaran" in inspect.getsource(RP.get_receive_payment_history)
