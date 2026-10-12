"""NK BERTAUT faktur = sisa kredit 0; refund/apply DARI NK itu 409 berkode (12 Okt 2026, putusan MASTER, pola NetSuite:
kredit yang sudah diterapkan ke invoice = applied; kelebihan = Customer Deposit KRD). Latar: kasus pemilik grapgrap
031-09-26 -- NK penuh atas INV lunas -> seluruhnya ke KRD; refund NK (Dr Piutang / Cr Bank) dulu tetap terbuka ->
piutang hantu + dana bisa keluar ganda. NK bebas (tanpa faktur): tak berubah."""
import asyncio
import ast
import os
import uuid
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest
from fastapi import HTTPException

from app.routers import credit_notes as CN

SRC = Path(CN.__file__).read_text()


def _fungsi(nama):
    for n in ast.walk(ast.parse(SRC)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nama:
            return ast.get_source_segment(SRC, n)
    raise AssertionError(nama)


FAKTUR = uuid.uuid4()


@pytest.mark.parametrize("status,faktur,harap", [
    ("posted", None, Decimal("700")),        # NK bebas: total - applied - refunded (tak berubah)
    ("partial", None, Decimal("700")),
    ("posted", FAKTUR, Decimal("0")),        # NK bertaut terbit: kredit sudah dipakai faktur / pindah ke KRD
    ("applied", FAKTUR, Decimal("0")),
    ("draft", FAKTUR, Decimal("700")),       # draf belum terbit: rumus biasa (belum memotong apa pun)
    ("void", None, Decimal("0")),
    ("void", FAKTUR, Decimal("0")),
])
def test_sisa_kredit_nk_satu_rumus(status, faktur, harap):
    assert CN.sisa_kredit_nk(status, Decimal("1000"), Decimal("200"), Decimal("100"), faktur) == harap


class Konek:
    def __init__(self, krd="KRD-2610-0007"):
        self.krd, self.log = krd, []

    async def fetchval(self, sql, *a):
        self.log.append((sql, a))
        assert "FROM customer_deposits" in sql and "tenant_id = $2" in sql
        return self.krd


def _nk(**kw):
    d = {"status": "posted", "original_invoice_id": FAKTUR, "original_invoice_number": "INV-2609-0023", "created_deposit_id": None}
    d.update(kw)
    return d


def test_tolak_bertaut_ber_krd_409_kode_dan_nomor_krd():
    k = Konek()
    with pytest.raises(HTTPException) as e:
        asyncio.run(CN.tolak_nk_bertaut(k, "t", _nk(created_deposit_id=uuid.uuid4())))
    assert e.value.status_code == 409 and e.value.detail["code"] == "CN_KREDIT_DI_UANG_MUKA"
    assert e.value.detail["message"] == "Kredit nota ini sudah menjadi saldo kredit KRD-2610-0007; kembalikan lewat uang muka itu."
    assert k.log[0][1][1] == "t"   # tenant eksplisit


def test_tolak_bertaut_tanpa_krd_409_sudah_memotong_faktur():
    with pytest.raises(HTTPException) as e:
        asyncio.run(CN.tolak_nk_bertaut(Konek(), "t", _nk()))
    assert e.value.status_code == 409 and e.value.detail["code"] == "CN_SUDAH_MEMOTONG_FAKTUR"
    assert "INV-2609-0023" in e.value.detail["message"]


def test_nk_bebas_lolos_pagar():
    asyncio.run(CN.tolak_nk_bertaut(Konek(), "t", _nk(original_invoice_id=None, original_invoice_number=None)))


def test_refund_dan_apply_memakai_pagar_sebelum_menghitung_sisa():
    r = _fungsi("refund_nota_kredit_core")
    assert r.index("tolak_nk_bertaut(") < r.index("remaining = sisa_kredit_nk(") < r.index("INSERT INTO journal_entries")
    a = _fungsi("apply_nota_kredit_core")
    assert "await tolak_nk_bertaut(conn, ctx[\"tenant_id\"], cn)" in a
    assert a.index("tolak_nk_bertaut(") < a.index("INSERT INTO")


def test_semua_pembaca_sisa_memakai_rumus_yang_sama():
    # daftar, detail, keadaan pratinjau, refund -> sisa_kredit_nk; tak ada lagi rumus lama total - applied - refunded
    assert SRC.count("sisa_kredit_nk(") >= 6   # definisi + daftar, detail, keadaan, refund inti, refund pratinjau
    assert 'sisa = cn["total_amount"] - (cn["amount_applied"] or 0)' not in SRC
    rp = _fungsi("_rencana_nk_aksi")
    assert rp.count("await tolak_nk_bertaut(conn, tid, cn)") == 2              # pratinjau apply + refund memblok
    assert '- (row["amount_refunded"] or 0)' not in SRC and '- (cn["amount_refunded"] or 0)' not in SRC
    assert "AND cn.original_invoice_id IS NULL), 0) as available_balance" in SRC   # ringkasan: NK bebas saja


def test_draf_bertaut_tetap_pesan_lama_400():
    with pytest.raises(HTTPException) as e:
        asyncio.run(CN.tolak_nk_bertaut(Konek(), "t", _nk(status="draft")))
    assert e.value.status_code == 400 and e.value.detail == "Nota kredit ini sudah terkait ke faktur."
