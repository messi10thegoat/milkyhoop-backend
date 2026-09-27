"""Void transaksi bank yang SUDAH direkonsiliasi/dicocokkan = 409 (27 Sep 2026, tiket FE Law 34; pola Xero/QBO).
Keadaan: bank_transactions.is_reconciled (sesi selesai) / matched_statement_line_id (dicocokkan, sesi berjalan)
(ditulis routers/bank_reconciliation.py). Lapis kode = 409 terbaca di 8 void utama; lapis DB V320 = semua jalur."""
import ast
import os
import uuid

import pytest
from fastapi import HTTPException

from app.services import jaga_rekonsiliasi as J

TENANT = "kaos-biru-konveksi"
DOK = uuid.uuid4()
APP = os.path.dirname(os.path.dirname(J.__file__))
MIG = os.path.join(os.path.dirname(os.path.dirname(APP)), "migrations", "V320__cegah_void_bank_terekonsiliasi.sql")


class _Conn:
    def __init__(self, rekon, cocok):
        self.r, self.q = {"rekon": rekon, "cocok": cocok}, []

    async def fetchrow(self, q, *a):
        self.q.append((q, a))
        return self.r


@pytest.mark.asyncio
async def test_terekonsiliasi_409():
    with pytest.raises(HTTPException) as e:
        await J.tolak_void_bila_terekonsiliasi(_Conn(1, 0), TENANT, DOK)
    assert e.value.status_code == 409 and e.value.detail["code"] == "BANK_TX_RECONCILED"
    assert "rekonsiliasi" in e.value.detail["message"]


@pytest.mark.asyncio
async def test_dicocokkan_409_dengan_jalan_keluar():
    with pytest.raises(HTTPException) as e:
        await J.tolak_void_bila_terekonsiliasi(_Conn(0, 2), TENANT, DOK)
    assert e.value.status_code == 409 and e.value.detail["code"] == "BANK_TX_MATCHED"
    assert "Lepaskan pencocokannya" in e.value.detail["message"]


@pytest.mark.asyncio
async def test_rekon_menang_atas_cocok():
    with pytest.raises(HTTPException) as e:
        await J.tolak_void_bila_terekonsiliasi(_Conn(1, 1), TENANT, DOK)
    assert e.value.detail["code"] == "BANK_TX_RECONCILED"


@pytest.mark.asyncio
async def test_belum_rekon_lolos_dan_sql_bertenant():
    c = _Conn(0, 0)
    assert await J.tolak_void_bila_terekonsiliasi(c, TENANT, DOK) is None
    q, a = c.q[0]
    sq = " ".join(q.split())
    assert a == (TENANT, str(DOK))
    assert "JOIN journal_entries je ON je.id = bt.journal_id AND je.tenant_id = bt.tenant_id" in sq
    assert "WHERE bt.tenant_id = $1 AND je.source_id = $2::uuid AND je.reversed_by_id IS NULL AND je.reversal_of_id IS NULL" in sq
    assert "COALESCE(bt.is_reconciled, false)" in sq and "bt.matched_statement_line_id IS NOT NULL" in sq


# (berkas, rute void, kunci lock, variabel id) — daftar literal, bukan dibaca dari modul
TITIK = [
    ("receive_payments", '"/{payment_id}/void"', "RECEIVE_PAYMENT_VOID:", "payment_id"),
    ("bill_payments", '"/{payment_id}/void"', "BILL_PAYMENT_VOID:", "payment_id"),
    ("expenses", '"/{expense_id}/void"', "EXPENSE_VOID:", "expense_id"),
    ("kasbank_v2", '"/bank-transactions/{transaction_id}/void"', "BANK_TX_VOID:", "transaction_id"),
    ("bank_transfers", '"/{transfer_id}/void"', "BANK_TRANSFER_VOID:", "transfer_id"),
    ("customer_deposits", '"/{deposit_id}/void"', "DEPOSIT:", "deposit_id"),
    ("vendor_deposits", '"/{deposit_id}/void"', "VENDOR_DEPOSIT_VOID:", "deposit_id"),
    ("sales_receipts", '"/{receipt_id}/void"', "SALES_RECEIPT_VOID:", "receipt_id"),
]


def _fungsi_void(src, rute):
    t = ast.parse(src)
    for n in ast.walk(t):
        if isinstance(n, ast.AsyncFunctionDef):
            for d in n.decorator_list:
                if isinstance(d, ast.Call) and d.args and ast.get_source_segment(src, d.args[0]) == rute \
                        and getattr(d.func, "attr", "") == "post":
                    return n
    raise AssertionError(f"rute {rute} tak ditemukan")


@pytest.mark.parametrize("berkas,rute,kunci,var", TITIK)
def test_void_memeriksa_sesudah_lock_sebelum_tulisan(berkas, rute, kunci, var):
    src = open(os.path.join(APP, "routers", berkas + ".py")).read()
    fn = _fungsi_void(src, rute)
    panggil = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
               and getattr(n.func, "id", "") == "tolak_void_bila_terekonsiliasi"]
    assert len(panggil) == 1, berkas
    c = panggil[0]
    assert ast.get_source_segment(src, c.args[1]) == 'ctx["tenant_id"]' and ast.get_source_segment(src, c.args[2]) == var
    lock = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and "pg_advisory_xact_lock" in n.value]
    kunci_baris = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.JoinedStr)
                   and kunci in ast.get_source_segment(src, n)]
    doc = fn.body[0].value if isinstance(fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant) else None
    tulis = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str) and n is not doc
             and any(k in n.value.upper() for k in ("UPDATE ", "INSERT INTO"))]
    assert lock and kunci_baris and min(kunci_baris) < c.lineno, (berkas, "harus SESUDAH lock (Law 13)")
    assert not tulis or c.lineno < min(tulis), (berkas, "harus SEBELUM tulisan pertama", min(tulis))


def test_migrasi_trigger_menjaga_semua_pembalikan():
    s = " ".join(open(MIG).read().split())
    assert "BEFORE UPDATE OF reversed_by_id ON journal_entries" in s
    assert "WHEN (OLD.reversed_by_id IS NULL AND NEW.reversed_by_id IS NOT NULL)" in s
    assert "WHERE bt.journal_id = OLD.id AND bt.tenant_id = OLD.tenant_id" in s
    assert "'BANK_TX_RECONCILED:" in s and "'BANK_TX_MATCHED:" in s
