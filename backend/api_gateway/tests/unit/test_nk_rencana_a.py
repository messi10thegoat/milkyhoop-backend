"""NK-RENCANA-A (23 Okt 2026, desain disetujui MASTER; KONTRAK-nk-rencana-20261023.md): pratinjau apply/unapply/refund nota kredit
= penentu bersama (services/nota_kredit_rencana) di transaksi READ ONLY -- tanpa kunci, tanpa penomoran, nol tulis. Inti tulis memanggil
penentu YANG SAMA sesudah kunci lama; blok pertama = galat lama. Kesetaraan pratinjau == tertulis dan lama == baru dibuktikan harness
scripts/ops/uji-nyata-nk-rencana-a.py di kaos (rollback); di sini: pemindai jalur pratinjau + rumus keadaan sesudah."""
import ast
import asyncio
import inspect
import textwrap
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.routers import credit_notes as C
from app.schemas.credit_notes import RefundCreditNoteRequest
from app.services import nota_kredit_rencana as NKR
from app.services import pihak_helpers as PH


def _kode_tanpa_docstring_nkra(fn) -> str:
    pohon = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    for n in ast.walk(pohon):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)) and n.body and isinstance(n.body[0], ast.Expr) \
                and isinstance(getattr(n.body[0], "value", None), ast.Constant) and isinstance(n.body[0].value.value, str):
            n.body = n.body[1:] or [ast.Pass()]
    return ast.unparse(pohon).upper()


JALUR_PRATINJAU_NKRA = [NKR.pratinjau_aksi_nk, NKR.rencana_terapkan_nk, NKR.rencana_lepas_nk, NKR.rencana_refund_nk, NKR._baca_nk,
                        NKR._sesudah_nk, NKR.kredit_piutang_nk, NKR.status_nk_sesudah, NKR.baris_refund_nk,
                        C._keadaan_nk, C._sisa_faktur_nk, C._saldo_akun_nk, C.tolak_nk_bertaut, C.resolusi_akun_refund,
                        C.pastikan_potongan_muat_sisa, PH.faktur_tenant_untuk_pelanggan, PH.pastikan_cn_muat_faktur, C._pratinjau_nk_aksi]


@pytest.mark.parametrize("fn", JALUR_PRATINJAU_NKRA, ids=lambda f: f.__name__)
def test_jalur_pratinjau_tanpa_kunci_penomoran_tulis(fn):
    k = _kode_tanpa_docstring_nkra(fn)
    for terlarang in ("PG_ADVISORY", "FOR UPDATE", "GET_NEXT_JOURNAL_NUMBER", "GENERATE_CUSTOMER_DEPOSIT_NUMBER", "INSERT INTO",
                      "UPDATE ", "DELETE FROM", "CREATE_BANK_TRANSACTION", "SEGARKAN_CACHE", "_CORE("):
        assert terlarang not in k, (fn.__name__, terlarang)


def test_kontrol_merah_pemindai_menangkap_kunci_dan_tulis():
    async def jahat_nkra(conn):
        """docstring boleh menyebut FOR UPDATE / INSERT INTO tanpa memerahkan."""
        await conn.execute("SELECT pg_advisory_xact_lock(1)")
        await conn.execute("INSERT INTO x VALUES (1)")
    k = _kode_tanpa_docstring_nkra(jahat_nkra)
    assert "PG_ADVISORY" in k and "INSERT INTO" in k and "FOR UPDATE" not in k


def test_pratinjau_transaksi_read_only():
    src = " ".join(inspect.getsource(C._pratinjau_nk_aksi).split())
    assert "conn.transaction(readonly=True)" in src and "_BatalkanPratinjauNk" not in src


@pytest.mark.parametrize("st,total,d,k,hasil", [
    ("posted", 100000, 100000, 0, "applied"), ("posted", 100000, 0, 40000, "partial"), ("partial", 100000, 0, 0, "posted"),
    ("posted", 100000, 0, 100000, "applied"), ("draft", 100000, 100000, 0, "draft"), ("void", 100000, 0, 0, "void"),
    ("posted", Decimal("100000.4"), Decimal("100000.4"), 0, "applied")])
def test_status_sesudah_sama_dengan_trigger(st, total, d, k, hasil):
    assert NKR.status_nk_sesudah(st, total, d, k) == hasil


def test_bigint_pg_membulatkan_setengah_menjauhi_nol():
    assert NKR._bigint_pg(Decimal("10.5")) == 11 and NKR._bigint_pg(Decimal("10.4")) == 10 and NKR._bigint_pg(None) == 0


