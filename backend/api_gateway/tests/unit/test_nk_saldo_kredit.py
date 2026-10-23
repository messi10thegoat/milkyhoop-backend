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
    import inspect
    from app.services import nota_kredit_rencana as NKR
    r = " ".join(inspect.getsource(NKR.rencana_posting_nk).split())  # NK-RENCANA-B: pecahan dihitung penentu, dipakai penulis
    assert "porsi_ar = min(Decimal(str(total)), max(sisa_ar, Decimal(\"0\")))" in r
    assert "AccountRole.CUSTOMER_DEPOSIT_LIABILITY" in r and "INSERT INTO customer_deposits" in s
    assert 'porsi_ar, kelebihan = _r["porsi_ar"], _r["kelebihan"]' in s and '_dep_acc = _r["akun"]["krd"]' in s
    assert "UPDATE credit_notes SET created_deposit_id" in s
    assert "kecuali_cn=cn_id" in r


def test_sisa_uang_muka_mengenal_jurnal_nk():
    # PERF-DAFTAR-SO (2): SQL satu-lintasan (CD.SQL_SISA_UANG_MUKA); sumber NK tetap salah satu dari 4 pasangan (jurnal, uang muka)
    s = " ".join(CD.SQL_SISA_UANG_MUKA.split())
    assert "SELECT cn.journal_id, cn.created_deposit_id FROM credit_notes cn WHERE cn.tenant_id = $1 AND cn.created_deposit_id = ANY($2::uuid[])" in s
    assert "SQL_SISA_UANG_MUKA" in ast.get_source_segment(_src(CD), _fn(CD, "compute_deposit_remaining_many"))


def test_void_nk_menjaga_saldo_kredit():
    fn = _fn(CN, "void_nota_kredit_core")  # isi void dipindah ke inti (U5-D); pemeriksaan = penentu NKR (NK-RENCANA-B 23 Okt)
    import inspect
    from app.services import nota_kredit_rencana as NKR
    r = " ".join(inspect.getsource(NKR.rencana_batal_nk).split())
    assert "compute_deposit_remaining(conn, tid, dep[\"id\"]) < Decimal(str(dep[\"amount\"]))" in r and "sudah dipakai" in r
    assert 'bl.galat("CN_CREDIT_USED", 400,' in r
    s = " ".join(ast.get_source_segment(_src(CN), fn).split())
    assert "NKR.rencana_batal_nk(conn, ctx, credit_note_id, reason)" in s
    assert "UPDATE customer_deposits SET status = 'void'" in s
