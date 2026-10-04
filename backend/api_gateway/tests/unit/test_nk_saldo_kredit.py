"""PUTUSAN PEMILIK 30 Sep 2026 (langsung di sesi BACKEND): "batas nota kredit = nilai faktur dikurangi nota kredit lain;
kelebihan jadi saldo kredit pelanggan".

- pastikan_cn_muat_faktur: batas = nilai faktur - SIGMA NK tak-void lain atas faktur itu (NK ini dikecualikan); dulu sisa
  tagihan -> retur atas faktur LUNAS ditolak.
- posting_nota_kredit: Cr Piutang PALING BANYAK sisa tagihan; kelebihan -> Cr Uang Muka Pelanggan + customer_deposits
  (KRD) + credit_notes.created_deposit_id. Sisa uang muka (journal-derived) mengenal jurnal NK itu.
- void NK: uang muka ikut void; sudah dipakai -> 400.
Uji nyata salinan DB: X1 faktur lunas (seluruhnya saldo kredit), X2 terbelah, X3 void, X4 void ditolak, X6 batas.
"""
import ast
import os
from decimal import Decimal as D
from pathlib import Path
from uuid import UUID

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.services import pihak_helpers as PH  # noqa: E402
from app.routers import credit_notes as CN, customer_deposits as CD  # noqa: E402

F = {"id": UUID(int=5), "status": "paid", "journal_id": UUID(int=6)}


class _C:
    def __init__(self, nilai, lain):
        self.nilai, self.lain, self.q = nilai, lain, []

    async def fetchval(self, sql, *a):
        self.q.append((" ".join(sql.split()), a))
        if "SELECT total_amount FROM sales_invoices" in sql:
            return self.nilai
        if "FROM credit_notes" in sql:
            assert "status <> 'void'" in sql and "id <> $3::uuid" in sql
            return self.lain
        raise AssertionError(sql)


@pytest.mark.asyncio
async def test_batas_nilai_faktur_dikurangi_nk_lain_bukan_sisa_tagihan():
    await PH.pastikan_cn_muat_faktur(_C(D("200000"), D("0")), "t", F, D("200000"))      # faktur LUNAS: boleh penuh
    await PH.pastikan_cn_muat_faktur(_C(D("200000"), D("50000")), "t", F, D("150000"))
    with pytest.raises(HTTPException) as e:
        await PH.pastikan_cn_muat_faktur(_C(D("200000"), D("50000")), "t", F, D("150001"))
    assert e.value.status_code == 400 and "dikurangi nota kredit lain" in e.value.detail
    c = _C(D("100"), D("0"))
    await PH.pastikan_cn_muat_faktur(c, "t", F, D("10"), kecuali_cn=UUID(int=9))
    assert c.q[1][1][2] == UUID(int=9)  # NK ini sendiri dikecualikan (posting)
    with pytest.raises(HTTPException):
        await PH.pastikan_cn_muat_faktur(_C(D("1"), D("0")), "t", {**F, "status": "void"}, D("1"))


def _src(mod):
    return Path(mod.__file__).read_text(encoding="utf-8")


def _fn(mod, nama):
    return next(n for n in ast.walk(ast.parse(_src(mod))) if isinstance(n, ast.AsyncFunctionDef) and n.name == nama)


def test_posting_membelah_piutang_dan_saldo_kredit():
    s = " ".join(ast.get_source_segment(_src(CN), _fn(CN, "posting_nota_kredit")).split())
    assert "porsi_ar = min(Decimal(str(total_amount)), max(_sisa_ar, Decimal(\"0\")))" in s
    assert "AccountRole.CUSTOMER_DEPOSIT_LIABILITY" in s and "INSERT INTO customer_deposits" in s
    assert "UPDATE credit_notes SET created_deposit_id" in s
    assert "kecuali_cn=credit_note_id" in s


def test_sisa_uang_muka_mengenal_jurnal_nk():
    s = " ".join(ast.get_source_segment(_src(CD), _fn(CD, "compute_deposit_remaining_many")).split())
    assert "SELECT journal_id FROM credit_notes WHERE tenant_id = $1 AND created_deposit_id = d.id" in s


def test_void_nk_menjaga_saldo_kredit():
    fn = _fn(CN, "void_nota_kredit_core")  # isi void dipindah ke inti (U5-D); handler = idempotensi + inti
    jaga = [n for n in ast.walk(fn) if isinstance(n, ast.If)
            and ast.unparse(n.test) == "_sisa_dep < Decimal(str(dep_nk['amount']))"
            and any(isinstance(b, ast.Raise) for b in n.body)]
    assert len(jaga) == 1, "void NK wajib MENOLAK (raise) bila saldo kredit sudah dipakai (sisa < nominal)"
    s = " ".join(ast.get_source_segment(_src(CN), fn).split())
    assert "compute_deposit_remaining" in s and "sudah dipakai" in s
    assert "UPDATE customer_deposits SET status = 'void'" in s