def test_baris_refund_sama_dengan_penulis_lama():
    ar, kas = uuid.uuid4(), uuid.uuid4()
    assert NKR.baris_refund_nk(ar, kas, Decimal("40000"), "CN-1") == [
        (ar, Decimal("40000"), 0, "Refund Piutang - CN-1"), (kas, 0, Decimal("40000"), "Pembayaran Refund - CN-1")]


class _KonnPv:
    """Koneksi tiruan BACA-SAJA untuk pratinjau refund: execute = galat (jalur pratinjau tak boleh menulis)."""
    def __init__(self, cn):
        self.cn = cn

    async def execute(self, sql, *a):
        raise AssertionError(f"pratinjau menulis: {sql}")

    async def fetchrow(self, sql, *a):
        if "FROM credit_notes" in sql:
            return self.cn
        if "FROM chart_of_accounts" in sql:
            return {"id": a[0], "code": "1-10201", "account_code": "1-10201", "name": "BCA", "account_type": "ASSET",
                    "is_cash": True, "is_bank_account": True}
        if "FROM bank_accounts WHERE id" in sql:
            return {"coa_id": KAS}
        return None

    async def fetchval(self, sql, *a):
        if "SUM(jl.debit) - SUM(jl.credit)" in sql:
            return Decimal("500000")
        if "account_name FROM bank_accounts" in sql:
            return "BCA Operasional"
        return None

    async def fetch(self, sql, *a):
        return []


KAS, BANK, CNID = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def test_pratinjau_refund_rumus_sesudah_jurnal_tanpa_nomor_dan_cermin(monkeypatch):
    async def ar(conn, tid, kode):
        return uuid.uuid4()
    monkeypatch.setattr(C, "resolve_account_id", ar)
    cn = {"id": CNID, "credit_note_number": "CN-9", "customer_id": "c1", "customer_name": "X", "status": "posted",
          "total_amount": Decimal("100000"), "amount_applied": 0, "amount_refunded": 0, "original_invoice_id": None,
          "journal_id": uuid.uuid4(), "created_deposit_id": None, "credit_note_date": date(2026, 10, 1), "reason": "retur"}
    body = RefundCreditNoteRequest(amount=Decimal("40000"), refund_date=date(2026, 10, 5), payment_method="transfer",
                                   bank_account_id=str(BANK))
    d = asyncio.run(NKR.pratinjau_aksi_nk(_KonnPv(cn), {"tenant_id": "t1", "user_id": uuid.uuid4()}, CNID, "refund", body))
    assert d["can_proceed"] is True and d["blocks"] == []
    assert d["after"]["status"] == "partial" and d["after"]["status_detail"] == "partially_refunded"
    assert d["after"]["amount_refunded"] == 40000.0 and d["after"]["remaining"] == 60000.0 and d["before"]["remaining"] == 100000.0
    j = d["journals"][0]
    assert j["journal_number"] is None and j["source_type"] == "CREDIT_NOTE_REFUND" and j["journal_date"] == "2026-10-05"
    assert [(b["debit"], b["credit"]) for b in j["lines"]] == [(40000.0, 0.0), (0.0, 40000.0)]
    assert d["bank"]["balance_before"] == 500000.0 and d["bank"]["balance_after"] == 460000.0
    assert d["bank"]["bank_transactions"] == [{"bank_account_id": str(BANK), "bank_account_name": "BCA Operasional",
                                               "transaction_type": "withdrawal", "amount": -40000.0}]
    assert d["invoice"] is None and d["preview"] is True


def test_pratinjau_terblok_sesudah_sama_dengan_sebelum(monkeypatch):
    cn = {"id": CNID, "credit_note_number": "CN-9", "customer_id": "c1", "customer_name": "X", "status": "draft",
          "total_amount": Decimal("100000"), "amount_applied": 0, "amount_refunded": 0, "original_invoice_id": None,
          "journal_id": None, "created_deposit_id": None, "credit_note_date": date(2026, 10, 1), "reason": "retur"}
    body = RefundCreditNoteRequest(amount=Decimal("40000"), refund_date=date(2026, 10, 5), payment_method="transfer",
                                   bank_account_id=str(BANK))
    d = asyncio.run(NKR.pratinjau_aksi_nk(_KonnPv(cn), {"tenant_id": "t1", "user_id": uuid.uuid4()}, CNID, "refund", body))
    assert d["can_proceed"] is False and [b["code"] for b in d["blocks"]] == ["CN_TAK_BISA_REFUND"]
    assert d["after"] == d["before"] and d["journals"] == []
